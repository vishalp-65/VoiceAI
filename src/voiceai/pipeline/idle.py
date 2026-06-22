"""Silence handling via a small custom frame processor.

Pipecat 1.4 removed the old `UserIdleProcessor`, so we implement the behaviour
directly — and get exactly the escalation we want: when the caller goes quiet we
nudge gently twice, then say a polite goodbye and end the call, instead of
sitting in dead air or hanging up abruptly. Nudges are fixed lines (no LLM
round-trip) so they're instant and deterministic.

The monitor arms a timer after each user/bot turn ends and cancels it the moment
the caller speaks again, so a normal back-and-forth never triggers it.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    EndTaskFrame,
    Frame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..settings import Settings

_NUDGES = [
    "Hello? Are you still there?",
    "No rush — I'm still here whenever you're ready.",
]
_GOODBYE = (
    "It looks like you've stepped away, so I'll let you go for now. "
    "Feel free to call back any time. Goodbye!"
)


class SilenceMonitor(FrameProcessor):
    """Watches for caller silence and escalates: nudge → nudge → polite hang-up."""

    def __init__(self, *, timeout: float):
        super().__init__()
        self._timeout = timeout
        self._timer: asyncio.Task | None = None
        self._retries = 0
        self._armed = False  # only start watching after the first turn of activity

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            # Caller is talking again — reset everything.
            self._armed = True
            self._retries = 0
            await self._stop_timer()
        elif isinstance(frame, BotStartedSpeakingFrame):
            await self._stop_timer()
        elif isinstance(frame, (UserStoppedSpeakingFrame, BotStoppedSpeakingFrame)):
            # A turn just ended; start counting silence.
            self._armed = True
            await self._restart_timer()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop_timer()

        await self.push_frame(frame, direction)

    async def _restart_timer(self) -> None:
        await self._stop_timer()
        if self._armed:
            self._timer = self.create_task(self._wait())

    async def _stop_timer(self) -> None:
        if self._timer is not None:
            await self.cancel_task(self._timer)
            self._timer = None

    async def _wait(self) -> None:
        await asyncio.sleep(self._timeout)
        self._retries += 1
        if self._retries <= len(_NUDGES):
            logger.info("Caller silent (nudge {}/{})", self._retries, len(_NUDGES))
            await self.push_frame(TTSSpeakFrame(_NUDGES[self._retries - 1]))
            # Re-arm for the next window (no self-cancel: this task is finishing).
            self._timer = self.create_task(self._wait())
        else:
            logger.info("Caller silent after {} nudges; ending the call", len(_NUDGES))
            await self.push_frame(TTSSpeakFrame(_GOODBYE))
            await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
            self._timer = None


def build_user_idle_processor(settings: Settings) -> SilenceMonitor:
    return SilenceMonitor(timeout=settings.idle_timeout_seconds)
