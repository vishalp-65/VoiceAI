"""Centralised, typed configuration loaded from the environment / `.env`.

Everything tunable about the agent (model ids, latency knobs, paths) lives here
so the rest of the code never reads `os.environ` directly. API keys are *not*
required at import time — the server boots without them and only the live voice
pipeline fails fast if a key is missing, which keeps `/health` and the unit
tests usable in CI.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from this file (src/voiceai/settings.py).
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings, populated from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider API keys (optional at import time, required to take a call) --
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    cartesia_api_key: str = Field(default="", alias="CARTESIA_API_KEY")

    # --- Models ---------------------------------------------------------------
    # Which LLM backend to use: "openrouter" (default) or "anthropic". This picks
    # both the service class and which API key is required at call start.
    llm_provider: str = Field(default="openrouter", alias="LLM_PROVIDER")
    # Model id in the *provider's* naming: OpenRouter slug ("anthropic/claude-haiku-4.5")
    # or Anthropic id ("claude-haiku-4-5-20251001") when LLM_PROVIDER=anthropic.
    llm_model: str = Field(default="anthropic/claude-haiku-4.5", alias="LLM_MODEL")
    stt_model: str = Field(default="nova-3", alias="STT_MODEL")
    tts_model: str = Field(default="sonic-2", alias="TTS_MODEL")
    tts_voice_id: str = Field(
        default="71a7ad14-091c-4e8e-a314-022ece01c121", alias="TTS_VOICE_ID"
    )

    # --- Turn-taking / latency ------------------------------------------------
    idle_timeout_seconds: float = Field(default=6.0, alias="IDLE_TIMEOUT_SECONDS")
    allow_interruptions: bool = Field(default=True, alias="ALLOW_INTERRUPTIONS")

    # --- WebRTC ICE -----------------------------------------------------------
    # STUN is enough on localhost/LAN. On a PaaS like Render (no inbound UDP to
    # arbitrary ports), a TURN relay is REQUIRED or media negotiation times out.
    stun_url: str = Field(
        default="stun:stun.l.google.com:19302", alias="STUN_URL"
    )
    # Comma-separated TURN URLs, e.g. "turn:turn.example.com:3478,turns:turn.example.com:5349".
    turn_urls: str = Field(default="", alias="TURN_URLS")
    turn_username: str = Field(default="", alias="TURN_USERNAME")
    turn_credential: str = Field(default="", alias="TURN_CREDENTIAL")

    def ice_servers(self) -> list:
        """Build the ICE server list for SmallWebRTCConnection (STUN + optional TURN)."""
        from aiortc import RTCIceServer

        servers: list = []
        if self.stun_url:
            servers.append(RTCIceServer(urls=self.stun_url))
        turn = [u.strip() for u in self.turn_urls.split(",") if u.strip()]
        if turn:
            servers.append(
                RTCIceServer(
                    urls=turn,
                    username=self.turn_username or None,
                    credential=self.turn_credential or None,
                )
            )
        return servers

    # --- Server / storage -----------------------------------------------------
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=7860, alias="PORT")
    database_path: str = Field(default="data/voiceai.db", alias="DATABASE_PATH")
    clinic_timezone: str = Field(default="America/New_York", alias="CLINIC_TIMEZONE")

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite store, resolved against the repo root."""
        p = Path(self.database_path)
        return p if p.is_absolute() else ROOT_DIR / p

    def require_provider_keys(self) -> None:
        """Raise a clear error if any provider key is missing (called at call start).

        The required LLM key depends on `llm_provider` — OpenRouter and Anthropic use
        different keys, so we only enforce the one actually in use.
        """
        checks = [
            ("DEEPGRAM_API_KEY", self.deepgram_api_key),
            ("CARTESIA_API_KEY", self.cartesia_api_key),
        ]
        if self.llm_provider == "openrouter":
            checks.append(("OPENROUTER_API_KEY", self.openrouter_api_key))
        else:
            checks.append(("ANTHROPIC_API_KEY", self.anthropic_api_key))
        missing = [name for name, value in checks if not value]
        if missing:
            raise RuntimeError(
                "Missing required API key(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
