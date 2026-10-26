"""Starting the ledger engine at boot (S0038; ADR-0054 decision 4 and Consequences; ARCHITECTURE.md §2.0, §9.7).

The composition root starts the engine on a thread while the sidecar binds its port and prints its handshake, so a
slow or missing engine delays only itself. The outcome is a state, never an exception: `EngineStarting`, then
`EngineReady` (the handshake's facts and the engine's pid) or `EngineDown` (the typed failure). Each outcome is
logged once as an event (`engine.ready`, `engine.start_failed`). A later request restarts a stopped engine through
the client's own supervision (`RestartPolicy`); this module only reports the first start. The future `/api/boot` and
`/health` read `state`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from app.platform.engine import EngineFailure, EngineInfo, EnginePort

type Level = Literal["debug", "info", "warning", "error"]
type EventLog = Callable[[Level, str, Mapping[str, object]], None]
"""Writes one event: its level, its name and its own fields (ARCHITECTURE.md §6A line shape)."""


@dataclass(frozen=True, slots=True)
class EngineStarting:
    """The first start has not finished."""


@dataclass(frozen=True, slots=True)
class EngineReady:
    info: EngineInfo
    pid: int | None


@dataclass(frozen=True, slots=True)
class EngineDown:
    failure: EngineFailure


type EngineState = EngineStarting | EngineReady | EngineDown


class EngineBoot:
    """The engine's first start and its reported state. Owned by the user container (`state_registry`)."""

    def __init__(self, engine: EnginePort, log: EventLog, pid: Callable[[], int | None] = lambda: None) -> None:
        self._engine = engine
        self._log = log
        self._pid = pid
        self._lock = threading.Lock()
        self._state: EngineState = EngineStarting()

    @property
    def state(self) -> EngineState:
        with self._lock:
            return self._state

    def run(self) -> EngineState:
        """Start the engine and record and log the outcome."""
        out = self._engine.start()
        if isinstance(out, EngineFailure):
            state: EngineState = EngineDown(out)
            self._log("error", "engine.start_failed", {"kind": out.kind, "detail": out.detail})
        else:
            state = EngineReady(out, self._pid())
            self._log(
                "info",
                "engine.ready",
                {
                    "pid": state.pid,
                    "protocol": out.protocol,
                    "engine_version": out.engine_version,
                    "beancount_version": out.beancount_version,
                },
            )
        with self._lock:
            self._state = state
        return state

    def start_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="engine-boot", daemon=True)
        thread.start()
        return thread
