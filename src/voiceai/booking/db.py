"""SQLite connection management, schema, and availability seeding.

The store is intentionally tiny: two tables (`slots`, `appointments`). All
business logic lives in `repository.py` and operates on a `sqlite3.Connection`
that is passed in, which makes it trivial to unit-test against an in-memory DB.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import SLOT_MINUTES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    id        TEXT PRIMARY KEY,
    start_iso TEXT NOT NULL UNIQUE,
    is_booked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS appointments (
    confirmation_code TEXT PRIMARY KEY,
    patient_name      TEXT NOT NULL,
    phone             TEXT NOT NULL,
    slot_id           TEXT NOT NULL REFERENCES slots(id),
    reason            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'booked',
    created_iso       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(phone);
"""

# Clinic working hours used when seeding availability.
_OPEN_HOUR = 9
_CLOSE_HOUR = 17  # last slot starts at 16:30
_BUSINESS_DAYS = 5  # Mon–Fri only


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with sane defaults (row dicts, FK enforcement)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def connect_memory() -> sqlite3.Connection:
    """In-memory connection for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def seed_slots(
    conn: sqlite3.Connection,
    *,
    timezone: str = "America/New_York",
    days: int = _BUSINESS_DAYS,
    now: datetime | None = None,
) -> int:
    """Generate 30-minute consultation slots over the next `days` business days.

    Starts from *tomorrow* so the demo always has fresh, future availability.
    Returns the number of slots inserted. Idempotent on (start_iso).
    """
    tz = ZoneInfo(timezone)
    today = (now or datetime.now(tz)).date()

    inserted = 0
    day_offset = 1  # start tomorrow
    days_added = 0
    while days_added < days:
        day = today + timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:  # skip Sat/Sun
            continue
        days_added += 1

        cursor = datetime.combine(day, time(_OPEN_HOUR, 0), tzinfo=tz)
        close = datetime.combine(day, time(_CLOSE_HOUR, 0), tzinfo=tz)
        while cursor < close:
            slot_id = f"S{cursor.strftime('%Y%m%d%H%M')}"
            try:
                conn.execute(
                    "INSERT INTO slots (id, start_iso, is_booked) VALUES (?, ?, 0)",
                    (slot_id, cursor.isoformat()),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # already seeded for this time
            cursor += timedelta(minutes=SLOT_MINUTES)

    conn.commit()
    return inserted


def reset_and_seed(db_path: str | Path, *, timezone: str = "America/New_York") -> int:
    """Drop everything and recreate a fresh, fully-available calendar."""
    conn = connect(db_path)
    try:
        conn.executescript("DROP TABLE IF EXISTS appointments; DROP TABLE IF EXISTS slots;")
        init_schema(conn)
        return seed_slots(conn, timezone=timezone)
    finally:
        conn.close()


def ensure_seeded(db_path: str | Path, *, timezone: str = "America/New_York") -> int:
    """Initialise the schema and seed slots only if the calendar is empty.

    Called on server startup so a fresh checkout "just works", while preserving
    bookings across reloads (unlike `reset_and_seed`, which the seed script uses).
    Returns the number of slots inserted (0 if it was already populated).
    """
    conn = connect(db_path)
    try:
        init_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM slots WHERE is_booked = 0").fetchone()[0]
        if count > 0:
            return 0
        return seed_slots(conn, timezone=timezone)
    finally:
        conn.close()
