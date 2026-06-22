"""Lightweight data structures for the booking domain.

Plain dataclasses (no ORM) keep the store dependency-free and the repository
logic easy to read and test. Times are stored as ISO-8601 strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# We model a single appointment type, as the brief asks: a 30-minute consultation.
APPOINTMENT_TYPE = "consultation"
SLOT_MINUTES = 30


@dataclass(frozen=True)
class Slot:
    """A bookable time slot in the clinic's calendar."""

    id: str
    start_iso: str
    is_booked: bool = False

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.start_iso)

    def label(self) -> str:
        """Human/voice-friendly label, e.g. 'Monday, June 23 at 2:30 PM'."""
        return format_slot_label(self.start)


@dataclass(frozen=True)
class Appointment:
    """A booked consultation."""

    confirmation_code: str
    patient_name: str
    phone: str
    slot_id: str
    reason: str
    status: str  # "booked" | "cancelled"
    created_iso: str


def format_slot_label(dt: datetime) -> str:
    """Render a datetime the way a receptionist would say it aloud."""
    # %-I / %-d are not portable to Windows, so strip leading zeros manually.
    hour12 = dt.strftime("%I").lstrip("0") or "12"
    day = str(dt.day)
    return dt.strftime(f"%A, %B {day} at {hour12}:%M %p")
