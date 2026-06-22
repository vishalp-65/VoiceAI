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
    cartesia_api_key: str = Field(default="", alias="CARTESIA_API_KEY")

    # --- Models ---------------------------------------------------------------
    llm_model: str = Field(default="claude-haiku-4-5-20251001", alias="LLM_MODEL")
    stt_model: str = Field(default="nova-3", alias="STT_MODEL")
    tts_model: str = Field(default="sonic-2", alias="TTS_MODEL")
    tts_voice_id: str = Field(
        default="71a7ad14-091c-4e8e-a314-022ece01c121", alias="TTS_VOICE_ID"
    )

    # --- Turn-taking / latency ------------------------------------------------
    idle_timeout_seconds: float = Field(default=6.0, alias="IDLE_TIMEOUT_SECONDS")
    allow_interruptions: bool = Field(default=True, alias="ALLOW_INTERRUPTIONS")

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
        """Raise a clear error if any provider key is missing (called at call start)."""
        missing = [
            name
            for name, value in (
                ("DEEPGRAM_API_KEY", self.deepgram_api_key),
                ("ANTHROPIC_API_KEY", self.anthropic_api_key),
                ("CARTESIA_API_KEY", self.cartesia_api_key),
            )
            if not value
        ]
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
