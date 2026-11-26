"""The hang paths' test-only hook (BL-0195, S0156; `tests/platform/held_leaders.py`): a signal goes to an engine only
while the client's reaper holds its leader unreaped, never after the reap, when its id may belong to another program.

The child here ends by itself once its input is closed, and the signals are recorded, never sent, so the test needs no
process control (`tests/process_control.py`)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable

import pytest

from tests.windows_pending import POSIX_SUPERVISION

if sys.platform == "win32":
    raise pytest.skip.Exception(POSIX_SUPERVISION, allow_module_level=True)

import signal

from app.platform.adapters.engine_group import PosixGroupOps
from tests.platform import held_leaders

DEADLINE = 20.0


def test_a_held_leader_is_signalled_and_a_reaped_one_never(monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not pass a hook that signals whatever pid it is given: after the reap the id is free for reuse."""
    held_leaders.install(monkeypatch)
    sent: list[tuple[str, int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        sent.append(("killpg", pid, sig))

    def kill(pid: int, sig: int) -> None:
        sent.append(("kill", pid, sig))

    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(os, "kill", kill)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE, start_new_session=True
    )
    ops = PosixGroupOps()
    pid = child.pid
    assert not held_leaders.signal_held(pid, signal.SIGKILL, group=True), "signalled before the reaper held it"
    waiting = threading.Thread(target=ops.wait_leader, args=(pid,), daemon=True)
    waiting.start()
    try:
        assert _eventually(lambda: held_leaders.held(pid))
        assert held_leaders.signal_held(pid, signal.SIGCONT, group=False)
        assert held_leaders.signal_held(pid, signal.SIGKILL, group=True)
        assert sent == [("kill", pid, signal.SIGCONT), ("killpg", pid, signal.SIGKILL)]
    finally:
        assert child.stdin is not None
        child.stdin.close()  # the child ends by itself
        waiting.join(DEADLINE)
    assert not waiting.is_alive()
    assert held_leaders.held(pid), "an exited leader is still held until its reap"
    ops.reap(pid)
    sent.clear()
    assert not held_leaders.signal_held(pid, signal.SIGKILL, group=True)
    assert not held_leaders.signal_held(pid, signal.SIGCONT, group=False)
    assert sent == [], "a signal went to a reaped engine's id"


def _eventually(cond: Callable[[], bool]) -> bool:
    done = threading.Event()
    for _ in range(int(DEADLINE / 0.02)):
        if cond():
            return True
        done.wait(0.02)
    return False
