"""Pipeline observers: client event bridge (RTVI) + latency metrics logging.

`RTVIObserver` translates internal frames (transcripts, speaking start/stop) into
RTVI messages the browser client consumes for the live transcript + latency HUD.
`MetricsLogObserver` (when available in the installed Pipecat) logs per-service
TTFB and processing time so we can report exactly where the time goes.
"""

from __future__ import annotations

from loguru import logger
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor


def build_observers(rtvi: RTVIProcessor) -> list:
    """Assemble the observer list for the pipeline task."""
    observers: list = [RTVIObserver(rtvi)]

    # MetricsLogObserver lives at different paths across versions; add it if present.
    try:
        from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver

        observers.append(MetricsLogObserver())
        logger.debug("MetricsLogObserver attached")
    except Exception as exc:  # pragma: no cover - depends on installed version
        # Not fatal: `enable_metrics=True` still logs TTFB lines on its own.
        logger.debug("MetricsLogObserver unavailable ({}); relying on built-in metrics", exc)

    return observers
