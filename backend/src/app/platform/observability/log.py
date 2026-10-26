"""The structured log: one JSON-lines file per installation (ARCHITECTURE.md §6A.1; ADR-0039; EP-15, EP-26).

`EventLog` writes registered events to `<app_local_data>/logs/finzytrack.jsonl` through structlog on stdlib
`logging`: a size-rotated file handler whose structlog `ProcessorFormatter` ends every line, ours and other
libraries', in the one redaction step (`app.platform.observability.redaction`). `capture_process` routes the rest of
the process into the same handler: other libraries' stdlib records at warning and above, `warnings`, and exceptions
nothing caught (main thread, other threads, unraisable). A line that cannot be written is dropped without printing
it: logging never takes the sidecar down, and the stdlib's error dump (which prints the raw record) never runs.

The log is the composition root's: it is built once per launch, before anything logs, and closed last. Its own
fields are the session's (`LogContext`); `correlation_id` and `ws` are bound per request with `bound_context`.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import uuid
from collections.abc import Callable, Generator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Any, override

import structlog

from app.platform.clock import Clock
from app.platform.observability.events import EVENTS, LIB_RECORD, UNCAUGHT, UNREGISTERED, Event, Level
from app.platform.observability.redaction import Common, Roots, redact_line

FILE_NAME = "finzytrack.jsonl"
SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
# Keys structlog and this module put on an event dict; everything else on it came from bound context.
META = frozenset({"event", "_record", "_from_structlog", "_ft_event", "_ft_fields", "exc_info", "stack_info"})
DROPPED_NOTICE = "finzytrack: a log line could not be written; later failures are not reported\n"


@dataclass(frozen=True, slots=True)
class LogSettings:
    """Size-based rotation (§6A.1): an app setting sized per deployment, not a development budget."""

    max_bytes: int = 10 * 1024 * 1024
    backups: int = 5


@dataclass(frozen=True, slots=True)
class LogContext:
    session_id: str
    app_version: str


@dataclass(frozen=True, slots=True)
class LogRefusal:
    reason: str


def new_session_id() -> str:
    """One per app launch, when the shell did not pass one (headless mode, development)."""
    return uuid.uuid4().hex


def valid_session_id(text: str) -> bool:
    return SESSION_ID.fullmatch(text) is not None


def log_file(app_local_data: Path, workspace: Path | None) -> Path | LogRefusal:
    """The log file's path, with its folder made; refused when it would lie outside the app-local-data folder (a
    `logs` link elsewhere, a linked file) or inside the workspace folder (INV-27). Never relative to the cwd.

    Folders are compared by identity (device and inode), not by spelling, so another case on a case-insensitive file
    system or a link to the same folder is the same folder. Nothing is created before the decision (S0039 verifier
    F1, F2)."""
    logs = app_local_data / "logs"
    try:
        base = app_local_data.resolve(strict=True)
        real = (logs / FILE_NAME).resolve()
        if not _inside(real, base):
            return LogRefusal("the log folder leads outside the app's data folder")
        if workspace is not None and _inside(real, workspace):
            return LogRefusal("the log file would be inside the workspace folder")
        logs.mkdir(exist_ok=True)
    except OSError:
        return LogRefusal("the log folder cannot be made in the app's data folder")
    return base / "logs" / FILE_NAME


def _inside(path: Path, folder: Path) -> bool:
    """Whether `folder` is `path` or one of its existing ancestors, judged by file identity. `path` is resolved, so
    its ancestors are real folders; a `folder` that cannot be read raises (the caller refuses)."""
    target = folder.stat()
    for p in (path, *path.parents):
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        if (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False


@contextmanager
def bound_context(correlation_id: str | None = None, ws: str | None = None) -> Generator[None]:
    """Bind the request's ids to every line logged inside the block, on this thread or task."""
    ids = {k: v for k, v in (("correlation_id", correlation_id), ("ws", ws)) if v is not None}
    with structlog.contextvars.bound_contextvars(**ids):
        yield


def _levelno(level: Level) -> int:
    match level:
        case "debug":
            return logging.DEBUG
        case "info":
            return logging.INFO
        case "warning":
            return logging.WARNING
        case "error":
            return logging.ERROR


def _level_of(levelno: int) -> Level:
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warning"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


class _Handler(RotatingFileHandler):
    """Drops a line it cannot write. The stdlib's `handleError` would print the raw record (every field unredacted)."""

    def __init__(self, path: Path, settings: LogSettings) -> None:
        super().__init__(path, maxBytes=settings.max_bytes, backupCount=settings.backups, encoding="utf-8")
        self.reported = False

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        if not self.reported:
            self.reported = True
            try:
                sys.stderr.write(DROPPED_NOTICE)
            except (OSError, ValueError):
                return


class EventLog:
    """The `EventSink` of the main sidecar. Thread-safe (the handler's lock); one line is written whole."""

    def __init__(
        self,
        path: Path,
        *,
        context: LogContext,
        clock: Clock,
        roots: Roots,
        debug: Callable[[], bool] = lambda: False,
        settings: LogSettings | None = None,
        registry: tuple[Event, ...] = EVENTS,
    ) -> None:
        self._context = context
        self._clock = clock
        self._roots = roots
        self._debug = debug
        self._registry = registry
        self._closed = False
        self._logger = logging.Logger("finzytrack", logging.DEBUG)  # private: not in logging's global registry
        self._logger.propagate = False
        self._handler = _Handler(path, settings or LogSettings())
        self._handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[structlog.contextvars.merge_contextvars, self._finish, structlog.processors.JSONRenderer()]
            )
        )
        self._handler.addFilter(self._admit)
        self._logger.addHandler(self._handler)
        self._bound = structlog.wrap_logger(
            self._logger,
            processors=[structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    def __call__(self, event: Event, /, **fields: object) -> None:
        if self._closed:
            return  # a closed log writes nothing, and never reaches logging's last-resort stderr handler
        self._bound.log(_levelno(event.level), event.name, _ft_event=event, _ft_fields=fields)

    def _admit(self, record: logging.LogRecord) -> bool:
        """Every line of ours; another logger's only at warning and above (§6A.5 volume)."""
        return getattr(record, "_logger", None) is self._logger or record.levelno >= logging.WARNING

    def _finish(self, _logger: object, _method: str, ed: MutableMapping[str, Any]) -> Mapping[str, Any]:
        """The one exit of every line: ours, other libraries', bound context. Builds a new dict, so nothing reaches
        the renderer that `redact_line` did not put there."""
        record = ed.get("_record")
        levelno = record.levelno if isinstance(record, logging.LogRecord) else logging.ERROR
        context = {k: v for k, v in ed.items() if k not in META}
        ours = ed.get("_from_structlog") is True and getattr(record, "_logger", None) is self._logger
        event = ed.get("_ft_event")
        raw = ed.get("_ft_fields")
        fields: Mapping[str, object] = raw if isinstance(raw, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        if ours and isinstance(event, Event) and event in self._registry:
            level = event.level
        elif ed.get("_from_structlog") is True:
            event, level = UNREGISTERED, UNREGISTERED.level
        else:
            event, level = LIB_RECORD, _level_of(levelno)
            fields = {"logger": record.name if isinstance(record, logging.LogRecord) else None, "message": ed["event"]}
            exc = ed.get("exc_info")
            if isinstance(exc, tuple) and len(exc) == 3 and isinstance(exc[1], BaseException):  # pyright: ignore[reportUnknownArgumentType]
                fields["exception"] = exc[1]
        ts = self._clock.now().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        common = Common(ts, self._context.session_id, self._context.app_version)
        return redact_line(event, level, fields, context, common, self._debug(), self._roots)  # pyright: ignore[reportUnknownArgumentType]

    def capture_process(self) -> Callable[[], None]:
        """Route other libraries' records, warnings and uncaught exceptions here; returns the undo. The composition
        root calls it once, after building the log."""
        root = logging.getLogger()
        saved = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        root.addHandler(self._handler)
        logging.captureWarnings(True)
        sys.excepthook = self._on_uncaught
        threading.excepthook = self._on_thread_uncaught
        sys.unraisablehook = self._on_unraisable

        def release() -> None:
            sys.excepthook, threading.excepthook, sys.unraisablehook = saved
            logging.captureWarnings(False)
            root.removeHandler(self._handler)

        return release

    def _on_uncaught(self, t: type[BaseException], e: BaseException, tb: TracebackType | None) -> None:
        self(UNCAUGHT, thread=threading.current_thread().name, exception=e)

    def _on_thread_uncaught(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        thread = args.thread.name if args.thread is not None else None
        self(UNCAUGHT, thread=thread, exception=args.exc_value)

    def _on_unraisable(self, args: sys.UnraisableHookArgs) -> None:
        self(UNCAUGHT, thread=threading.current_thread().name, exception=args.exc_value)

    def close(self) -> None:
        """Later events are dropped. The handler stays on the private logger, so a record can never find that
        logger without a handler (logging would print it, unredacted, through its last-resort handler)."""
        self._closed = True
        self._handler.close()
