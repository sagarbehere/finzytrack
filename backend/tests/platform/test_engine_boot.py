"""The engine's first start at boot as a reported state (`app.platform.engine_boot`; S0038; ADR-0054 decision 4).

A fake `EnginePort` stands in for the engine: the state is starting, then ready or down. A failure is a state, never
an exception. The outcome is logged by the client's supervision table, once per start (S0040), not here.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.platform.engine import EngineFailure, EngineInfo, EngineRefusal, Pong
from app.platform.engine_boot import EngineBoot, EngineDown, EngineReady, EngineStarting

INFO = EngineInfo(protocol=1, engine_version="0.4.0.dev0", beancount_version="3.2.0")


class FakeEngine:
    def __init__(self, start: EngineInfo | EngineFailure) -> None:
        self._start = start
        self.starts = 0

    def start(self) -> EngineInfo | EngineFailure:
        self.starts += 1
        return self._start

    def ping(self, timeout: float) -> Pong | EngineRefusal | EngineFailure:
        return Pong()

    def call(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        return {}

    def close(self) -> None:
        pass


def test_a_started_engine_is_ready_with_its_facts() -> None:
    boot = EngineBoot(FakeEngine(INFO), pid=lambda: 4242)
    assert isinstance(boot.state, EngineStarting)
    assert boot.run() == EngineReady(INFO, 4242)
    assert boot.state == EngineReady(INFO, 4242)


def test_a_failed_start_is_a_down_state() -> None:
    failure = EngineFailure("start_failed", "the engine could not be started: no such file")
    boot = EngineBoot(FakeEngine(failure))
    assert boot.run() == EngineDown(failure)
    assert boot.state == EngineDown(failure)


def test_the_background_start_runs_once_and_sets_the_state() -> None:
    engine = FakeEngine(INFO)
    boot = EngineBoot(engine)
    boot.start_in_background().join(10)
    assert engine.starts == 1
    assert isinstance(boot.state, EngineReady)
