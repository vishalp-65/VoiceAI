"""Function (tool) schemas exposed to the LLM.

Pipecat's `FunctionSchema` is provider-agnostic; `ToolsSchema` is what we attach
to the LLM context. Keeping the descriptions tight and unambiguous is part of
the latency story — a clear schema means fewer clarifying round-trips.
"""

from __future__ import annotations

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

CHECK_AVAILABILITY = FunctionSchema(
    name="check_availability",
    description=(
        "Look up open 30-minute consultation slots. Call this before offering or "
        "booking any time. Returns slots with a spoken-friendly label."
    ),
    properties={
        "date": {
            "type": "string",
            "description": (
                "Preferred date as YYYY-MM-DD. You may also pass 'today', "
                "'tomorrow', or a weekday like 'Monday'. Omit to list the next "
                "available days."
            ),
        },
        "part_of_day": {
            "type": "string",
            "enum": ["morning", "afternoon", "evening"],
            "description": "Optional preference to narrow the results.",
        },
    },
    required=[],
)

BOOK_APPOINTMENT = FunctionSchema(
    name="book_appointment",
    description=(
        "Book a consultation. Only call this AFTER you have read back and the "
        "caller has confirmed their name, phone number, and the exact slot."
    ),
    properties={
        "patient_name": {"type": "string", "description": "Caller's full name."},
        "phone": {
            "type": "string",
            "description": "Caller's phone number, digits only (e.g. 5551234567).",
        },
        "date": {"type": "string", "description": "Slot date as YYYY-MM-DD."},
        "time": {"type": "string", "description": "Slot start time as 24-hour HH:MM."},
        "reason": {
            "type": "string",
            "description": "Optional short reason for the consultation.",
        },
    },
    required=["patient_name", "phone", "date", "time"],
)

RESCHEDULE_APPOINTMENT = FunctionSchema(
    name="reschedule_appointment",
    description="Move an existing booking to a new open slot.",
    properties={
        "identifier": {
            "type": "string",
            "description": "Confirmation code (e.g. CONS-ABCDE) or the phone number on the booking.",
        },
        "date": {"type": "string", "description": "New slot date as YYYY-MM-DD."},
        "time": {"type": "string", "description": "New slot start time as 24-hour HH:MM."},
    },
    required=["identifier", "date", "time"],
)

CANCEL_APPOINTMENT = FunctionSchema(
    name="cancel_appointment",
    description="Cancel an existing booking and free the slot.",
    properties={
        "identifier": {
            "type": "string",
            "description": "Confirmation code (e.g. CONS-ABCDE) or the phone number on the booking.",
        },
    },
    required=["identifier"],
)

ALL_SCHEMAS = [
    CHECK_AVAILABILITY,
    BOOK_APPOINTMENT,
    RESCHEDULE_APPOINTMENT,
    CANCEL_APPOINTMENT,
]

# Attach this to the LLM context.
TOOLS_SCHEMA = ToolsSchema(standard_tools=ALL_SCHEMAS)
