"""The interim event log: one JSON object per line on the sidecar's stderr (S0038; ARCHITECTURE.md §6A).

§6A's structured log (structlog, the event registry, default-deny redaction, `<app_local_data>/logs/
finzytrack.jsonl`) does not exist yet (BL-0088). Until it does, the composition root writes its few events here, in
§6A's line shape: `ts` (UTC, milliseconds), `level`, `event`, `source` (`backend`, or `engine` for the engine's own
stderr lines), `app_version`, then the event's own fields. The common fields always win over an event's fields of
the same name, except `source`. Nothing here is persisted by the sidecar: the shell or the acceptance harness
decides where its stderr goes. The engine holds no ledger yet, so its stderr carries no ledger text; BL-0088 must
land before the first engine operation that reads a ledger, to redact it (EP-26).
A stream that fails (closed, broken pipe) drops the line: logging never takes the sidecar down.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import UTC
from typing import TextIO

from app.platform.clock import Clock
from app.platform.engine_boot import Level

COMMON = ("ts", "level", "event", "source", "app_version")


class StderrLog:
    """An `EventLog` writing JSON lines to `stream`. Thread-safe: one line is written whole."""

    def __init__(self, stream: TextIO, clock: Clock, app_version: str) -> None:
        self._stream = stream
        self._clock = clock
        self._app_version = app_version
        self._lock = threading.Lock()

    def __call__(self, level: Level, event: str, fields: Mapping[str, object]) -> None:
        ts = self._clock.now().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        source = fields.get("source", "backend")
        own = {k: v for k, v in fields.items() if k not in COMMON}
        line = json.dumps(
            {"ts": ts, "level": level, "event": event, "source": source, "app_version": self._app_version, **own},
            ensure_ascii=False,
            default=str,
        )
        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (OSError, ValueError):
                return
