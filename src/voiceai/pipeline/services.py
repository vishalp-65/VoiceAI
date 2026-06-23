"""Factories for the three streaming services + the local VAD analyzer.

Each factory reads from `Settings` so models/voices are swappable via `.env`
without touching pipeline code. Everything here is chosen for low latency:
streaming STT with interim results, a fast Claude model, and Cartesia Sonic.
"""

from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.llm_service import LLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

from ..settings import Settings


def create_vad_analyzer() -> SileroVADAnalyzer:
    """Silero VAD for endpointing + barge-in.

    `stop_secs` is the silence after speech before we treat the turn as ended —
    the main lever between "snappy" and "cuts the caller off". 0.6s is a good
    middle ground for a booking call where people read out numbers.
    """
    return SileroVADAnalyzer(params=VADParams(stop_secs=0.6))


def create_stt(settings: Settings) -> DeepgramSTTService:
    """Deepgram streaming STT.

    `smart_format` + `numerals` make spoken numbers ("five five five...") come back
    as digits, which is essential for phone numbers and times. `interim_results`
    gives us partial transcripts for a responsive UI and faster endpointing.
    """
    return DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        live_options=LiveOptions(
            model=settings.stt_model,
            language="en-US",
            smart_format=True,
            numerals=True,
            punctuate=True,
            interim_results=True,
        ),
    )


def create_llm(settings: Settings) -> LLMService:
    """Build the tool-calling LLM service for the configured provider.

    Default provider is OpenRouter, talking to Claude Haiku 4.5
    (`anthropic/claude-haiku-4.5`) over its OpenAI-compatible API. Haiku's
    time-to-first-token is ~2-4x lower than Sonnet's, which is what keeps the
    user-stop -> bot-speak gap under ~2s for this constrained booking flow. The
    provider-agnostic `ToolsSchema` and `register_function` wiring work unchanged.

    Set `LLM_PROVIDER=anthropic` to use the Anthropic Messages API directly (or an
    Anthropic-compatible passthrough via `ANTHROPIC_BASE_URL`); that path adds
    explicit prompt caching of the static system+tools prefix. OpenRouter caches
    Anthropic prompts implicitly, so there's no `enable_prompt_caching` knob there.
    """
    if settings.llm_provider == "openrouter":
        return OpenRouterLLMService(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            settings=OpenRouterLLMService.Settings(model=settings.llm_model),
        )
    return _create_anthropic_llm(settings)


def _create_anthropic_llm(settings: Settings) -> AnthropicLLMService:
    """Anthropic Messages API path.

    `enable_prompt_caching=True` makes each turn read the stable system+tools prefix
    (~2K static tokens) from Anthropic's cache instead of reprocessing it — cutting
    input-token processing time and ~90% of input cost. Watch for
    `cache_read_input_tokens > 0` in the logs to confirm it's working.
    """
    kwargs: dict = {
        "api_key": settings.anthropic_api_key,
        "settings": AnthropicLLMService.Settings(
            model=settings.llm_model,
            enable_prompt_caching=True,
        ),
    }
    if settings.anthropic_base_url:
        from anthropic import AsyncAnthropic

        kwargs["client"] = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        )
    return AnthropicLLMService(**kwargs)


def create_tts(settings: Settings) -> CartesiaTTSService:
    """Cartesia Sonic streaming TTS (~40–90 ms time-to-first-audio)."""
    return CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        voice_id=settings.tts_voice_id,
        model=settings.tts_model,
    )
