"""The event registry: every event the main sidecar logs, declared once (ARCHITECTURE.md §6A.3; EP-05, EP-15, EP-26).

An event declares its name, its level class (§6A.5), its feature, its source and its typed fields. A field's kind
says how its value may be written:

- the safe kinds `id`, `count`, `duration_ms`, `bytes`, `enum` and `safe_text` are written as themselves, but only
  when the value fits the kind (`app.platform.observability.redaction`); `enum` and `safe_text` list their values
  (`safe_text`: developer-written constants only);
- `debug_detail` is written only in debug mode, and redacted by its value's type otherwise.

A value that is not a declared field is never written. The registry is checked by `registry_problems` (EP-26's
event-registry schema test). `code` (a registry error code) joins the kinds with the error registry (BL-0093).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, get_args

type Level = Literal["debug", "info", "warning", "error"]
type Source = Literal["backend", "engine", "ui", "shell"]

COMMON = (
    "ts",
    "level",
    "event",
    "session_id",
    "correlation_id",
    "ws",
    "source",
    "feature",
    "app_version",
    "debug",
    "undeclared_fields",
)
"""§6A.1's fields of every line, in their order; an event's own fields follow, and none may share these names."""

EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")
FEATURE_NAME = re.compile(r"[a-z][a-z0-9_]*")


class Kind(StrEnum):
    ID = "id"
    COUNT = "count"
    DURATION_MS = "duration_ms"
    BYTES = "bytes"
    ENUM = "enum"
    SAFE_TEXT = "safe_text"
    DEBUG_DETAIL = "debug_detail"


CLOSED_KINDS = frozenset({Kind.ENUM, Kind.SAFE_TEXT})


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    kind: Kind
    values: frozenset[str] = field(default=frozenset())


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    level: Level
    feature: str
    fields: tuple[Field, ...]
    source: Source = "backend"


class EventSink(Protocol):
    """Writes one registered event with its own fields (the log, or a test's recorder)."""

    def __call__(self, event: Event, /, **fields: object) -> None: ...


# -- The ledger engine's lifecycle (S0038; logged by its supervision table since S0040; ARCHITECTURE.md §2.0, §7) ---

ENGINE_READY = Event(
    "engine.ready",
    "info",
    "platform",
    (
        Field("pid", Kind.ID),
        Field("protocol", Kind.ID),
        Field("engine_version", Kind.ID),
        Field("beancount_version", Kind.ID),
    ),
)
ENGINE_START_FAILED = Event(
    "engine.start_failed",
    "error",
    "platform",
    (
        Field(
            "kind",
            Kind.ENUM,  # `app.platform.engine.FailureKind`, held equal by a test
            frozenset(
                {
                    "crashed",
                    "cancelled",
                    "protocol_violation",
                    "incompatible",
                    "start_failed",
                    "unavailable",
                    "closed",
                    "too_large",
                }
            ),
        ),
        Field("detail", Kind.DEBUG_DETAIL),  # free text: an OS error, the engine's refusal
    ),
)
ENGINE_STOPPED = Event(
    "engine.stopped",
    "warning",  # a degraded dependency (§6A.5): the next request restarts it
    "platform",
    (
        # The ways a running engine stops (`app.platform.engine_supervision`, S0040); a failed start is
        # `engine.start_failed`.
        Field("kind", Kind.ENUM, frozenset({"crashed", "protocol_violation"})),
        Field("detail", Kind.DEBUG_DETAIL),  # free text: how the output ended, a decoder's complaint
    ),
)
ENGINE_UNAVAILABLE = Event(
    "engine.unavailable",
    "error",  # the engine of every write and BQL query is down until the restart window lets a stop go
    "platform",
    (Field("stops", Kind.COUNT),),
)
ENGINE_ENDED = Event(
    "engine.ended",
    "info",
    "platform",
    # Our own end of an engine (S0057; engine-jobs §5.2, §5.3): a Cancel, Quit anyway or a retire, never a stop.
    (Field("reason", Kind.ENUM, frozenset({"cancel", "retire", "quit_anyway"})),),
)
ENGINE_BYTECODE = Event(
    "engine.bytecode",
    "warning",  # only its trouble is logged: a fill that failed, a directory discarded after a failed start
    "platform",
    (
        Field("outcome", Kind.ENUM, frozenset({"fill_failed", "discarded"})),
        Field("detail", Kind.DEBUG_DETAIL),  # free text: the failed start's detail, the fill's exit
    ),
)
ENGINE_EXIT = Event(
    "engine.exit",
    "info",
    "platform",
    # Each engine's end report, logged by the job scheduler with its job (S0058; engine-jobs §5.2): content-free.
    (
        Field("engine", Kind.ID),  # the queue's engine number (the one its `Start` carried)
        Field(
            "job",
            Kind.ENUM,  # `app.platform.engine_jobs.queue.JobKind`, held equal by a test
            frozenset({"open", "rebuild", "preview", "save", "r4", "bql", "recover_all", "create", "copy"}),
        ),
        Field(
            "reason",
            Kind.ENUM,  # who ended it (`app.platform.engine.EngineEnd.reason`); `none`: it ended by itself
            frozenset({"cancel", "retire", "quit_anyway", "close", "stop", "input_closed", "none"}),
        ),
        Field("exit_code", Kind.COUNT),  # its exit status, if it exited
        Field("signal", Kind.COUNT),  # the signal that ended it, if one did
        Field("max_rss_kib", Kind.COUNT),  # its peak memory
    ),
)
ENGINE_STDERR = Event("engine.stderr", "info", "platform", (Field("line", Kind.DEBUG_DETAIL),), source="engine")
ENGINE_CLOSED = Event("engine.closed", "info", "platform", ())

# -- The API edge (S0046; ARCHITECTURE.md §6.3) ------------------------------------------------------------------------

API_INTERNAL_ERROR = Event(
    "api.internal_error",
    "error",
    "api",
    (
        Field("exception_type", Kind.ID),  # the exception's class name
        Field("exception", Kind.DEBUG_DETAIL),  # its text and traceback: free text that can carry ledger data
    ),
)
"""A request ended in `INTERNAL`: an exception nothing expected, or a malformed `DomainError`. The line carries the
request's id as `correlation_id`, which the error envelope shows to the user (§6.6)."""

# -- The engine job scheduler (S0058; ARCHITECTURE.md §6.3: the job runner is one of the three catch-all places) -----

JOB_INTERNAL_ERROR = Event(
    "jobs.internal_error",
    "error",
    "platform",
    (
        Field("job", Kind.ID),  # the job's id in the queue
        Field("exception_type", Kind.ID),  # the exception's class name
        Field("exception", Kind.DEBUG_DETAIL),  # its text and traceback: free text that can carry ledger data
    ),
)
"""A job's own code raised: the job ends `INTERNAL` and its engine is not reused."""

# -- Lines the log writes for what is not a registered event -------------------------------------------------------

LIB_RECORD = Event(
    "lib.record",
    "warning",  # written at the record's own level
    "platform",
    (Field("logger", Kind.ID), Field("message", Kind.DEBUG_DETAIL), Field("exception", Kind.DEBUG_DETAIL)),
)
"""A record from another library's stdlib logger, or a `warnings` warning."""
UNCAUGHT = Event(
    "app.uncaught",
    "error",
    "platform",
    (Field("thread", Kind.ID), Field("exception", Kind.DEBUG_DETAIL)),
)
"""An exception nothing caught, on any thread."""
UNREGISTERED = Event("log.unregistered", "error", "platform", ())
"""An `Event` that is not in the registry was logged: neither its name nor its fields are written."""

EVENTS = (
    ENGINE_READY,
    ENGINE_START_FAILED,
    ENGINE_STOPPED,
    ENGINE_UNAVAILABLE,
    ENGINE_ENDED,
    ENGINE_BYTECODE,
    ENGINE_EXIT,
    ENGINE_STDERR,
    ENGINE_CLOSED,
    API_INTERNAL_ERROR,
    JOB_INTERNAL_ERROR,
    LIB_RECORD,
    UNCAUGHT,
    UNREGISTERED,
)


def registry_problems(events: tuple[Event, ...]) -> list[str]:
    """Every way a registry can break §6A.3's rules, as messages; empty when it holds."""
    out: list[str] = []
    names: set[str] = set()
    for e in events:
        if e.name in names:
            out.append(f"{e.name}: listed twice")
        names.add(e.name)
        if not EVENT_NAME.fullmatch(e.name):
            out.append(f"{e.name!r}: the name must be dotted lower-case words")
        if e.level not in get_args(Level.__value__):
            out.append(f"{e.name}: unknown level {e.level!r}")
        if e.source not in get_args(Source.__value__):
            out.append(f"{e.name}: unknown source {e.source!r}")
        if not FEATURE_NAME.fullmatch(e.feature):
            out.append(f"{e.name}: the feature must be one lower-case word")
        seen: set[str] = set()
        for f in e.fields:
            out.extend(_field_problems(e, f, seen))
            seen.add(f.name)
    return out


def _field_problems(e: Event, f: Field, seen: set[str]) -> list[str]:
    out: list[str] = []
    where = f"{e.name}.{f.name}"
    if f.name in seen:
        out.append(f"{where}: declared twice")
    if f.name in COMMON:
        out.append(f"{where}: shadows a common field")
    if not FIELD_NAME.fullmatch(f.name):
        out.append(f"{where}: the field name must be a lower-case word")
    if not isinstance(f.kind, Kind):  # pyright: ignore[reportUnnecessaryIsInstance] (a registry is data)
        out.append(f"{where}: unknown kind {f.kind!r}")
    elif f.kind in CLOSED_KINDS:
        if not f.values or not all(isinstance(v, str) and v for v in f.values):  # pyright: ignore[reportUnnecessaryIsInstance]
            out.append(f"{where}: {f.kind} needs its values, each a non-empty str")
    elif f.values:
        out.append(f"{where}: only enum and safe_text take values")
    return out
