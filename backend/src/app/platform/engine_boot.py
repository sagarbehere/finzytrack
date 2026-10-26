"""Starting the ledger engine at boot (S0038; ADR-0054 decision 4 and Consequences; ARCHITECTURE.md §2.0, §9.7).

The composition root starts the engine on a thread while the sidecar binds its port and prints its handshake, so a
slow or missing engine delays only itself. The outcome is a state, never an exception: `EngineStarting`, then
`EngineReady` (the handshake's facts and the engine's pid) or `EngineDown` (the typed failure). Each outcome is
logged once as its registered event (`engine.ready`, `engine.start_failed`; ARCHITECTURE.md §6A). A later request
restarts a stopped engine through the client's own supervision (`RestartPolicy`); this module only reports the first
start. The future `/api/boot` and `/health` read `state`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from app.platform.engine import EngineFailure, EngineInfo, EnginePort
from app.platform.observability import EventSink
from app.platform.observability.events import ENGINE_READY, ENGINE_START_FAILED


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

    def __init__(self, engine: EnginePort, log: EventSink, pid: Callable[[], int | None] = lambda: None) -> None:
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
            self._log(ENGINE_START_FAILED, kind=out.kind, detail=out.detail)
        else:
            state = EngineReady(out, self._pid())
            self._log(
                ENGINE_READY,
                pid=state.pid,
                protocol=out.protocol,
                engine_version=out.engine_version,
                beancount_version=out.beancount_version,
            )
        with self._lock:
            self._state = state
        return state

    def start_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="engine-boot", daemon=True)
        thread.start()
        return thread
