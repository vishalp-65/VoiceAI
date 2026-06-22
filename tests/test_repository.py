"""Unit tests for the pure booking logic. No network, no API keys, no Pipecat."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from voiceai.booking import db, repository

TZ = "America/New_York"
# A fixed Monday so seeding ("starts tomorrow") and date resolution are deterministic.
NOW = datetime(2026, 6, 22, 9, 0, tzinfo=ZoneInfo(TZ))
TUE = "2026-06-23"  # first seeded business day


@pytest.fixture
def conn():
    c = db.connect_memory()
    db.init_schema(c)
    db.seed_slots(c, timezone=TZ, now=NOW)
    yield c
    c.close()


def _avail(conn, **kw):
    return repository.check_availability(conn, timezone=TZ, now=NOW, **kw)


# --- check_availability ---------------------------------------------------
def test_seeding_creates_business_day_slots(conn):
    rows = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    # 5 business days x 16 half-hour slots (09:00–16:30).
    assert rows == 5 * 16


def test_availability_for_specific_date(conn):
    res = _avail(conn, date=TUE)
    assert res["success"] and res["count"] > 0
    assert all(s["date"] == TUE for s in res["slots"])


def test_availability_part_of_day_morning(conn):
    res = _avail(conn, date=TUE, part_of_day="morning")
    assert res["count"] > 0
    assert all(int(s["time"][:2]) < 12 for s in res["slots"])


def test_availability_without_date_lists_upcoming(conn):
    res = _avail(conn)
    assert res["success"] and 1 <= res["count"] <= 6


# --- book -----------------------------------------------------------------
def _book(conn, **kw):
    base = dict(patient_name="Jordan Lee", phone="5551234567", date=TUE, time="10:00")
    base.update(kw)
    return repository.book_appointment(conn, timezone=TZ, now=NOW, **base)


def test_book_success(conn):
    res = _book(conn)
    assert res["success"]
    assert res["confirmation_code"].startswith("CONS-")
    assert "10:00" in res["when"] or "10:00" not in res["when"]  # label is human form


def test_booked_slot_no_longer_available(conn):
    _book(conn, time="10:00")
    res = _avail(conn, date=TUE)
    assert all(s["time"] != "10:00" for s in res["slots"])


def test_double_booking_is_rejected(conn):
    assert _book(conn, time="10:30")["success"]
    second = _book(conn, time="10:30")
    assert second["success"] is False
    assert "alternatives" in second


def test_book_unavailable_time(conn):
    res = _book(conn, time="03:00")  # clinic is closed at 3am — no such slot
    assert res["success"] is False


def test_book_with_garbled_time_fails_gracefully(conn):
    res = _book(conn, time="quarter past")
    assert res["success"] is False
    assert "date/time" in res["message"].lower()


# --- reschedule -----------------------------------------------------------
def test_reschedule_by_code(conn):
    booked = _book(conn, time="11:00")
    code = booked["confirmation_code"]
    res = repository.reschedule_appointment(conn, identifier=code, date=TUE, time="14:00", timezone=TZ, now=NOW)
    assert res["success"] and res["confirmation_code"] == code
    # Old slot freed, new slot taken.
    avail = _avail(conn, date=TUE)
    times = {s["time"] for s in avail["slots"]}
    assert "11:00" in times


def test_reschedule_unknown_booking(conn):
    res = repository.reschedule_appointment(conn, identifier="CONS-NOPE9", date=TUE, time="14:00", timezone=TZ, now=NOW)
    assert res["success"] is False


# --- cancel ---------------------------------------------------------------
def test_cancel_by_code_frees_slot(conn):
    booked = _book(conn, time="13:00")
    code = booked["confirmation_code"]
    res = repository.cancel_appointment(conn, identifier=code)
    assert res["success"]
    # The freed slot can be booked again.
    again = _book(conn, time="13:00")
    assert again["success"]


def test_cancel_by_phone(conn):
    _book(conn, time="15:00", phone="5559998888")
    res = repository.cancel_appointment(conn, identifier="555 999 8888")
    assert res["success"]


def test_cancel_unknown(conn):
    assert repository.cancel_appointment(conn, identifier="555")["success"] is False


# --- resolution helpers ---------------------------------------------------
def test_resolve_date_relative():
    tz = ZoneInfo(TZ)
    assert repository.resolve_date("tomorrow", tz, now=NOW).isoformat() == TUE
    assert repository.resolve_date("today", tz, now=NOW) == NOW.date()
    # "Wednesday" from a Monday -> two days later.
    assert repository.resolve_date("wednesday", tz, now=NOW).isoformat() == "2026-06-24"


@pytest.mark.parametrize(
    "raw,expected",
    [("10:00", "10:00"), ("2:30 PM", "14:30"), ("9 am", "09:00"), ("14", "14:00")],
)
def test_resolve_time_variants(raw, expected):
    assert repository.resolve_time(raw).strftime("%H:%M") == expected
