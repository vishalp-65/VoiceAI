"""Reset and seed the local SQLite availability store.

Usage:
    python scripts/seed_db.py

Creates a fresh calendar with 30-minute consultation slots over the next five
business days (09:00–17:00). Safe to re-run any time you want a clean demo.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script (no install) by adding src/ to the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voiceai.booking import db  # noqa: E402
from voiceai.settings import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    inserted = db.reset_and_seed(settings.db_path, timezone=settings.clinic_timezone)
    print(f"Seeded {inserted} consultation slots into {settings.db_path}")


if __name__ == "__main__":
    main()
