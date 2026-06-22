"""Build and run the Pipecat voice pipeline for a single connected caller.

Pipeline order (audio flows top to bottom):

    transport.input()              WebRTC audio in + Silero VAD (turn / barge-in)
      -> rtvi                      emits transcript / speaking events to the client
      -> silence                   silence detection & escalation
      -> stt                       Deepgram streaming transcription
      -> context_aggregator.user() accumulates the user turn into the LLM context
      -> llm                       Claude + the four booking tools
      -> tts                       Cartesia Sonic streaming speech
      -> transport.output()        WebRTC audio out
      -> context_aggregator.assistant()  records the bot turn back into context

Targets Pipecat 1.4: the universal `LLMContext` / `LLMContextAggregatorPair`, and
interruptions (barge-in) are enabled by default by the turn controller.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frameworks.rtvi import RTVIProcessor

from .pipeline.idle import build_user_idle_processor
from .pipeline.observers import build_observers
from .pipeline.prompts import build_system_prompt
from .pipeline.services import create_llm, create_stt, create_tts
from .settings import get_settings
from .tools import TOOLS_SCHEMA, register_all


def build_worker(transport) -> PipelineWorker:
    """Assemble the full pipeline for one connection. Pure construction (no run),
    which keeps it unit-/smoke-testable without a live WebRTC session."""
    settings = get_settings()
    settings.require_provider_keys()  # fail fast with a clear message if a key is missing

    # --- Services ---------------------------------------------------------
    stt = create_stt(settings)
    llm = create_llm(settings)
    tts = create_tts(settings)
    register_all(llm)  # wire the four booking tools to their handlers

    # --- Context ----------------------------------------------------------
    messages = [{"role": "system", "content": build_system_prompt(timezone=settings.clinic_timezone)}]
    context = LLMContext(messages=messages, tools=TOOLS_SCHEMA)
    context_aggregator = LLMContextAggregatorPair(context)

    # --- Processors -------------------------------------------------------
    rtvi = RTVIProcessor()  # bridges transcript/speaking events to the browser client
    silence = build_user_idle_processor(settings)

    pipeline = Pipeline(
        [
            transport.input(),
            rtvi,
            silence,
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    # enable_rtvi=False: we add our own RTVIProcessor/observer explicitly above so we
    # can hook on_client_ready; this stops the worker adding duplicate defaults.
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,        # logs per-service TTFB for the latency write-up
            enable_usage_metrics=True,
        ),
        observers=build_observers(rtvi),
        enable_rtvi=False,
    )

    # Greet the caller as soon as the browser client signals it's ready.
    @rtvi.event_handler("on_client_ready")
    async def _on_client_ready(rtvi_proc):
        await rtvi_proc.set_bot_ready()
        # Kick the LLM once so the agent speaks first (system prompt tells it to greet).
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(_transport, _client):
        logger.info("Client disconnected; ending pipeline")
        await worker.cancel()

    return worker


async def run_bot(transport) -> None:
    """Run the agent for the lifetime of one WebRTC connection."""
    settings = get_settings()
    worker = build_worker(transport)
    runner = PipelineRunner(handle_sigint=False)
    logger.info(
        "Pipeline starting (llm={}, stt={}, tts={})",
        settings.llm_model,
        settings.stt_model,
        settings.tts_model,
    )
    await runner.run(worker)
    logger.info("Pipeline finished")
