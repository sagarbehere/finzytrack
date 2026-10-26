"""Observability: the event registry, default-deny redaction and the structured log (ARCHITECTURE.md §6A).

The only package that imports `logging` or `structlog` (`.importlinter`, "One logger"); everything else logs through
an `EventSink` with an event from `events`.
"""

from app.platform.observability.events import COMMON, EVENTS, Event, EventSink, Field, Kind, Level, registry_problems
from app.platform.observability.log import (
    EventLog,
    LogContext,
    LogRefusal,
    LogSettings,
    bound_context,
    log_file,
    new_session_id,
    valid_session_id,
)
from app.platform.observability.redaction import Roots

__all__ = [
    "COMMON",
    "EVENTS",
    "Event",
    "EventLog",
    "EventSink",
    "Field",
    "Kind",
    "Level",
    "LogContext",
    "LogRefusal",
    "LogSettings",
    "Roots",
    "bound_context",
    "log_file",
    "new_session_id",
    "registry_problems",
    "valid_session_id",
]
