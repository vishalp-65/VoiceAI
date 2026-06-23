"""FastAPI application: static demo UI + WebRTC signaling for the voice agent.

Endpoints:
  GET  /            -> custom browser-mic demo (transcript + latency HUD)
  GET  /health      -> liveness + which API keys are configured
  POST /api/offer   -> WebRTC offer/answer signaling for SmallWebRTCTransport
                       (also handles renegotiation when a pc_id is supplied)
  /static/*         -> JS/CSS assets
  /client           -> Pipecat prebuilt UI (zero-config fallback, if installed)

Each new peer connection spins up an independent Pipecat pipeline via `run_bot`.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .booking import db
from .bot import run_bot
from .settings import get_settings

WEB_DIR = Path(__file__).resolve().parent / "web"

# STUN keeps NAT traversal working for non-localhost demos; harmless locally.
ICE_SERVERS = ["stun:stun.l.google.com:19302"]

# Active peer connections, keyed by their WebRTC pc_id (for renegotiation/cleanup).
_connections: dict[str, object] = {}


def _mount_prebuilt_ui(app: FastAPI) -> None:
    """Mount the optional Pipecat prebuilt client at /client, if the package is present."""
    candidates = [
        ("pipecat_ai_small_webrtc_prebuilt.frontend", "SmallWebRTCPrebuiltUI"),
        ("pipecat_ai_prebuilt.frontend", "PipecatPrebuiltUI"),
    ]
    for module_name, attr in candidates:
        try:
            module = importlib.import_module(module_name)
            app.mount("/client", getattr(module, attr))
            logger.info("Mounted prebuilt fallback UI at /client ({}.{})", module_name, attr)
            return
        except Exception as exc:  # noqa: BLE001 - any import/mount failure is non-fatal
            logger.debug("Prebuilt UI {} unavailable: {}", module_name, exc)
    logger.info("Prebuilt UI not installed; the custom UI at / is the demo surface.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    inserted = db.ensure_seeded(settings.db_path, timezone=settings.clinic_timezone)
    logger.info(
        "Store ready at {} ({}).",
        settings.db_path,
        f"seeded {inserted} slots" if inserted else "already populated",
    )
    yield
    # Best-effort cleanup of any lingering connections on shutdown.
    for conn in list(_connections.values()):
        try:
            await conn.disconnect()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Alris Voice Booking Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
_mount_prebuilt_ui(app)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health() -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "models": {"llm": s.llm_model, "stt": s.stt_model, "tts": s.tts_model},
            "keys_configured": {
                "deepgram": bool(s.deepgram_api_key),
                "anthropic": bool(s.anthropic_api_key),
                "cartesia": bool(s.cartesia_api_key),
            },
            "active_calls": len(_connections),
        }
    )


@app.post("/api/offer")
async def offer(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """WebRTC signaling. Creates a fresh pipeline for new peers; renegotiates existing ones."""
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    from .pipeline.services import create_vad_analyzer

    body = await request.json()
    pc_id = body.get("pc_id")

    if pc_id and pc_id in _connections:
        conn = _connections[pc_id]
        await conn.renegotiate(
            sdp=body["sdp"], type=body["type"], restart_pc=body.get("restart_pc", False)
        )
        return JSONResponse(conn.get_answer())

    conn = SmallWebRTCConnection(ICE_SERVERS)
    await conn.initialize(sdp=body["sdp"], type=body["type"])

    @conn.event_handler("closed")
    async def _on_closed(closed_conn):
        _connections.pop(closed_conn.pc_id, None)
        logger.info("Connection {} closed ({} active)", closed_conn.pc_id, len(_connections))

    transport = SmallWebRTCTransport(
        webrtc_connection=conn,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=create_vad_analyzer(),
        ),
    )
    background_tasks.add_task(run_bot, transport)

    answer = conn.get_answer()
    _connections[answer["pc_id"]] = conn
    logger.info("New call {} ({} active)", answer["pc_id"], len(_connections))
    return JSONResponse(answer)


@app.patch("/api/offer")
async def offer_ice(request: Request) -> JSONResponse:
    """Handle ICE candidate trickle from the SmallWebRTC client."""
    body = await request.json()
    pc_id = body.get("pc_id")

    if not pc_id or pc_id not in _connections:
        return JSONResponse({"error": "unknown peer"}, status_code=404)

    conn = _connections[pc_id]
    candidate = body.get("candidate")
    if candidate:
        await conn.add_ice_candidate(candidate)

    return JSONResponse({"ok": True})
