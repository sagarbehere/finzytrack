"""BL-0309 (S0147): the process tests skip where the OS refuses what they need, and only there.

`tests/process_control.py` asks the OS once per session (a child in its own process group, `ps`, `os.killpg`); a
refusal skips every test marked `needs_process_control`, or, with `FT_REQUIRE_PROCESS_TESTS` set (the pre-push hook,
`ci.yml`), fails the run. These tests start only children that end by themselves (the probe's, and pytest runs on a
sample test that starts nothing), so they run inside the sandbox too.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import override

import pytest

from tests import process_control

BACKEND = Path(__file__).resolve().parents[1]
SAMPLE = "tests/process_control_sample.py"
POSIX_ONLY = "the probe tries process groups and `ps`, which Windows lacks; there it answers allowed"


class FakeOps:
    """Each step works unless it is named in `refuse`; `listed` answers `lists`; the child is a stand-in."""

    def __init__(self, refuse: frozenset[str] = frozenset(), *, lists: bool = True, exit_code: int = -9) -> None:
        self.refuse = refuse
        self.lists = lists
        self.exit_code = exit_code
        self.calls: list[str] = []

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if name in self.refuse:
            raise PermissionError(1, "Operation not permitted")

    def spawn(self) -> process_control.Child:
        self._step("spawn")
        return FakeChild(self)

    def listed(self, pid: int) -> bool:
        del pid
        self._step("listed")
        return self.lists

    def kill_group(self, pid: int) -> None:
        del pid
        self._step("kill_group")

    def kill(self, pid: int) -> None:
        del pid
        self._step("kill")


class FakeChild:
    pid = 4242

    def __init__(self, ops: FakeOps) -> None:
        self.ops = ops

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.ops.calls.append("wait")
        return self.ops.exit_code


@pytest.mark.skipif(sys.platform == "win32", reason=POSIX_ONLY)
def test_every_step_working_is_allowed() -> None:
    """Must not pass with a probe that always answers "refused" (the loosening mutant: it would skip everywhere)."""
    ops = FakeOps()
    assert process_control.probe(ops) is None
    assert ops.calls == ["spawn", "listed", "kill_group", "wait"]


@pytest.mark.skipif(sys.platform == "win32", reason=POSIX_ONLY)
@pytest.mark.parametrize(
    ("ops", "named"),
    [
        (FakeOps(frozenset({"spawn"})), "starting a child in its own process group"),
        (FakeOps(frozenset({"listed"})), "running `ps`"),
        (FakeOps(lists=False), "`ps` did not list"),
        (FakeOps(frozenset({"kill_group"})), "`os.killpg`"),
        (FakeOps(exit_code=0), "did not end the probe's child"),
    ],
)
def test_a_refused_or_failed_step_is_refused_and_named(ops: FakeOps, named: str) -> None:
    answer = process_control.probe(ops)
    assert answer is not None
    assert named in answer


@pytest.mark.skipif(sys.platform == "win32", reason=POSIX_ONLY)
def test_the_probe_ends_and_waits_for_its_child_whatever_step_is_refused() -> None:
    for refuse in ({"listed"}, {"kill_group"}, {"listed", "kill_group"}):
        ops = FakeOps(frozenset(refuse))
        process_control.probe(ops)
        assert ops.calls[-1] == "wait", (refuse, ops.calls)
        if "kill_group" in refuse:
            assert "kill" in ops.calls, "a refused group kill is followed by a kill of the child alone"


class RecordingOps(process_control.RealOps):
    """The real operations, keeping the child; `refuse_kills` stands for a sandbox that refuses every signal."""

    def __init__(self, *, refuse_kills: bool = False) -> None:
        self.refuse_kills = refuse_kills
        self.children: list[subprocess.Popen[bytes]] = []

    @override
    def spawn(self) -> subprocess.Popen[bytes]:
        child = super().spawn()
        self.children.append(child)
        return child

    @override
    def listed(self, pid: int) -> bool:
        return True if self.refuse_kills else super().listed(pid)  # the kill path alone, even where `ps` is refused

    @override
    def kill_group(self, pid: int) -> None:
        if self.refuse_kills:
            raise PermissionError(1, "Operation not permitted")
        super().kill_group(pid)

    @override
    def kill(self, pid: int) -> None:
        if not self.refuse_kills:
            super().kill(pid)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.skipif(sys.platform == "win32", reason=POSIX_ONLY)
def test_the_real_probe_leaves_no_child() -> None:
    """Here (sandbox or not) the real probe answers and its child is reaped and gone when it returns."""
    ops = RecordingOps()
    answer = process_control.probe(ops)
    assert answer is None or answer
    [child] = ops.children
    assert child.returncode is not None, "the probe returned before its child ended"
    assert gone(child.pid)


@pytest.mark.skipif(sys.platform == "win32", reason=POSIX_ONLY)
def test_with_every_kill_refused_the_probes_child_ends_by_itself_within_seconds() -> None:
    ops = RecordingOps(refuse_kills=True)
    start = time.monotonic()
    answer = process_control.probe(ops)
    took = time.monotonic() - start
    assert answer is not None
    assert "`os.killpg`" in answer
    [child] = ops.children
    assert child.returncode == 0, "the child ended by itself"
    assert gone(child.pid)
    assert took < process_control.CHILD_LIFE + 5


def run_sample(forced: str, *, required: bool) -> subprocess.CompletedProcess[str]:
    """pytest on the sample marked test (it starts nothing), the probe's answer forced by the test plugin."""
    env = {k: v for k, v in os.environ.items() if k != process_control.REQUIRE}
    env[FORCED] = forced
    if required:
        env[process_control.REQUIRE] = "1"
    return subprocess.run(  # noqa: S603 (fixed argv: this interpreter running pytest on a fixed sample)
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "tests.process_control_forced", SAMPLE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


FORCED = "FT_TEST_PROCESS_PROBE_ANSWER"  # read only by tests/process_control_forced.py, loaded with -p
REFUSED = "running `ps`: [Errno 1] Operation not permitted: 'ps'"


def test_refused_and_not_required_the_marked_test_skips_with_the_named_reason() -> None:
    done = run_sample(REFUSED, required=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 skipped" in done.stdout, done.stdout
    assert "BL-0309" in done.stdout
    assert REFUSED in done.stdout


def test_refused_and_required_the_run_fails_naming_the_answer() -> None:
    """The must-not-pass test: where the process tests must run, a refusal never turns into a skip."""
    done = run_sample(REFUSED, required=True)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "skipped" not in done.stdout
    assert "passed" not in done.stdout
    assert process_control.REQUIRE in out
    assert REFUSED in out


def test_allowed_and_required_the_marked_test_runs() -> None:
    done = run_sample("", required=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed" in done.stdout, done.stdout


def test_the_answer_is_shown_once_probed() -> None:
    done = run_sample(REFUSED, required=False)
    assert f"process tests: refused ({REFUSED})" in done.stdout, done.stdout
    allowed = run_sample("", required=False)
    assert "process tests: allowed" in allowed.stdout, allowed.stdout


def test_an_unmarked_session_does_not_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe runs only when a marked test is collected (a run of other tests starts no probe child)."""
    calls: list[str] = []
    asked: list[bool] = []

    class Item:
        def get_closest_marker(self, name: str) -> None:
            calls.append(name)

    def answer() -> str | None:
        asked.append(True)
        return "refused"

    monkeypatch.setattr(process_control, "answer", answer)
    process_control.apply([Item()], {process_control.REQUIRE: "1"})  # type: ignore[list-item]
    assert calls == [process_control.MARK]
    assert asked == [], "the probe was asked although no marked test was collected"
