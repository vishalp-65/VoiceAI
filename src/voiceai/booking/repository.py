"""Pure booking logic over a SQLite connection.

Every function takes an explicit `sqlite3.Connection` and returns plain,
JSON-serialisable dicts (so tool handlers can hand them straight back to the
LLM). No global state, no network — which is exactly why this module is the
one with thorough unit tests.

Date/time inputs are tolerant: the agent is prompted to pass an ISO date
(`YYYY-MM-DD`) and 24-hour `HH:MM`, but we also accept "today"/"tomorrow",
weekday names, and 12-hour times as a safety net against ASR/LLM variation.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import format_slot_label

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I/L
_MAX_OPTIONS = 6
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# --------------------------------------------------------------------------- #
# Input resolution helpers
# --------------------------------------------------------------------------- #
def _now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def resolve_date(date_str: str | None, tz: ZoneInfo, *, now: datetime | None = None) -> date_cls | None:
    """Best-effort parse of a spoken/typed date into a calendar date."""
    if not date_str:
        return None
    today = (now or _now(tz)).date()
    s = date_str.strip().lower()

    if s in ("today",):
        return today
    if s in ("tomorrow", "tmrw"):
        return today + timedelta(days=1)
    if s in _WEEKDAYS:
        delta = (_WEEKDAYS[s] - today.weekday()) % 7
        delta = delta or 7  # always the *next* occurrence, never today
        return today + timedelta(days=delta)

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d", "%B %d", "%d %B", "%b %d", "%d %b"):
        try:
            parsed = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        # Formats without a year default to 1900 — roll forward to a sensible year.
        if parsed.year == 1900:
            parsed = parsed.replace(year=today.year)
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed
    return None


def resolve_time(time_str: str | None) -> time | None:
    """Best-effort parse of a spoken/typed time into a wall-clock time."""
    if not time_str:
        return None
    s = time_str.strip().lower().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    # Bare hour like "10" or "14".
    if s.isdigit():
        h = int(s)
        if 0 <= h <= 23:
            return time(h, 0)
    return None


def _part_of_day_filter(start: datetime, part: str | None) -> bool:
    if not part:
        return True
    p = part.strip().lower()
    if p in ("morning", "am"):
        return start.hour < 12
    if p in ("afternoon", "pm"):
        return 12 <= start.hour < 17
    if p in ("evening", "night"):
        return start.hour >= 17
    return True


def _slot_option(row: sqlite3.Row) -> dict:
    start = datetime.fromisoformat(row["start_iso"])
    return {
        "slot_id": row["id"],
        "label": format_slot_label(start),
        "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
    }


def _generate_code() -> str:
    return "CONS-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(5))


def _find_open_slot_row(conn: sqlite3.Connection, target: datetime) -> sqlite3.Row | None:
    """Find an unbooked slot starting at `target` (matched on local Y-M-D + HH:MM)."""
    day_prefix = target.date().isoformat()
    hhmm = target.strftime("%H:%M")
    rows = conn.execute(
        "SELECT * FROM slots WHERE is_booked = 0 AND start_iso LIKE ? ORDER BY start_iso",
        (f"{day_prefix}%",),
    ).fetchall()
    for row in rows:
        if datetime.fromisoformat(row["start_iso"]).strftime("%H:%M") == hhmm:
            return row
    return None


def _find_appointment_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    """Locate a booked appointment by confirmation code or phone number."""
    ident = (identifier or "").strip()
    if not ident:
        return None
    # Try confirmation code (case-insensitive).
    row = conn.execute(
        "SELECT * FROM appointments WHERE UPPER(confirmation_code) = ? AND status = 'booked'",
        (ident.upper(),),
    ).fetchone()
    if row:
        return row
    # Fall back to phone (compare digits only).
    digits = "".join(c for c in ident if c.isdigit())
    if len(digits) >= 7:
        row = conn.execute(
            """
            SELECT * FROM appointments
            WHERE status = 'booked'
              AND REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ?
            ORDER BY created_iso DESC LIMIT 1
            """,
            (f"%{digits}%",),
        ).fetchone()
        return row
    return None


# --------------------------------------------------------------------------- #
# Public repository API (called by tool handlers)
# --------------------------------------------------------------------------- #
def check_availability(
    conn: sqlite3.Connection,
    *,
    date: str | None = None,
    part_of_day: str | None = None,
    timezone: str = "America/New_York",
    now: datetime | None = None,
) -> dict:
    """List open consultation slots, optionally narrowed to a date / part of day."""
    tz = ZoneInfo(timezone)
    target_date = resolve_date(date, tz, now=now)

    if target_date is not None:
        rows = conn.execute(
            "SELECT * FROM slots WHERE is_booked = 0 AND start_iso LIKE ? ORDER BY start_iso",
            (f"{target_date.isoformat()}%",),
        ).fetchall()
        scope = format_slot_label(datetime.combine(target_date, time(0, 0))).split(" at ")[0]
    else:
        rows = conn.execute(
            "SELECT * FROM slots WHERE is_booked = 0 ORDER BY start_iso"
        ).fetchall()
        scope = "the next few days"

    options = [
        _slot_option(r)
        for r in rows
        if _part_of_day_filter(datetime.fromisoformat(r["start_iso"]), part_of_day)
    ][:_MAX_OPTIONS]

    if not options:
        return {
            "success": True,
            "count": 0,
            "scope": scope,
            "slots": [],
            "message": f"No open consultation slots for {scope}. Offer another day.",
        }
    return {
        "success": True,
        "count": len(options),
        "scope": scope,
        "slots": options,
        "message": f"{len(options)} open consultation slot(s) for {scope}.",
    }


def book_appointment(
    conn: sqlite3.Connection,
    *,
    patient_name: str,
    phone: str,
    date: str,
    time: str,  # noqa: A002 - matches the tool's argument name
    reason: str = "",
    timezone: str = "America/New_York",
    now: datetime | None = None,
) -> dict:
    """Book the consultation slot at `date`+`time` for the caller."""
    tz = ZoneInfo(timezone)
    target_date = resolve_date(date, tz, now=now)
    target_time = resolve_time(time)
    if target_date is None or target_time is None:
        return {
            "success": False,
            "message": "Could not understand the requested date/time. Ask the caller to repeat it.",
        }

    target = datetime.combine(target_date, target_time, tzinfo=tz)
    slot = _find_open_slot_row(conn, target)
    if slot is None:
        nearby = check_availability(conn, date=target_date.isoformat(), timezone=timezone, now=now)
        return {
            "success": False,
            "message": f"{format_slot_label(target)} is not available.",
            "alternatives": nearby["slots"],
        }

    code = _generate_code()
    conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (slot["id"],))
    conn.execute(
        """INSERT INTO appointments
           (confirmation_code, patient_name, phone, slot_id, reason, status, created_iso)
           VALUES (?, ?, ?, ?, ?, 'booked', ?)""",
        (code, patient_name.strip(), phone.strip(), slot["id"], (reason or "").strip(),
         (now or _now(tz)).isoformat()),
    )
    conn.commit()
    return {
        "success": True,
        "confirmation_code": code,
        "patient_name": patient_name.strip(),
        "when": format_slot_label(target),
        "message": f"Booked {format_slot_label(target)}. Confirmation code {code}.",
    }


def reschedule_appointment(
    conn: sqlite3.Connection,
    *,
    identifier: str,
    date: str,
    time: str,  # noqa: A002
    timezone: str = "America/New_York",
    now: datetime | None = None,
) -> dict:
    """Move an existing booking to a new open slot."""
    tz = ZoneInfo(timezone)
    appt = _find_appointment_row(conn, identifier)
    if appt is None:
        return {
            "success": False,
            "message": "No matching booking found. Ask for the confirmation code or phone number.",
        }

    target_date = resolve_date(date, tz, now=now)
    target_time = resolve_time(time)
    if target_date is None or target_time is None:
        return {"success": False, "message": "Could not understand the new date/time."}

    target = datetime.combine(target_date, target_time, tzinfo=tz)
    new_slot = _find_open_slot_row(conn, target)
    if new_slot is None:
        nearby = check_availability(conn, date=target_date.isoformat(), timezone=timezone, now=now)
        return {
            "success": False,
            "message": f"{format_slot_label(target)} is not available.",
            "alternatives": nearby["slots"],
        }

    old_slot = conn.execute("SELECT * FROM slots WHERE id = ?", (appt["slot_id"],)).fetchone()
    conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (appt["slot_id"],))
    conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (new_slot["id"],))
    conn.execute(
        "UPDATE appointments SET slot_id = ? WHERE confirmation_code = ?",
        (new_slot["id"], appt["confirmation_code"]),
    )
    conn.commit()
    old_label = format_slot_label(datetime.fromisoformat(old_slot["start_iso"])) if old_slot else "the previous time"
    return {
        "success": True,
        "confirmation_code": appt["confirmation_code"],
        "from": old_label,
        "when": format_slot_label(target),
        "message": f"Moved {appt['confirmation_code']} from {old_label} to {format_slot_label(target)}.",
    }


def cancel_appointment(
    conn: sqlite3.Connection,
    *,
    identifier: str,
) -> dict:
    """Cancel a booking and free its slot."""
    appt = _find_appointment_row(conn, identifier)
    if appt is None:
        return {
            "success": False,
            "message": "No matching booking found. Ask for the confirmation code or phone number.",
        }
    slot = conn.execute("SELECT * FROM slots WHERE id = ?", (appt["slot_id"],)).fetchone()
    conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (appt["slot_id"],))
    conn.execute(
        "UPDATE appointments SET status = 'cancelled' WHERE confirmation_code = ?",
        (appt["confirmation_code"],),
    )
    conn.commit()
    when = format_slot_label(datetime.fromisoformat(slot["start_iso"])) if slot else "the appointment"
    return {
        "success": True,
        "confirmation_code": appt["confirmation_code"],
        "cancelled": when,
        "message": f"Cancelled {appt['confirmation_code']} ({when}).",
    }
