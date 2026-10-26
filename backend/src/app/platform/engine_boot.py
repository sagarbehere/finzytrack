"""Starting the ledger engine at boot (S0038; ADR-0054 decision 4 and Consequences; ARCHITECTURE.md §2.0, §9.7).

The composition root starts the engine on a thread while the sidecar binds its port and prints its handshake, so a
slow or missing engine delays only itself. The outcome is a state, never an exception: `EngineStarting`, then
`EngineReady` (the handshake's facts and the engine's pid) or `EngineDown` (the typed failure). The client's
supervision table logs every start and stop (`engine.ready`, `engine.start_failed`, ...; S0040), this first one
included, so this module logs nothing. A later request restarts a stopped engine through that table
(`app.platform.engine_supervision`); this module only reports the first start, and the client's `state` is the
current one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from app.platform.engine import EngineFailure, EngineInfo, EnginePort


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

    def __init__(self, engine: EnginePort, pid: Callable[[], int | None] = lambda: None) -> None:
        self._engine = engine
        self._pid = pid
        self._lock = threading.Lock()
        self._state: EngineState = EngineStarting()

    @property
    def state(self) -> EngineState:
        with self._lock:
            return self._state

    def run(self) -> EngineState:
        """Start the engine and record the outcome."""
        out = self._engine.start()
        state: EngineState = EngineDown(out) if isinstance(out, EngineFailure) else EngineReady(out, self._pid())
        with self._lock:
            self._state = state
        return state

    def start_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="engine-boot", daemon=True)
        thread.start()
        return thread
