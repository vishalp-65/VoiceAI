"""Tests for the async tool handlers (the bridge from LLM function calls to the store).

These exercise the full handler path with a real (temporary) SQLite file, using a
minimal stand-in for Pipecat's FunctionCallParams.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from voiceai.booking import db
from voiceai.settings import get_settings


class FakeParams:
    """Mimics the bits of FunctionCallParams the handlers actually use."""

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: dict | None = None
        self.llm = SimpleNamespace()

    async def result_callback(self, result: dict) -> None:
        self.result = result


@pytest.fixture
def handlers(tmp_path, monkeypatch):
    """Point settings at a temp DB, seed it, and import the handlers freshly."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("CARTESIA_API_KEY", "x")
    get_settings.cache_clear()
    settings = get_settings()
    db.reset_and_seed(settings.db_path, timezone=settings.clinic_timezone)

    from voiceai.tools import handlers as h

    yield h
    get_settings.cache_clear()


async def _next_slot(handlers):
    """Grab the first real open slot so booking tests are date-agnostic."""
    p = FakeParams({})
    await handlers.handle_check_availability(p)
    assert p.result["count"] > 0
    return p.result["slots"][0]


@pytest.mark.asyncio
async def test_check_availability_handler(handlers):
    p = FakeParams({"part_of_day": "morning"})
    await handlers.handle_check_availability(p)
    assert p.result["success"] is True


@pytest.mark.asyncio
async def test_book_then_cancel_roundtrip(handlers):
    slot = await _next_slot(handlers)

    book = FakeParams(
        {"patient_name": "Sam Rivera", "phone": "5551112222", "date": slot["date"], "time": slot["time"]}
    )
    await handlers.handle_book_appointment(book)
    assert book.result["success"] is True
    code = book.result["confirmation_code"]

    cancel = FakeParams({"identifier": code})
    await handlers.handle_cancel_appointment(cancel)
    assert cancel.result["success"] is True


@pytest.mark.asyncio
async def test_reschedule_handler(handlers):
    first = await _next_slot(handlers)
    book = FakeParams(
        {"patient_name": "Pat Doe", "phone": "5553334444", "date": first["date"], "time": first["time"]}
    )
    await handlers.handle_book_appointment(book)
    code = book.result["confirmation_code"]

    # Find another open slot to move to.
    avail = FakeParams({"date": first["date"]})
    await handlers.handle_check_availability(avail)
    target = next(s for s in avail.result["slots"] if s["time"] != first["time"])

    resched = FakeParams({"identifier": code, "date": target["date"], "time": target["time"]})
    await handlers.handle_reschedule_appointment(resched)
    assert resched.result["success"] is True
    assert resched.result["confirmation_code"] == code
