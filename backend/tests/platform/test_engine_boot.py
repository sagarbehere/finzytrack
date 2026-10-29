"""The engine's first start at boot as a reported state (`app.platform.engine_boot`; S0038; ADR-0054 decision 4).

A fake `EnginePort` stands in for the engine: the state is starting, then ready or down. A failure is a state, never
an exception. The outcome is logged by the client's supervision table, once per start (S0040), not here.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import override

import pytest

from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.engine import EngineFailure, EngineInfo, EngineRefusal, Pong
from app.platform.engine_boot import EngineBoot, EngineDown, EngineReady, EngineStarting
from app.platform.observability import Event
from app.platform.observability.events import ENGINE_START_FAILED

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


def test_the_boot_waits_for_the_engine_lock_before_the_first_start() -> None:
    """engine-jobs §5.1: "At start, before its first engine, the sidecar takes the lock itself, waiting"."""
    order: list[str] = []

    class Recording(FakeEngine):
        @override
        def start(self) -> EngineInfo | EngineFailure:
            order.append("start")
            return super().start()

    boot = EngineBoot(Recording(INFO), wait_engine_lock=lambda: order.append("lock"))
    boot.run()
    assert order == ["lock", "start"]


def test_while_the_lock_is_waited_for_the_engine_is_starting_and_not_started() -> None:
    release = threading.Event()
    engine = FakeEngine(INFO)
    boot = EngineBoot(engine, wait_engine_lock=lambda: release.wait(15))
    thread = boot.start_in_background()
    assert isinstance(boot.state, EngineStarting)
    assert engine.starts == 0
    release.set()
    thread.join(15)
    assert engine.starts == 1
    assert isinstance(boot.state, EngineReady)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object]]] = []

    def __call__(self, event: Event, /, **fields: object) -> None:
        self.events.append((event, fields))


def test_a_failed_engine_lock_wait_is_a_down_state_logged_and_no_engine_starts(tmp_path: Path) -> None:
    """EP-13 (never swallowed), EP-15, engine-jobs §5.1 (no engine before the wait): when the wait itself fails (a
    file where `locks/` belongs, a read-only app-local-data folder), the boot reports `EngineDown` with
    `start_failed`, logs `engine.start_failed`, and starts no engine: it cannot know whether an earlier session's
    engine still runs (S0053 verifier F2)."""
    (tmp_path / "locks").write_text("a file where the folder belongs")
    engine = FakeEngine(INFO)
    log = Recorder()
    boot = EngineBoot(engine, wait_engine_lock=lambda: wait_engine_lock(engine_lock_path(tmp_path)), log=log)
    boot.start_in_background().join(15)
    assert engine.starts == 0
    state = boot.state
    assert isinstance(state, EngineDown)
    assert state.failure.kind == "start_failed"
    assert [(e, f["kind"]) for e, f in log.events] == [(ENGINE_START_FAILED, "start_failed")]


@pytest.mark.parametrize("error", [PermissionError(13, "denied"), OSError(30, "read-only file system")])
def test_any_os_error_of_the_wait_is_a_down_state(error: OSError) -> None:
    def fail() -> None:
        raise error

    engine = FakeEngine(INFO)
    boot = EngineBoot(engine, wait_engine_lock=fail, log=Recorder())
    state = boot.run()
    assert isinstance(state, EngineDown)
    assert boot.state == state
    assert engine.starts == 0
