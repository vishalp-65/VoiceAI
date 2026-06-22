"""Async tool handlers that bridge the LLM's function calls to the repository.

Each handler:
  1. reads validated arguments from `params.arguments`,
  2. runs the (synchronous, sub-millisecond) SQLite work off the event loop via
     `asyncio.to_thread` so the audio pipeline never blocks, and
  3. returns the JSON result through `params.result_callback`.

The handlers are deliberately thin — all logic and validation live in
`booking.repository`, which is unit-tested in isolation.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from ..booking import db, repository
from ..settings import get_settings


async def _run(fn: Callable, /, **kwargs) -> dict:
    """Open a short-lived connection, run a repository function in a thread."""
    settings = get_settings()

    def work() -> dict:
        conn: sqlite3.Connection = db.connect(settings.db_path)
        try:
            db.init_schema(conn)  # cheap & idempotent; safe if seeding hasn't run
            return fn(conn, **kwargs)
        finally:
            conn.close()

    return await asyncio.to_thread(work)


async def handle_check_availability(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    settings = get_settings()
    result = await _run(
        repository.check_availability,
        date=args.get("date"),
        part_of_day=args.get("part_of_day"),
        timezone=settings.clinic_timezone,
    )
    logger.info("check_availability({}) -> {} slot(s)", args, result.get("count"))
    await params.result_callback(result)


async def handle_book_appointment(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    settings = get_settings()
    result = await _run(
        repository.book_appointment,
        patient_name=args.get("patient_name", ""),
        phone=args.get("phone", ""),
        date=args.get("date", ""),
        time=args.get("time", ""),
        reason=args.get("reason", ""),
        timezone=settings.clinic_timezone,
    )
    logger.info("book_appointment -> success={}", result.get("success"))
    await params.result_callback(result)


async def handle_reschedule_appointment(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    settings = get_settings()
    result = await _run(
        repository.reschedule_appointment,
        identifier=args.get("identifier", ""),
        date=args.get("date", ""),
        time=args.get("time", ""),
        timezone=settings.clinic_timezone,
    )
    logger.info("reschedule_appointment -> success={}", result.get("success"))
    await params.result_callback(result)


async def handle_cancel_appointment(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    result = await _run(
        repository.cancel_appointment,
        identifier=args.get("identifier", ""),
    )
    logger.info("cancel_appointment -> success={}", result.get("success"))
    await params.result_callback(result)


# Maps each tool name to its handler.
_HANDLERS = {
    "check_availability": handle_check_availability,
    "book_appointment": handle_book_appointment,
    "reschedule_appointment": handle_reschedule_appointment,
    "cancel_appointment": handle_cancel_appointment,
}


def register_all(llm) -> None:
    """Register every tool handler with the given Pipecat LLM service."""
    for name, handler in _HANDLERS.items():
        llm.register_function(name, handler)
