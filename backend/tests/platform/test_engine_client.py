"""The engine client against the real engine process (ADR-0054 decision 4; S0035 brief; EP-07, EP-13; since S0057
`design/specs/engine-jobs.md` §5.1-§5.5).

The real engine runs from `engine/.venv` (sync it with `uv sync --locked` in `engine/`). A few cases need an engine
that misbehaves in one exact way; those run a small stand-in script instead. Backoff is checked with an injected
monotonic clock and pause, so no test waits for real backoff time (EP-28). Since S0057 nothing in the client has a
time limit: a call waits until the engine answers, ends, or is ended by us (`end`, `close`). Where a test must show
that something did *not* happen (a call still waiting, no second engine), it looks once after `ABSENCE` seconds, far
above the time the event would take (an engine starts in about 0.1 s): an absence cannot be awaited (EP-28
deviation, as in S0053).
"""

from __future__ import annotations

import contextlib
import dataclasses
import faulthandler
import fcntl
import inspect
import os
import queue
import resource
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, override

import pytest

from app.platform.adapters.engine_client import (
    EngineClient,
    EngineCommand,
    _Lines,  # pyright: ignore[reportPrivateUsage]
    _pump_lines_until_reaped,  # pyright: ignore[reportPrivateUsage]
    next_request_id,
)
from app.platform.adapters.engine_group import PosixGroupOps, Reaped
from app.platform.engine import (
    Committed,
    Decision,
    EngineEnd,
    EngineEvent,
    EngineFailure,
    EngineInfo,
    EnginePort,
    EngineRefusal,
    GateReached,
    Pong,
    Progress,
)
from app.platform.engine_protocol import MAX_ID
from app.platform.engine_supervision import (
    Busy,
    Closed,
    Crashed,
    Handshaking,
    Idle,
    Ready,
    RestartPolicy,
    Send,
    SupervisionEvent,
    Unavailable,
)
from app.platform.observability import Event

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 20.0
HANG_GUARD = 15 * DEADLINE  # the test's own hang detector (below), far above its longest wait (a bytecode fill)


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    """Since S0057 the client has no time limits (engine-jobs §5.4), so a check that misses what it should catch (a
    violation, an end) would leave its test waiting for ever. This guard is the test's, not the product's: after
    `HANG_GUARD` seconds it ends the test process with every thread's traceback, which names the hung test (with
    xdist, the worker's crash names it too)."""
    faulthandler.dump_traceback_later(HANG_GUARD, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


ABSENCE = 0.5


def engine_command(*, test_ops: bool = True) -> EngineCommand:
    if not (ENGINE_VENV / "bin" / "python").exists() and not (ENGINE_VENV / "Scripts" / "python.exe").exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return EngineCommand.for_venv(ENGINE_VENV, test_ops=test_ops)


class FakeTime:
    """An injected monotonic clock whose pause advances it, recording each pause (EP-28: no real waits)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.pauses: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def client(fake_time: FakeTime) -> Iterator[EngineClient]:
    c = EngineClient(engine_command(), RestartPolicy(), monotonic=fake_time.monotonic, pause=fake_time.pause)
    yield c
    c.close()


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    step = threading.Event()
    while time.monotonic() < end:
        if cond():
            return True
        step.wait(0.02)
    return cond()


def alive(pid: int) -> bool:
    """Running: it exists and is not a zombie (a killed child stays a zombie until the client reaps it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607 (fixed argv)
    return not stat.stdout.strip().startswith("Z") and bool(stat.stdout.strip())


def stopped(pid: int) -> bool:
    """Wait until the process is no longer running."""
    return wait_until(lambda: not alive(pid))


def kill(pid: int) -> None:
    os.kill(pid, signal.SIGKILL if os.name == "posix" else signal.SIGTERM)


def in_thread[T](fn: Callable[[], T]) -> tuple[threading.Thread, list[T]]:
    out: list[T] = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    return t, out


def engine_version() -> str:
    return tomllib.loads((REPO / "engine" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_client_is_an_engine_port(client: EngineClient) -> None:
    port: EnginePort = client  # pyright checks the structural match
    assert port is client


def test_start_handshakes(client: EngineClient) -> None:
    info = client.start()
    assert info == EngineInfo(protocol=2, engine_version=engine_version(), beancount_version="3.2.0")
    assert client.pid is not None
    assert alive(client.pid)


def test_ping_starts_the_engine_when_needed(client: EngineClient) -> None:
    assert client.pid is None
    assert client.ping() == Pong()
    assert client.pid is not None


def test_a_refusal_comes_back_as_a_refusal(client: EngineClient) -> None:
    out = client.call("no.such.op", {})
    assert isinstance(out, EngineRefusal)
    assert out.key == "protocol.unknown_op"
    assert client.ping() == Pong()  # a refusal does not end the engine


def test_an_engine_killed_mid_request_is_a_failure_not_a_hang(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    first = client.pid
    assert first is not None
    t, out = in_thread(lambda: client.call("test.stall", {}))
    assert wait_until(lambda: "stalling" in client.stderr_tail()), "the engine never started the request"
    kill(first)
    t.join(DEADLINE)
    assert not t.is_alive(), "the caller hung after the engine died"
    assert len(out) == 1
    failure = out[0]
    assert isinstance(failure, EngineFailure)
    assert failure.kind == "crashed"
    # The next request restarts the engine, after the first backoff delay.
    assert client.ping() == Pong()
    assert client.pid not in (None, first)
    assert fake_time.pauses == [0.5]


def test_a_request_is_never_retried_after_a_crash(client: EngineClient) -> None:
    """A write is not idempotent: the caller decides; the client never resends (S0035 decision)."""
    client.start()
    pid = client.pid
    assert pid is not None
    t, out = in_thread(lambda: client.call("test.stall", {}))
    assert wait_until(lambda: "stalling" in client.stderr_tail())
    kill(pid)
    t.join(DEADLINE)
    assert isinstance(out[0], EngineFailure)
    assert client.pid is None  # no new engine was started, so nothing was resent on the caller's behalf


def test_an_engine_that_died_between_requests_is_restarted(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    kill(pid)
    assert stopped(pid)
    assert client.ping() == Pong()
    assert fake_time.pauses == [0.5]


def test_restarts_are_bounded_then_allowed_again_after_the_window(client: EngineClient, fake_time: FakeTime) -> None:
    for _ in range(3):
        assert client.ping() == Pong()
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    out = client.ping()
    assert isinstance(out, EngineFailure)
    assert out.kind == "unavailable"
    now = client.pid
    assert now is None or not alive(now)
    assert fake_time.pauses == [0.5, 2.0]
    fake_time.now += RestartPolicy().window
    assert client.ping() == Pong()


def test_reset_allows_a_restart_at_once(client: EngineClient) -> None:
    for _ in range(3):
        client.ping()
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    assert isinstance(client.ping(), EngineFailure)
    client.reset()
    assert client.ping() == Pong()


def stand_in(script: str) -> EngineCommand:
    return EngineCommand(argv=(sys.executable, "-I", "-c", script), env=None)


def test_a_start_that_fails_counts_towards_the_bound(fake_time: FakeTime) -> None:
    c = EngineClient(stand_in("import sys; sys.exit(3)"), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        kinds = [r.kind for r in (c.start() for _ in range(4)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed", "start_failed", "unavailable"]
        assert fake_time.pauses == [0.5, 2.0]
        fake_time.now += RestartPolicy().window
        again = c.start()
        assert isinstance(again, EngineFailure)
        assert again.kind == "start_failed"
        assert fake_time.pauses == [0.5, 2.0]  # the window passed: no delay before this start
    finally:
        c.close()


COUNTING = r"""
import json, sys
with open(sys.argv[1], "a") as fh:
    fh.write("x")
req = json.loads(sys.stdin.readline())
reply = {MODE}
sys.stdout.write(json.dumps(reply) + "\n")
sys.stdout.flush()
sys.stdin.read()
"""
MISMATCH = """{"v": 2, "id": req["id"], "op": "handshake", "ok": False,
         "error": {"key": "protocol.version_mismatch", "message": "this engine speaks protocol 2"}}"""
NEWER = """{"v": 2, "id": req["id"], "op": "handshake", "ok": True,
         "result": {"protocol": 3, "engine_version": "9", "beancount_version": "3.2.0"}}"""
# S0035 verifier F2: a real protocol-2 engine stamps its own version on every reply, in a shape this side cannot
# know; the client must still recognise it by `v` and call it incompatible.
V2_REFUSAL = """{"v": 3, "id": req["id"], "op": "handshake", "ok": False,
         "error": {"key": "protocol.version_mismatch", "message": "this engine speaks protocol 2"}}"""
V2_REPLY = """{"v": 3, "id": req["id"], "op": "handshake", "ok": True, "result": {"protocol": 2, "new": True}}"""
V2_OTHER_SHAPE = """{"v": 3, "kind": "hello", "speaks": [2]}"""


@pytest.mark.parametrize("mode", [MISMATCH, NEWER, V2_REFUSAL, V2_REPLY, V2_OTHER_SHAPE])
def test_an_incompatible_engine_is_never_retried(tmp_path: Path, fake_time: FakeTime, mode: str) -> None:
    starts = tmp_path / "starts"
    script = COUNTING.replace("{MODE}", mode)
    command = EngineCommand(argv=(sys.executable, "-I", "-c", script, str(starts)), env=None)
    c = EngineClient(command, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        for _ in range(3):
            out = c.start()
            assert isinstance(out, EngineFailure)
            assert out.kind == "incompatible"
        assert starts.read_text() == "x"
        assert fake_time.pauses == []
        now = c.pid
        assert now is None or not alive(now)
    finally:
        c.close()


GARBAGE = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
sys.stdout.write(BAD + "\n")
sys.stdout.flush()
sys.stdin.read()
"""
NOT_A_REPLY = '"this is not a reply"'
DEEP_REPLY = '\'{"v":2,"id":%d,"op":"ping","ok":true,"result":{"x":%s%s}}\' % (req["id"], "[" * 200000, "]" * 200000)'


@pytest.mark.parametrize("bad", [NOT_A_REPLY, DEEP_REPLY])
def test_a_reply_that_breaks_the_protocol_is_a_failure_and_ends_the_engine(fake_time: FakeTime, bad: str) -> None:
    """Includes S0035 verifier F4(a): a reply nested deeply enough to exhaust the parser."""
    script = GARBAGE.replace("BAD", bad)
    c = EngineClient(stand_in(script), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        out = c.ping()
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert stopped(pid)
    finally:
        c.close()


def test_a_broken_handshake_in_our_own_protocol_is_a_failed_start_not_incompatible(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    """Only another `v` makes an engine incompatible; a malformed protocol-1 reply is a failed start, retried."""
    starts = tmp_path / "starts"
    broken = """{"v": 2, "id": req["id"], "ok": True}"""
    command = EngineCommand(
        argv=(sys.executable, "-I", "-c", COUNTING.replace("{MODE}", broken), str(starts)), env=None
    )
    c = EngineClient(command, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        kinds = [r.kind for r in (c.start() for _ in range(2)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed"]
        assert starts.read_text() == "xx"
    finally:
        c.close()


def _deep() -> object:
    deep: object = []
    for _ in range(200_000):
        deep = [deep]
    return deep


@pytest.mark.parametrize(
    ("op", "params"),
    [
        ("ping", {"x": _deep()}),  # S0035 verifier F4(b)
        ("ping", {"x": float("nan")}),
        ("ping", {"x": object()}),
        ("ping", {"x": "\ud800"}),
        ("x\udfff", {}),
    ],
    ids=["too-deep", "nan", "not-json", "surrogate-in-params", "surrogate-in-op"],
)
def test_a_request_that_is_not_json_data_raises_before_sending(
    client: EngineClient, op: str, params: dict[str, object]
) -> None:
    """S0035 independent verifier F7: the engine never saw it, so it is no `EngineRefusal`; params that are not
    JSON data are the caller's error (like a bad timeout) and raise. The engine is untouched."""
    client.start()
    pid = client.pid
    with pytest.raises(ValueError, match="not JSON data"):
        client.call(op, params)
    assert client.ping() == Pong()
    assert client.pid == pid


def test_a_request_longer_than_the_line_limit_is_not_sent_and_is_no_stop(client: EngineClient) -> None:
    """S0035 independent verifier F7: a too-large request depends on the data, so it is an outcome the caller
    handles, distinct from the engine's refusals; nothing is sent and it does not count towards the restart bound."""
    client.start()
    pid = client.pid
    big = {"x": "a" * (16 * 1024 * 1024)}
    for _ in range(RestartPolicy().max_crashes + 1):
        out = client.call("ping", big)
        assert isinstance(out, EngineFailure)
        assert out.kind == "too_large"
    assert client.ping() == Pong()
    assert client.pid == pid


@pytest.mark.parametrize(
    "policy",
    [
        {"delays": (0.5, float("inf"))},
        {"delays": (-1.0,)},
        {"delays": (float("nan"),)},
        {"window": float("inf")},
        {"window": 0.0},
        {"max_crashes": 0},
    ],
)
def test_a_restart_policy_without_finite_bounds_is_refused(policy: dict[str, Any]) -> None:
    """The backoff and its window are times the client waits; each must be finite (S0035 independent verifier F3)."""
    with pytest.raises(ValueError, match="RestartPolicy"):
        RestartPolicy(**policy)


def test_the_stderr_of_an_engine_that_failed_to_start_is_kept(fake_time: FakeTime) -> None:
    """S0035 independent verifier F4: the stderr tail is the only diagnosis of a crash; it outlives the run."""
    script = "import sys; sys.stderr.write('marker: cannot start\\n'); sys.stderr.flush(); sys.exit(3)"
    c = EngineClient(stand_in(script), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        out = c.start()
        assert isinstance(out, EngineFailure)
        assert c.pid is None
        assert "marker: cannot start" in c.stderr_tail()
    finally:
        c.close()


DIES_ON_PING = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
sys.stdin.readline()
sys.stderr.write("marker: died on ping\n")
sys.stderr.flush()
sys.exit(7)
"""


def test_the_stderr_of_an_engine_that_crashed_mid_request_is_kept_until_the_next_start(fake_time: FakeTime) -> None:
    c = EngineClient(stand_in(DIES_ON_PING), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        out = c.ping()
        assert isinstance(out, EngineFailure)
        assert out.kind == "crashed"
        assert "marker: died on ping" in c.stderr_tail()
        assert isinstance(c.start(), EngineInfo)  # a new run: its own tail
        assert "marker: died on ping" not in c.stderr_tail()
    finally:
        c.close()


def test_a_stop_exactly_one_window_old_no_longer_counts(fake_time: FakeTime) -> None:
    """The window is half-open: a stop `window` seconds ago has left it (S0035 independent verifier F8, M2)."""
    c = EngineClient(stand_in("import sys; sys.exit(3)"), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        first = fake_time.now
        kinds = [r.kind for r in (c.start() for _ in range(4)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed", "start_failed", "unavailable"]
        fake_time.now = first + RestartPolicy().window
        again = c.start()
        assert isinstance(again, EngineFailure)
        assert again.kind == "start_failed"  # two stops left in the window: a start was tried
    finally:
        c.close()


def test_an_engine_that_cannot_be_spawned_counts_towards_the_bound(fake_time: FakeTime) -> None:
    """S0035 independent verifier F8 (M9): a missing interpreter is a stop like any other, with the same backoff."""
    missing = EngineCommand(argv=(str(REPO / "no-such-dir" / "python"),), env=None)
    c = EngineClient(missing, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        kinds = [r.kind for r in (c.start() for _ in range(4)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed", "start_failed", "unavailable"]
        assert fake_time.pauses == [0.5, 2.0]
    finally:
        c.close()


def test_request_ids_count_from_1_and_wrap_below_the_largest_id() -> None:
    """S0035 independent verifier F8 (M14): ids stay in 1..MAX_ID, the range every JSON reader holds exactly."""
    assert next_request_id(0) == 1
    assert next_request_id(1) == 2
    assert next_request_id(MAX_ID - 1) == MAX_ID
    assert next_request_id(MAX_ID) == 1


def test_close_ends_the_engine_and_later_calls_fail(client: EngineClient) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    client.close()
    assert stopped(pid)
    out = client.ping()
    assert isinstance(out, EngineFailure)
    assert out.kind == "closed"
    client.close()  # closing twice is harmless


def test_the_engine_environment_drops_the_shell_token_and_python_overrides() -> None:
    environ = {
        "FT_SHELL_TOKEN": "secret",
        "FT_OTHER": "x",
        "PYTHONPATH": "/x",
        "PYTHONHOME": "/y",
        "ft_lower": "x",  # Windows names are case-insensitive (S0035 independent verifier F8, M4)
        "Python_Mixed": "x",
        "HOME": "/var/empty",
        "PATH": "/bin",
        "TZ": "Asia/Kolkata",
        "LANG": "C.UTF-8",
    }
    command = EngineCommand.for_venv(ENGINE_VENV, environ=environ)
    assert command.env == {
        "HOME": "/var/empty",
        "PATH": "/bin",
        "TZ": "Asia/Kolkata",
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "0",  # S0057: the one PYTHON* variable, set by us (engine-jobs §5.1)
    }


def test_the_engine_is_told_its_parent(client: EngineClient) -> None:
    """The engine watches the pid the client gives it, so a parent that dies during start-up is caught."""
    client.start()
    pid = client.pid
    assert pid is not None
    # -ww: procps on Linux cuts the column to the terminal width (80 without one); the manual CI run of
    # 2026-10-26 saw "... -m ledger_eng" with the flag cut off. macOS ps takes -ww too.
    args = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert f"--parent-pid {os.getpid()}" in args.stdout


def test_the_spawned_engine_is_told_its_engine_lock() -> None:
    """S0053, engine-jobs §5.1: every engine the product starts holds the engine lock, so the spawn names it."""
    lock = Path("/data/locks/engine.lock")
    with_lock = EngineCommand(argv=("python",), env=None, engine_lock=lock)
    assert with_lock.spawn_argv(7) == ["python", "--parent-pid", "7", "--engine-lock", str(lock)]
    assert EngineCommand(argv=("python",), env=None).spawn_argv(7) == ["python", "--parent-pid", "7"]


# -- The statechart in the client (S0040; ARCHITECTURE.md §7) ------------------------------------------------------


class Events:
    """An `EventSink` that records what it is given."""

    def __init__(self) -> None:
        self.seen: list[tuple[Event, dict[str, object]]] = []

    def __call__(self, event: Event, /, **fields: object) -> None:
        self.seen.append((event, fields))

    @property
    def names(self) -> list[str]:
        return [e.name for e, _ in self.seen]


def test_the_clients_state_follows_the_supervision_table(client: EngineClient) -> None:
    """S0040 brief: idle until the first request (lazy start), ready after the handshake, closed after close."""
    assert isinstance(client.state, Idle)
    assert isinstance(client.start(), EngineInfo)
    state = client.state
    assert isinstance(state, Ready)
    assert state.info == client.info
    assert state.pid == client.pid
    client.close()
    assert isinstance(client.state, Closed)


def test_the_lifecycle_is_logged_on_every_start_and_stop(fake_time: FakeTime) -> None:
    """S0040 brief: every start and stop is logged through the structured log's sink, not only the first start
    (S0038 logged only that one): ready, the stop between requests, ready again, closed."""
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        assert isinstance(c.start(), EngineInfo)
        first = c.pid
        assert first is not None
        kill(first)
        assert stopped(first)
        assert c.ping() == Pong()
        second = c.pid
    finally:
        c.close()
    assert events.names == ["engine.ready", "engine.stopped", "engine.ready", "engine.closed"]
    assert events.seen[0][1]["pid"] == first
    assert events.seen[1][1]["kind"] == "crashed"
    assert events.seen[2][1]["pid"] == second
    assert events.seen[2][1]["protocol"] == 2


def test_the_stop_that_reaches_the_bound_makes_the_client_unavailable_at_once(fake_time: FakeTime) -> None:
    """S0040 decision: `Unavailable` from the third stop on, logged once; later refusals log nothing more."""
    events = Events()
    c = EngineClient(
        stand_in("import sys; sys.exit(3)"), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events
    )
    try:
        for _ in range(3):
            out = c.start()
            assert isinstance(out, EngineFailure)
            assert out.kind == "start_failed"
        assert isinstance(c.state, Unavailable)
        assert events.names == ["engine.start_failed"] * 3 + ["engine.unavailable"]
        assert events.seen[3][1] == {"stops": 3}
        again = c.start()
        assert isinstance(again, EngineFailure)
        assert again.kind == "unavailable"
        assert len(events.seen) == 4
    finally:
        c.close()


def test_an_incompatible_engine_is_logged_once_as_a_failed_start(tmp_path: Path, fake_time: FakeTime) -> None:
    events = Events()
    command = EngineCommand(
        argv=(sys.executable, "-I", "-c", COUNTING.replace("{MODE}", NEWER), str(tmp_path / "starts")), env=None
    )
    c = EngineClient(command, monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        for _ in range(3):
            out = c.start()
            assert isinstance(out, EngineFailure)
            assert out.kind == "incompatible"
    finally:
        c.close()
    assert events.names == ["engine.start_failed", "engine.closed"]
    assert events.seen[0][1]["kind"] == "incompatible"


class Interrupting:
    """A pause that is interrupted the first time (the caller's thread got an exception mid-backoff)."""

    def __init__(self, time: FakeTime) -> None:
        self.time = time
        self.calls = 0

    def __call__(self, seconds: float) -> None:
        self.calls += 1
        if self.calls == 1:
            raise KeyboardInterrupt
        self.time.pause(seconds)


def test_an_interrupted_backoff_leaves_the_client_usable(fake_time: FakeTime) -> None:
    """S0040: an exception in the middle of a restart must not leave the statechart in a passing state, where every
    later request would be an `IllegalTransition`."""
    pause = Interrupting(fake_time)
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=pause)
    try:
        c.start()
        pid = c.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
        with pytest.raises(KeyboardInterrupt):
            c.ping()
        assert isinstance(c.state, Crashed)
        assert c.ping() == Pong()
    finally:
        c.close()


def test_an_exception_while_spawning_counts_as_a_failed_start_and_leaves_the_client_usable(
    fake_time: FakeTime,
) -> None:
    """`Popen` raises `ValueError` (not `OSError`) for an argument with a NUL byte; the error goes to the caller as
    before, and the client is left `Crashed` with the stop counted, not stuck in `Starting`."""
    bad = EngineCommand(argv=(sys.executable, "-c", "pass\x00"), env=None)
    c = EngineClient(bad, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        with pytest.raises(ValueError, match="null"):
            c.start()
        state = c.state
        assert isinstance(state, Crashed)
        assert state.last == "start_failed"
        assert len(state.stops) == 1
        assert c.pid is None
    finally:
        c.close()


WRONG_PONG = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "ping", "ok": True, "result": {"status": "no"}}) + "\n")
sys.stdout.flush()
sys.stdin.read()
"""


def test_a_ping_answered_with_the_wrong_result_is_a_violation_and_ends_the_engine(fake_time: FakeTime) -> None:
    """A well-formed reply whose result is not the ping's `{status: ok}` is an invalid reply (S0035: it stops the
    engine, its state is unknown); S0040 self-mutation C6."""
    c = EngineClient(stand_in(WRONG_PONG), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        out = c.ping()
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert stopped(pid)
        assert isinstance(c.state, Crashed)
    finally:
        c.close()


WRONG_INFO = """{"v": 2, "id": req["id"], "op": "handshake", "ok": True, "result": {"protocol": "one"}}"""


def test_a_handshake_with_the_wrong_result_is_a_failed_start_and_is_retried(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    """A protocol-1 reply whose result is not `EngineInfo` is a failed start, counted and retried, never an
    exception (S0035; S0040 self-mutation C13)."""
    starts = tmp_path / "starts"
    command = EngineCommand(
        argv=(sys.executable, "-I", "-c", COUNTING.replace("{MODE}", WRONG_INFO), str(starts)), env=None
    )
    c = EngineClient(command, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        kinds = [r.kind for r in (c.start() for _ in range(2)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed"]
        assert starts.read_text() == "xx"
        assert isinstance(c.state, Crashed)
    finally:
        c.close()


STALLS = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
sys.stdin.readline()
sys.stderr.write("marker: request read\n")
sys.stderr.flush()
sys.stdin.read()
"""


@pytest.mark.skipif(os.name != "posix", reason="raises SIGINT in the test process; Windows is the manual CI run")
def test_an_exception_while_waiting_for_a_reply_stops_the_engine_and_leaves_the_client_usable(
    fake_time: FakeTime,
) -> None:
    """The request is out and its reply may still come (S0035 verifier F4c): the engine is stopped, the exception goes
    on, and the table rests in `Crashed`, not in `Busy` (S0040 self-mutation C2). SIGINT, sent to this (the waiting)
    thread, stands in for any exception that reaches a call while its request is out (Ctrl-C, or SystemExit from a
    signal handler); where exactly it lands does not matter, every such place must end the same way."""
    c = EngineClient(stand_in(STALLS), monotonic=fake_time.monotonic, pause=fake_time.pause)
    waiting = threading.get_ident()
    done = threading.Event()

    def interrupt() -> None:
        if wait_until(lambda: "marker: request read" in c.stderr_tail()):
            signal.pthread_kill(waiting, signal.SIGINT)
        # The test's own watchdog: the call has no time limit, so a SIGINT that does not reach it would leave it
        # waiting for ever; end the stand-in instead and let the test fail.
        if not done.wait(DEADLINE):
            pid = c.pid
            if pid is not None:
                with contextlib.suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)

    # A shell starts a background job with SIGINT ignored; this test needs Python's own handler (found in S0057).
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        threading.Thread(target=interrupt, daemon=True).start()
        began = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            c.call("anything", {})
        done.set()
        assert time.monotonic() - began < 30.0, "the signal did not reach the wait (the test proves nothing then)"
        assert stopped(pid)
        state = c.state
        assert isinstance(state, Crashed)
        assert state.last == "crashed"
        assert isinstance(c.start(), EngineInfo)  # usable: a new engine starts
    finally:
        done.set()
        signal.signal(signal.SIGINT, previous)
        c.close()


def test_the_state_is_readable_at_once_while_a_request_is_in_flight(fake_time: FakeTime) -> None:
    """S0040 verifier F1: `state` is what /health and /api/boot will read, so it never waits behind a call in flight
    (which has no time limit since S0057), and it shows the passing state `Busy`."""
    c = EngineClient(stand_in(STALLS), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("anything", {}))
        assert wait_until(lambda: "marker: request read" in c.stderr_tail())
        began = time.monotonic()
        state = c.state
        info, pid = c.info, c.pid
        assert time.monotonic() - began < 1.0, "a read of the state waited for the call in flight"
        assert isinstance(state, Busy)
        assert info == state.info
        assert pid == state.pid
        assert t.is_alive(), "the call ended before the state was read: the test proves nothing then"
        assert pid is not None
        kill(pid)  # end the call at once: the stand-in never answers
        t.join(DEADLINE)
        assert len(out) == 1
    finally:
        c.close()


def test_an_engine_that_died_after_its_last_request_is_logged_as_a_stop_at_close(fake_time: FakeTime) -> None:
    """S0040 verifier F2 (brief: every start and stop is logged): close() notices the dead engine and logs the stop
    before the close."""
    events = Events()
    c = EngineClient(stand_in(STALLS), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    assert isinstance(c.start(), EngineInfo)
    pid = c.pid
    assert pid is not None
    kill(pid)
    assert stopped(pid)
    c.close()
    assert events.names == ["engine.ready", "engine.stopped", "engine.closed"]
    assert events.seen[1][1]["kind"] == "crashed"
    assert isinstance(c.state, Closed)


def test_closing_a_running_engine_logs_no_stop() -> None:
    """The must-not-pass beside F2's test: a live engine closed is a close, not a stop."""
    events = Events()
    c = EngineClient(stand_in(STALLS), log=events)
    assert isinstance(c.start(), EngineInfo)
    c.close()
    assert events.names == ["engine.ready", "engine.closed"]


# -- Cross-process failure modes (S0041; design/specs/failure-modes.md) ---------------------------------------------


class RaisingSink:
    """An `EventSink` that fails on every event, after recording its name."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, event: Event, /, **fields: object) -> None:
        self.names.append(event.name)
        msg = "the log sink is broken"
        raise RuntimeError(msg)


def test_a_log_sink_that_raises_changes_nothing_in_the_supervision(fake_time: FakeTime) -> None:
    """S0041 (S0040 carry-over): the log is diagnostics, so a sink that fails must not change what the client does.
    Before, the sink's exception reached the caller after the state had moved: a start returned no facts, and a crash
    mid-request raised instead of returning its typed failure. Every event still reaches the sink first."""
    sink = RaisingSink()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=sink)
    try:
        assert isinstance(c.start(), EngineInfo)  # engine.ready failed to log
        first = c.pid
        assert first is not None
        t, out = in_thread(lambda: c.call("test.stall", {}))
        assert wait_until(lambda: "stalling" in c.stderr_tail()), "the engine never started the request"
        kill(first)
        t.join(DEADLINE)
        assert not t.is_alive(), "the caller hung after the engine died"
        assert len(out) == 1, "the caller got an exception instead of its typed failure"
        failure = out[0]
        assert isinstance(failure, EngineFailure)
        assert failure.kind == "crashed"
        assert stopped(first)
        assert isinstance(c.state, Crashed)
        assert c.ping() == Pong()  # restarted
        assert c.pid not in (None, first)
    finally:
        c.close()  # engine.closed failed to log; close still returns
    assert isinstance(c.state, Closed)
    assert sink.names == ["engine.ready", "engine.stopped", "engine.ready", "engine.closed"]


class InterruptedSink:
    """An `EventSink` interrupted on `engine.ready` (Ctrl-C landing while the line is written)."""

    def __call__(self, event: Event, /, **fields: object) -> None:
        if event.name == "engine.ready":
            raise KeyboardInterrupt


def test_an_interrupt_inside_the_log_sink_still_reaches_the_caller(fake_time: FakeTime) -> None:
    """@regression-guard (S0041): only the sink's own failures are dropped; an interrupt goes on to the caller, as
    everywhere else in a call, and the client stays usable."""
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=InterruptedSink())
    try:
        with pytest.raises(KeyboardInterrupt):
            c.start()
        assert isinstance(c.state, Ready)
        assert c.ping() == Pong()
    finally:
        c.close()


# -- A large request (S0042; area review 2026-10-26 F3): the writer thread owns stdin, so Cancel can end it --------

LARGE = {"blob": "x" * (1 << 20)}  # 1 MiB: far beyond a pipe's buffer (16-64 KiB), well under the line limit


def frozen_until_released(pid: int, *threads: threading.Thread) -> list[threading.Thread]:
    """The threads still running after a hang detector's wait. It then releases the frozen engine (SIGCONT), so that
    on a client that hangs the test still ends and nothing is left stuck behind the call lock."""
    for t in threads:
        t.join(DEADLINE)
    hung = [t for t in threads if t.is_alive()]
    if hung:
        os.kill(pid, signal.SIGCONT)
        for t in hung:
            t.join(DEADLINE)
    return hung


STOPS_READING = r"""
import json, sys, threading
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
threading.Event().wait()
"""


HOLDS_OUTPUT = r"""
import json, subprocess, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
child = subprocess.Popen([sys.executable, "-I", "-c", "import threading; threading.Event().wait()"],
                         stdin=subprocess.DEVNULL)  # inherits the engine's stdout and stderr
sys.stderr.write(f"marker: child {child.pid}\n")
sys.stderr.flush()
sys.stdin.read()
"""


def test_a_large_request_to_a_reading_engine_is_answered(client: EngineClient) -> None:
    """@regression-guard (S0042): the must-not-pass beside the time limit on the write: a large request to an engine
    that reads is delivered whole and answered, and is no stop."""
    out = client.call("no.such.op", LARGE)
    assert isinstance(out, EngineRefusal)
    assert out.key == "protocol.unknown_op"
    pid = client.pid
    assert client.ping() == Pong()
    assert client.pid == pid


CLOSES_ITS_INPUT = r"""
import json, os, sys, threading
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
os.close(0)
sys.stderr.write("marker: input closed\n")
sys.stderr.flush()
threading.Event().wait()
"""


def test_a_request_the_engine_cannot_take_is_a_crash_at_once_not_a_timeout(fake_time: FakeTime) -> None:
    """@regression-guard (S0042): a write that fails (the engine closed its input but runs on, its output open) is
    reported by the writer at once as a crash; the caller does not wait out its limit for a reply that cannot come."""
    c = EngineClient(stand_in(CLOSES_ITS_INPUT), monotonic=fake_time.monotonic)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        assert wait_until(lambda: "marker: input closed" in c.stderr_tail())
        t, out = in_thread(lambda: c.call("anything", {}))
        t.join(DEADLINE)
        if t.is_alive():
            kill(pid)
            t.join(DEADLINE)
            pytest.fail("the caller waited for a reply to a request that was never taken")
        failure = out[0]
        assert isinstance(failure, EngineFailure)
        assert failure.kind == "crashed"
        assert stopped(pid)
    finally:
        c.close()


def engine_threads(before: set[threading.Thread]) -> list[str]:
    """The engine threads started since `before` that still run (another test's leftovers do not count)."""
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("engine-") and t not in before)


# -- No time limits, our own ends, the process group (S0057; engine-jobs §5.1-§5.5) ---------------------------------

STALLS_ON_READ = STALLS


def test_the_port_takes_no_time_limit() -> None:
    """§5.5: "`EngineClient.call` takes no timeout"; a slow job and a hung one look the same (§5.4)."""
    for fn in (EngineClient.call, EngineClient.ping, EngineClient.start, EnginePort.call, EnginePort.ping):
        assert "timeout" not in inspect.signature(fn).parameters, fn
    assert {f.name for f in dataclasses.fields(RestartPolicy)} == {"max_crashes", "window", "delays"}


def stall(c: EngineClient) -> tuple[threading.Thread, list[Any]]:
    t, out = in_thread(lambda: c.call("test.stall", {}))
    assert wait_until(lambda: "stalling" in c.stderr_tail()), "the engine never started the request"
    return t, out


def test_a_stalled_request_waits_until_it_is_cancelled(fake_time: FakeTime) -> None:
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        t, out = stall(c)
        t.join(ABSENCE)
        assert t.is_alive(), "a request ended without a Cancel: there is no time limit"
        assert isinstance(c.state, Busy)
        assert c.proceed() is False  # a request in flight with no gate pending: nothing is sent
        c.end("cancel")
        t.join(DEADLINE)
        assert not t.is_alive(), "Cancel did not end the call"
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
        assert stopped(pid)
        assert c.state == Idle(())
        assert c.ping() == Pong()  # at once: our end is no stop
        assert fake_time.pauses == []
    finally:
        c.close()
    assert "engine.stopped" not in events.names
    assert ("engine.ended", {"reason": "cancel"}) in [(e.name, f) for e, f in events.seen]


def test_three_cancels_in_a_row_leave_the_engine_startable_at_once(fake_time: FakeTime) -> None:
    """§14: "three Cancels in a row leave the engine `Ready`, not `Unavailable`"."""
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        for _ in range(3):
            t, out = stall(c)
            c.end("cancel")
            t.join(DEADLINE)
            assert isinstance(out[0], EngineFailure)
            assert out[0].kind == "cancelled"
        assert c.ping() == Pong()
        assert isinstance(c.state, Ready)
        assert c.state.stops == ()
        assert fake_time.pauses == []
    finally:
        c.close()


SILENT = "import sys; sys.stdin.read()"  # starts, never answers the handshake


def test_a_start_that_never_completes_is_ended_by_cancel(fake_time: FakeTime) -> None:
    """§5.5: "A start that never completes is ended by the waiting job's Cancel." No handshake time limit."""
    c = EngineClient(stand_in(SILENT), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        t, out = in_thread(c.start)
        assert wait_until(lambda: isinstance(c.state, Handshaking))
        t.join(ABSENCE)
        assert t.is_alive(), "the handshake was given up without a Cancel"
        pid = c.pid
        assert pid is not None
        c.end("cancel")
        t.join(DEADLINE)
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
        assert stopped(pid)
        assert c.state == Idle(())
    finally:
        c.close()


class CancelsInPause:
    """A backoff pause during which the waiting job's Cancel arrives."""

    def __init__(self, time: FakeTime) -> None:
        self.time = time
        self.client: EngineClient | None = None
        self.cancel = True

    def __call__(self, seconds: float) -> None:
        self.time.pause(seconds)
        if self.cancel and self.client is not None:
            self.client.end("cancel")


def test_a_cancel_during_the_backoff_ends_the_wait_and_starts_nothing(fake_time: FakeTime) -> None:
    pause = CancelsInPause(fake_time)
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=pause)
    pause.client = c
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
        out = c.ping()  # found dead (a stop), then the backoff, then the Cancel
        assert out == EngineFailure("cancelled", "the engine was ended by us (cancel)")
        assert c.pid is None, "an engine was started after the Cancel"
        assert fake_time.pauses == [0.5]
        pause.cancel = False
        assert c.ping() == Pong()
    finally:
        c.close()


def test_a_cancel_of_an_idle_engine_ends_it_and_is_no_stop(fake_time: FakeTime) -> None:
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        c.end("cancel")
        assert stopped(pid)
        assert c.ping() == Pong()
        assert c.pid not in (None, pid)
        assert fake_time.pauses == []
    finally:
        c.close()
    assert events.names == ["engine.ready", "engine.ended", "engine.ready", "engine.closed"]


def test_a_retire_ends_the_idle_engine_and_the_next_call_gets_a_fresh_one(fake_time: FakeTime) -> None:
    """§5.1: "An engine that ran user code is ended after its job, after its final reply; the next job gets a fresh
    engine"; §5.3: no stop."""
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        assert c.ping() == Pong()
        pid = c.pid
        assert pid is not None
        c.end("retire")
        assert c.state == Idle(())  # at once: no call was in flight
        assert stopped(pid)
        assert c.ping() == Pong()
        assert c.pid not in (None, pid)
        assert fake_time.pauses == []
    finally:
        c.close()
    assert [(e.name, f) for e, f in events.seen if e.name == "engine.ended"] == [("engine.ended", {"reason": "retire"})]


def test_a_retire_while_a_call_is_in_flight_ends_that_call_as_our_end_and_never_raises() -> None:
    """Our ends never raise (S0058 verifier F1: a retire that raised on the scheduler thread wedged the queue; S0057
    made `end` callable from any thread at any time). A retire during a call ends the engine as our end, like a
    Cancel: the call fails `cancelled`, no stop is counted, and the end report says `retire`."""
    ends = Ends()
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        t, out = stall(c)
        c.end("retire")
        t.join(DEADLINE)
        assert not t.is_alive()
        (failure,) = out
        assert isinstance(failure, EngineFailure)
        assert failure.kind == "cancelled"
        assert ends.wait(1)
        assert ends.reports[-1].reason == "retire"
        state = c.state
        assert isinstance(state, Idle)
        assert state.stops == ()
    finally:
        c.close()


def test_a_retire_while_another_public_call_holds_the_client_is_carried_out_when_it_returns() -> None:
    """The F1 window: a reset (Try again) holds the client's call lock while the scheduler retires the engine of the
    job that has just ended. The retire does not raise and the engine is ended once the reset returns."""
    ends = Ends()
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        assert isinstance(c.start(), EngineInfo)
        serial = c.serial
        with c._call():  # pyright: ignore[reportPrivateUsage] (the window of any public call, held open)
            c.end("retire")
        assert ends.wait(1)
        assert (ends.reports[-1].serial, ends.reports[-1].reason) == (serial, "retire")
        assert isinstance(c.state, Idle)
    finally:
        c.close()


def test_the_engine_leads_its_own_process_group(client: EngineClient) -> None:
    """§5.2: "Each engine leads its own process group", so our end can signal its plugin's children with it."""
    client.start()
    pid = client.pid
    assert pid is not None
    assert os.getpgid(pid) == pid
    assert os.getpgid(pid) != os.getpgrp()


def venv_site() -> Path:
    (site,) = (
        ENGINE_VENV.glob("lib/python*/site-packages") if os.name == "posix" else [ENGINE_VENV / "Lib/site-packages"]
    )
    return site


def test_the_development_command_is_s_S_B_P_with_the_venvs_site_and_never_I() -> None:
    """§5.1: "flags `-s -S -B -P ...`, never `-I`"; "the development form uses the same bootstrap with the virtual
    environment's site-packages, since `-S` keeps a venv's packages off the path"."""
    command = EngineCommand.for_venv(ENGINE_VENV, environ={})
    python = Path(command.argv[0])
    assert python.parent.parent == ENGINE_VENV
    assert command.argv[1:5] == ("-s", "-S", "-B", "-P")
    assert command.argv[5] == "-c"
    assert command.argv[7] == str(venv_site())
    assert "-I" not in command.argv
    assert "-E" not in command.argv
    assert EngineCommand.for_venv(ENGINE_VENV, environ={}, test_ops=True).argv[-1] == "--test-ops"


def test_the_engine_environment_sets_the_hash_seed() -> None:
    """§5.1: "Environment: the parent's, minus `FT_*` and every `PYTHON*` variable, plus `PYTHONHASHSEED=0`"."""
    command = EngineCommand.for_venv(ENGINE_VENV, environ={"PYTHONHASHSEED": "random", "HOME": "/h"})
    assert command.env == {"HOME": "/h", "PYTHONHASHSEED": "0"}


def interpreter(c: EngineClient) -> dict[str, Any]:
    out = c.call("test.interpreter", {})
    assert isinstance(out, dict), out
    return dict(out)


def test_two_engines_hash_a_string_alike_and_run_with_the_specs_flags() -> None:
    """§5.1: R4's digests must not depend on hash order: "two runs under `-s -S -B -P` [with PYTHONHASHSEED=0] the
    same"; `-B`: no `__pycache__` written into a user's folder (INV-30); `-I` would ignore the seed."""
    seen: list[dict[str, Any]] = []
    for _ in range(2):
        c = EngineClient(engine_command())
        try:
            seen.append(interpreter(c))
        finally:
            c.close()
    assert seen[0]["hash_abc"] == seen[1]["hash_abc"]
    for flags in seen:
        assert flags["no_site"] == 1
        assert flags["no_user_site"] == 1
        assert flags["safe_path"] is True
        assert flags["dont_write_bytecode"] is True
        assert flags["isolated"] == 0
        assert flags["ignore_environment"] == 0
        assert flags["hash_seed"] == "0"


class Ends:
    """`on_engine_ended`: each engine's end report, and what the test sees at the moment it arrives."""

    def __init__(self, probe: Callable[[], object] = lambda: None) -> None:
        self.reports: list[EngineEnd] = []
        self.seen_at_report: list[object] = []
        self._probe = probe
        self._got = threading.Condition()

    def __call__(self, end: EngineEnd) -> None:
        with self._got:
            self.seen_at_report.append(self._probe())
            self.reports.append(end)
            self._got.notify_all()

    def wait(self, n: int) -> bool:
        with self._got:
            return self._got.wait_for(lambda: len(self.reports) >= n, DEADLINE)


def lock_free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return not alive(pid)


@pytest.mark.skipif(os.name != "posix", reason="process groups and flock are POSIX; Windows is BL-0087")
@pytest.mark.parametrize("how", ["cancel", "crash of the leader alone"])
def test_a_plugins_child_holding_a_lock_is_ended_with_the_engine(tmp_path: Path, how: str) -> None:
    """§5.2: "a plugin's child processes go too and cannot keep the ledger-set locks"; the end is reported only once
    the group has no live member, so a next engine never starts beside a lock holder (§5.1)."""
    lock = tmp_path / "ledger.lock"
    child: list[int] = []
    ends = Ends(lambda: (lock_free(lock), bool(child) and gone(child[0])))
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        out = c.call("test.child", {"lock": str(lock)})
        assert isinstance(out, dict)
        child.append(int(out["pid"]))  # pyright: ignore[reportArgumentType]
        pid, serial = c.pid, c.serial
        assert pid is not None
        assert not lock_free(lock)
        if how == "cancel":
            c.end("cancel")
        else:
            kill(pid)
        assert ends.wait(1)
        assert ends.seen_at_report == [(True, True)], "the end was reported while the group still held the lock"
        (report,) = ends.reports
        assert (report.pid, report.serial) == (pid, serial)
        assert report.reason == ("cancel" if how == "cancel" else None)
        assert report.returncode == -signal.SIGKILL
    finally:
        c.close()
        if child and not gone(child[0]):
            kill(child[0])


class RecordingOps(PosixGroupOps):
    """The real group operations, recorded; `hold_reap` keeps the reap from returning until released."""

    def __init__(self, *, hold_reap: bool = False) -> None:
        self.calls: list[str] = []
        self.release = threading.Event()
        if not hold_reap:
            self.release.set()

    @override
    def kill_group(self, pgid: int) -> None:
        self.calls.append("kill")
        super().kill_group(pgid)

    @override
    def reap(self, pid: int) -> Reaped:
        self.calls.append("reap")
        self.release.wait()
        return super().reap(pid)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_no_signal_reaches_the_group_after_the_reap() -> None:
    """§5.2: "After the reap no signal is sent to that group: its id may already belong to another program." A later
    Cancel, retire or close of the same engine sends nothing."""
    ops = RecordingOps()
    ends = Ends()
    c = EngineClient(engine_command(), group_ops=ops, on_engine_ended=ends)
    assert isinstance(c.start(), EngineInfo)
    c.end("cancel")
    assert ends.wait(1)
    after = ops.calls[ops.calls.index("reap") + 1 :]
    c.end("cancel")
    c.close()
    assert ops.calls[ops.calls.index("reap") + 1 :] == after == []


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_close_returns_without_waiting_for_the_reap() -> None:
    """§5.5: "`close()` ... signals the engine's group once and returns without waiting for the reap" (§14: "`close()`
    and Quit anyway return while a fake port's reap never returns")."""
    ops = RecordingOps(hold_reap=True)
    c = EngineClient(engine_command(), group_ops=ops)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        closer = threading.Thread(target=c.close, daemon=True)
        closer.start()
        closer.join(DEADLINE)
        assert not closer.is_alive(), "close() waited for the reap"
        assert isinstance(c.state, Closed)
        assert wait_until(lambda: "reap" in ops.calls)
        assert ops.calls.count("kill") >= 1
    finally:
        ops.release.set()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_quit_anyway_returns_while_the_reap_never_returns() -> None:
    ops = RecordingOps(hold_reap=True)
    c = EngineClient(engine_command(), group_ops=ops)
    try:
        t, out = stall(c)
        c.end("quit_anyway")
        t.join(DEADLINE)
        assert not t.is_alive(), "Quit anyway waited for the reap"
        assert out == [EngineFailure("cancelled", "the engine was ended by us (quit_anyway)")]
    finally:
        ops.release.set()
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_a_new_engine_starts_only_after_the_previous_ones_group_is_drained() -> None:
    """§5.1: "A new engine is spawned when a job needs one, only after the previous one is reaped and its process
    group has no live member" (`AtMostOneEngine`)."""
    ops = RecordingOps(hold_reap=True)
    c = EngineClient(engine_command(), group_ops=ops)
    try:
        assert isinstance(c.start(), EngineInfo)
        first = c.pid
        c.end("cancel")
        t, out = in_thread(c.ping)
        t.join(ABSENCE)
        assert t.is_alive(), "a second engine started before the first was reaped"
        assert c.pid is None
        ops.release.set()
        t.join(DEADLINE)
        assert out == [Pong()]
        assert c.pid not in (None, first)
    finally:
        ops.release.set()
        c.close()


def test_close_returns_at_once_while_a_call_to_a_hung_engine_is_in_flight() -> None:
    """§9.3 step 4: an engine stuck on a dead disk cannot hold the quit. Before S0057 close() waited behind the call
    lock for the call's time limit; now there is no limit, so it must not take that lock."""
    c = EngineClient(engine_command())
    t, out = stall(c)
    closer = threading.Thread(target=c.close, daemon=True)
    closer.start()
    closer.join(DEADLINE)
    assert not closer.is_alive(), "close() waited for the call in flight"
    t.join(DEADLINE)
    assert out == [EngineFailure("closed", "the engine client was closed")]
    assert isinstance(c.state, Closed)
    later = c.ping()
    assert isinstance(later, EngineFailure)
    assert later.kind == "closed"


@pytest.mark.skipif(os.name != "posix", reason="SIGSTOP is POSIX; Windows is the manual CI run")
def test_cancel_ends_a_large_request_to_a_frozen_engine(fake_time: FakeTime) -> None:
    """S0042's writer thread stays (§5.5 bullet 5): "it lets Cancel end an engine in the middle of a large request".
    A frozen engine (SIGSTOP: its own watchdog frozen too) reads nothing; the Cancel's SIGKILL reaches it."""
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        os.kill(pid, signal.SIGSTOP)
        t, out = in_thread(lambda: c.call("no.such.op", LARGE))
        assert wait_until(lambda: isinstance(c.state, Busy))
        c.end("cancel")
        assert frozen_until_released(pid, t) == [], "the call did not end while the engine was frozen"
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
        assert stopped(pid)
        assert c.ping() == Pong()
    finally:
        c.close()


def test_cancel_ends_a_large_request_to_an_engine_that_stops_reading(fake_time: FakeTime) -> None:
    c = EngineClient(stand_in(STOPS_READING), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        t, out = in_thread(lambda: c.call("anything", LARGE))
        t.join(ABSENCE)
        assert t.is_alive()
        c.end("cancel")
        t.join(DEADLINE)
        assert not t.is_alive(), "Cancel did not end a call blocked in its write"
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
        assert stopped(pid)
    finally:
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_close_ends_a_child_of_the_engine_that_holds_its_output() -> None:
    """S0042 left such a child (and its pipe threads) behind; it is in the engine's group now, so the close's group
    signal ends it, and with it the pipes."""
    ends = Ends()
    c = EngineClient(stand_in(HOLDS_OUTPUT), on_engine_ended=ends)
    assert isinstance(c.start(), EngineInfo)
    assert wait_until(lambda: "marker: child " in c.stderr_tail())
    grandchild = int(c.stderr_tail().split("marker: child ")[1].split()[0])
    try:
        c.close()
        assert ends.wait(1)
        assert gone(grandchild), "the engine's child outlived the close"
    finally:
        if not gone(grandchild):
            kill(grandchild)


def test_a_stopped_engine_leaves_no_thread_behind() -> None:
    """S0042, kept: no engine thread or pipe outlives its engine once it is drained, so restarts do not pile them up
    (INV-10, INV-11). Since S0057 the drain runs on the engine's own reaper thread."""
    before = set(threading.enumerate())
    ends = Ends()
    c = EngineClient(engine_command(), on_engine_ended=ends)
    c.start()
    assert engine_threads(before) == ["engine-reaper", "engine-replies", "engine-requests", "engine-stderr"]
    run = c._run  # pyright: ignore[reportPrivateUsage] (the pipes have no public handle)
    assert run is not None
    pipes = (run.proc.stdin, run.proc.stdout, run.proc.stderr)
    t, _ = stall(c)
    c.end("cancel")
    t.join(DEADLINE)
    assert ends.wait(1)
    assert wait_until(lambda: engine_threads(before) == [])
    assert wait_until(lambda: all(p is not None and p.closed for p in pipes))
    assert c.ping() == Pong()
    c.close()
    assert ends.wait(2)
    assert wait_until(lambda: engine_threads(before) == [])


# -- The protocol's job messages (S0057; engine-jobs §4, §5.4, §6.1 steps 7 and 9, §12) --------------------------------


def bounded_call(c: EngineClient, op: str, params: dict[str, Any], on_event: Callable[[EngineEvent], None]) -> object:
    """`c.call` with the test's own watchdog: the client waits without a time limit, so a check it misses (a message
    out of order, a violation) would leave the call waiting for ever. After `DEADLINE` the engine's group is ended and
    the test fails, naming what hung."""
    t, out = in_thread(lambda: c.call(op, params, on_event=on_event))
    t.join(DEADLINE)
    if t.is_alive():
        pid = c.pid
        if pid is not None:
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
        t.join(DEADLINE)
        pytest.fail(f"the call to {op} was still waiting after {DEADLINE} s: the client missed what should end it")
    return out[0]


class Job:
    """`on_event` for one call: records every event; at the gate it proceeds, cancels, or does nothing."""

    def __init__(self, c: EngineClient, at_gate: str = "proceed") -> None:
        self.c = c
        self.at_gate = at_gate
        self.events: list[EngineEvent] = []
        self.proceeded: list[bool] = []

    def __call__(self, event: EngineEvent) -> None:
        self.events.append(event)
        if isinstance(event, GateReached):
            if self.at_gate == "proceed":
                self.proceeded.append(self.c.proceed())
                self.proceeded.append(self.c.proceed())  # a second proceed for one gate sends nothing
            elif self.at_gate == "cancel":
                self.c.end("cancel")
                self.proceeded.append(self.c.proceed())  # the Cancel won the commit-point step: nothing to proceed


def test_progress_gate_proceed_committed_and_the_decision_reach_the_caller(tmp_path: Path) -> None:
    target = tmp_path / "written"
    c = EngineClient(engine_command())
    try:
        job = Job(c)
        out = bounded_call(c, "test.gate", {"path": str(target)}, job)
        assert out == {"status": "ok"}
        assert job.events == [
            Progress("checking_change"),
            GateReached(),
            Committed(),
            Progress("updating_views"),
            Decision("test.gate", 1, {"wrote": True}, "committed"),
        ]
        assert job.proceeded == [True, False]
        assert target.read_text(encoding="utf-8") == "post"
        assert c.ping() == Pong()  # the engine and the stream are in step afterwards
    finally:
        c.close()


def test_a_cancel_at_the_gate_ends_the_engine_before_anything_is_written(tmp_path: Path) -> None:
    """§4: "In one step the scheduler either ends the engine (a Cancel is pending: outcome `cancelled`, disk
    unchanged) or ... sends `proceed`."""
    target = tmp_path / "written"
    ends = Ends()
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        job = Job(c, at_gate="cancel")
        out = bounded_call(c, "test.gate", {"path": str(target)}, job)
        assert out == EngineFailure("cancelled", "the engine was ended by us (cancel)")
        assert GateReached() in job.events
        assert Committed() not in job.events
        assert job.proceeded == [False]
        assert ends.wait(1)
        assert not target.exists(), "the engine passed its gate without proceed"
    finally:
        c.close()


def test_proceed_with_no_gate_pending_sends_nothing(client: EngineClient) -> None:
    """A `proceed` the engine is not waiting for would be read as a request and answered, putting the stream out of
    step: it is never sent."""
    assert client.proceed() is False  # no engine
    assert client.ping() == Pong()
    assert client.proceed() is False  # an idle engine
    assert client.ping() == Pong()


MISBEHAVES_AT_THE_GATE = r"""
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
req = json.loads(sys.stdin.readline())
send({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
      "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}})
req = json.loads(sys.stdin.readline())
rid, op = req["id"], req["op"]
MODE
"""
COMMITTED_FIRST = """send({"v": 2, "id": rid, "op": op, "event": "committed"})
sys.stdin.read()"""
GATE_TWICE_BEFORE_PROCEED = """send({"v": 2, "id": rid, "op": op, "event": "gate"})
send({"v": 2, "id": rid, "op": op, "event": "gate"})
sys.stdin.read()"""


REPLY_WITHOUT_PROCEED = """send({"v": 2, "id": rid, "op": op, "event": "gate"})
send({"v": 2, "id": rid, "op": op, "ok": True, "result": {}})
sys.stdin.read()"""


@pytest.mark.parametrize(
    "mode",
    [COMMITTED_FIRST, GATE_TWICE_BEFORE_PROCEED, REPLY_WITHOUT_PROCEED],
    ids=["committed-first", "gate-twice", "reply-while-the-gate-waits"],
)
def test_a_job_message_out_of_order_before_the_commit_point_is_a_violation(fake_time: FakeTime, mode: str) -> None:
    """`committed` says the save landed (§6.1 step 9): before `proceed` it cannot be true. Before the commit point a
    violation ends the engine like any invalid reply (§5.3)."""
    c = EngineClient(
        stand_in(MISBEHAVES_AT_THE_GATE.replace("MODE", mode)), monotonic=fake_time.monotonic, pause=fake_time.pause
    )
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        out = bounded_call(c, "save", {}, Job(c, at_gate="nothing"))
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert stopped(pid)
        assert isinstance(c.state, Crashed)
    finally:
        c.close()


BREAKS_AFTER_PROCEED = r"""send({"v": 2, "id": rid, "op": op, "event": "gate"})
assert json.loads(sys.stdin.readline())["control"] == "proceed"
sys.stdout.write(BAD + "\n")
sys.stdout.flush()
for _ in range(1000):
    sys.stdout.write("more garbage after the violation\n")
sys.stdout.flush()
sys.stdin.read()  # returns at the end of input
open(sys.argv[1], "w").write("finished")
"""


@pytest.mark.parametrize(
    "bad",
    ['"not a reply"', 'json.dumps({"v": 2, "id": rid, "op": op, "event": "gate"})'],
    ids=["garbage", "a-second-gate"],
)
def test_a_violation_after_the_commit_point_closes_the_input_and_never_signals_the_engine(
    tmp_path: Path, fake_time: FakeTime, bad: str
) -> None:
    """§5.3: "a reply that breaks the protocol after the commit point: close the engine's input, read and discard every
    further byte, wait for the exit ...; counted as a stop at the exit". The stand-in finishes its request (it marks
    the end) only because nothing signalled it."""
    marker = tmp_path / "finished"
    script = MISBEHAVES_AT_THE_GATE.replace("MODE", BREAKS_AFTER_PROCEED.replace("BAD", bad))
    events = Events()
    c = EngineClient(
        EngineCommand(argv=(sys.executable, "-I", "-c", script, str(marker)), env=None),
        monotonic=fake_time.monotonic,
        pause=fake_time.pause,
        log=events,
    )
    try:
        assert isinstance(c.start(), EngineInfo)
        out = bounded_call(c, "save", {}, Job(c))
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert "after the commit point" in out.detail
        assert marker.read_text() == "finished", "the engine was ended past its commit point"
        state = c.state
        assert isinstance(state, Crashed)
        assert len(state.stops) == 1
    finally:
        c.close()
    assert [f["kind"] for e, f in events.seen if e.name == "engine.stopped"] == ["protocol_violation"]


# -- The bytecode directory (§5.1) ------------------------------------------------------------------------------------


def bytecode_command(directory: Path) -> EngineCommand:
    return EngineCommand.for_venv(ENGINE_VENV, test_ops=True, bytecode_dir=directory)


def test_the_bytecode_directory_is_filled_once_then_used(tmp_path: Path) -> None:
    """§5.1: "filled once per build with the bytecode of the engine's interpreter library, site-packages and code,
    read-only afterwards"; engines use it with `-X pycache_prefix`."""
    directory = tmp_path / "engine" / "bytecode" / "build"
    c = EngineClient(bytecode_command(directory))
    try:
        assert interpreter(c)["pycache_prefix"] is None  # not filled yet: started without it, the fill begun
        assert wait_until(lambda: (directory / ".complete").is_file(), 120.0), "the fill never completed"
        assert list(directory.rglob("server.cpython-*.pyc")), "the engine's own code was not compiled"
        c.end("retire")
        assert interpreter(c)["pycache_prefix"] == str(directory)
    finally:
        c.close()


def test_an_engine_that_fails_to_start_with_the_directory_gets_it_deleted_and_starts_once_without_it(
    tmp_path: Path,
) -> None:
    """§5.1: "A damaged `.pyc` under `pycache_prefix` raises instead of falling back to the source ... If an engine
    fails to start while using the directory, the directory is deleted and the engine is started once without it".
    Not a stop: the engine is fine."""
    directory = tmp_path / "bytecode"
    events = Events()
    c = EngineClient(bytecode_command(directory), log=events)
    try:
        c.start()
        assert wait_until(lambda: (directory / ".complete").is_file(), 120.0), "the fill never completed"
        c.end("retire")
        (damaged,) = [p for p in directory.rglob("server.cpython-*.pyc") if "ledger_engine" in str(p)]
        damaged.write_bytes(damaged.read_bytes()[:16] + b"\x00garbage")
        assert isinstance(c.start(), EngineInfo)
        state = c.state
        assert isinstance(state, Ready)
        assert state.stops == ()
        assert not (directory / ".complete").exists()
        assert interpreter(c)["pycache_prefix"] is None
    finally:
        c.close()
    assert ("engine.bytecode", "discarded") in [(e.name, f.get("outcome")) for e, f in events.seen]
    assert "engine.start_failed" not in events.names


ESCAPES_WITH_OUTPUT = r"""
import json, subprocess, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
sys.stdin.readline()
child = subprocess.Popen([sys.executable, "-I", "-c", "import threading; threading.Event().wait()"],
                         stdin=subprocess.DEVNULL, start_new_session=True)  # leaves the group, keeps our stdout
sys.stderr.write(f"marker: escaped {child.pid}\n")
sys.stderr.flush()
sys.stdin.read()
"""


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_a_cancel_ends_the_call_even_when_an_escaped_child_keeps_the_output_open() -> None:
    """§15: "A plugin child that leaves the engine's process group (`setsid`) is not ended with the engine". It must
    still not hold the user's Cancel: once the engine is drained, the call ends (it would wait for an end of output
    that the escaped child never gives)."""
    c = EngineClient(stand_in(ESCAPES_WITH_OUTPUT))
    escaped: int | None = None
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("anything", {}))
        assert wait_until(lambda: "marker: escaped " in c.stderr_tail())
        escaped = int(c.stderr_tail().split("marker: escaped ")[1].split()[0])
        c.end("cancel")
        t.join(DEADLINE)
        assert not t.is_alive(), "a Cancel waited for an output an escaped child holds open"
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
    finally:
        if escaped is not None and not gone(escaped):
            kill(escaped)
        c.close()


class Unendable(PosixGroupOps):
    """A process our signal cannot end (stuck in the kernel on a dead disk, §5.2): the group signal does nothing."""

    @override
    def kill_group(self, pgid: int) -> None:
        pass


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_close_returns_while_a_call_waits_on_an_engine_our_signal_cannot_end() -> None:
    """§9.3 step 4: "an engine stuck on a dead disk cannot hold the quit". Here the call in flight never ends, so a
    `close()` that waited for the call (its lock) would never return."""
    c = EngineClient(engine_command(), group_ops=Unendable())
    t, _ = stall(c)
    pid = c.pid
    assert pid is not None
    try:
        closer = threading.Thread(target=c.close, daemon=True)
        closer.start()
        closer.join(DEADLINE)
        assert not closer.is_alive(), "close() waited for a call that cannot end"
        assert t.is_alive()  # the stuck call is still there: nothing could end it
    finally:
        os.killpg(pid, signal.SIGKILL)  # the test ends what the fake could not
        t.join(DEADLINE)


# -- S0057 verifier round 1 --------------------------------------------------------------------------------------------

SLOW_WRITE = r"""
import json, sys, time
def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()
req = json.loads(sys.stdin.readline())
send({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
      "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}})
req = json.loads(sys.stdin.readline())
rid, op = req["id"], req["op"]
send({"v": 2, "id": rid, "op": op, "event": "gate"})
assert json.loads(sys.stdin.readline())["control"] == "proceed"
open(sys.argv[1] + ".half", "w").write("first file replaced")
time.sleep(1)  # in the middle of the replace
open(sys.argv[1], "w").write("second file replaced")
send({"v": 2, "id": rid, "op": op, "event": "committed"})
send({"v": 2, "id": rid, "op": op, "ok": True, "result": {}})
sys.stdin.read()
"""


@pytest.mark.parametrize("how", ["cancel", "close"])
def test_a_cancel_or_close_past_the_commit_point_lets_the_job_finish(tmp_path: Path, how: str) -> None:
    """R1-F1 (INV-01; §4: "a writing job past its commit point: nothing: the job finishes"; "Quit anyway is the one
    case where we end a writing job past its commit point"). After `proceed()` neither the user's Cancel nor a close
    may cut the replace."""
    marker = tmp_path / "m"
    c = EngineClient(EngineCommand(argv=(sys.executable, "-I", "-c", SLOW_WRITE, str(marker)), env=None))
    try:
        assert isinstance(c.start(), EngineInfo)
        events: list[EngineEvent] = []

        def on_event(event: EngineEvent) -> None:
            events.append(event)
            if isinstance(event, GateReached):
                assert c.proceed()

        t, out = in_thread(lambda: c.call("save", {}, on_event=on_event))
        assert wait_until(lambda: (tmp_path / "m.half").exists())
        if how == "cancel":
            c.end("cancel")
        else:
            closer = threading.Thread(target=c.close, daemon=True)
            closer.start()
            closer.join(DEADLINE)
            assert not closer.is_alive(), "close() waited for the job"
        t.join(DEADLINE)
        assert not t.is_alive()
        assert out == [{}], "the job past its commit point did not finish"
        assert marker.read_text() == "second file replaced"
        assert Committed() in events
        if how == "close":
            assert wait_until(lambda: isinstance(c.state, Closed))
    finally:
        c.close()


def test_quit_anyway_ends_a_job_past_its_commit_point(tmp_path: Path) -> None:
    """§4, §9.3: Quit anyway ends a running job "before or after its commit point" (its journal covers the cut)."""
    marker = tmp_path / "m"
    c = EngineClient(EngineCommand(argv=(sys.executable, "-I", "-c", SLOW_WRITE, str(marker)), env=None))
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("save", {}, on_event=Job(c)))
        assert wait_until(lambda: (tmp_path / "m.half").exists())
        c.end("quit_anyway")
        t.join(DEADLINE)
        assert out == [EngineFailure("cancelled", "the engine was ended by us (quit_anyway)")]
        assert not marker.exists()
    finally:
        c.close()


CRASHES_WITH_ESCAPED_CHILD = r"""
import json, os, subprocess, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
child = subprocess.Popen([sys.executable, "-I", "-c", "import threading; threading.Event().wait()"],
                         stdin=subprocess.DEVNULL, start_new_session=True)  # leaves the group, keeps our stdout
sys.stderr.write(f"marker: escaped {child.pid}\n")
sys.stderr.flush()
MODE
"""
DIES = "os._exit(1)  # the engine ends by itself in the middle of the request"
ANSWERS_THEN_DIES = """sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": req["op"], "ok": True,
    "result": {"answered": True}}) + "\\n")
sys.stdout.flush()
os._exit(0)"""


def escaped_pid(c: EngineClient) -> int:
    assert wait_until(lambda: "marker: escaped " in c.stderr_tail())
    return int(c.stderr_tail().split("marker: escaped ")[1].split()[0])


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_an_engine_that_ends_by_itself_while_an_escaped_child_holds_its_output_ends_the_call() -> None:
    """R1-F2 (§5.3: "A crash is noticed by the engine's exit"; ADR-0063 decision 1: every wait has an exit): the end of
    output never comes while a child that left the group holds it, so the reaped engine must end the call itself."""
    c = EngineClient(stand_in(CRASHES_WITH_ESCAPED_CHILD.replace("MODE", DIES)))
    escaped: int | None = None
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("anything", {}))
        escaped = escaped_pid(c)
        t.join(DEADLINE)
        assert not t.is_alive(), "the crash of an engine whose output an escaped child holds was never noticed"
        assert isinstance(out[0], EngineFailure)
        assert out[0].kind == "crashed"
    finally:
        if escaped is not None and not gone(escaped):
            kill(escaped)
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_a_reply_written_just_before_the_engine_ends_reaches_the_caller() -> None:
    """@regression-guard (S0057 verifier R1 note): the reaped engine ends the stream only after everything it
    wrote, so its last reply is never lost behind the end (the ordering the reaper relies on), also with an escaped
    child holding the output."""
    for _ in range(5):  # a race would show up across runs
        c = EngineClient(stand_in(CRASHES_WITH_ESCAPED_CHILD.replace("MODE", ANSWERS_THEN_DIES)))
        escaped: int | None = None
        try:
            assert isinstance(c.start(), EngineInfo)
            t, out = in_thread(lambda c=c: c.call("anything", {}))
            escaped = escaped_pid(c)
            t.join(DEADLINE)
            assert not t.is_alive()
            assert out == [{"answered": True}]
        finally:
            if escaped is not None and not gone(escaped):
                kill(escaped)
            c.close()


GARBAGE_AFTER_HANDSHAKE = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 2, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
sys.stdout.write('"not a reply"\n')
sys.stdout.flush()
sys.stdin.read()
"""


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_every_end_we_cause_is_reported_with_its_reason() -> None:
    """R1-F3 (§5.2: "The reason is recorded before the signal"; §5.3: a SIGKILL we did not cause is
    `killed_by_system`): the end report tells our kills from the OS's. A violation before the commit point is ended
    by the table (`stop`); a crash is the engine's own (None)."""
    ends = Ends()
    c = EngineClient(stand_in(GARBAGE_AFTER_HANDSHAKE), on_engine_ended=ends)
    try:
        assert isinstance(c.start(), EngineInfo)
        out = c.call("x", {})
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert ends.wait(1)
        assert ends.reports[0].reason == "stop"
    finally:
        c.close()
    ends = Ends()
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        kill(pid)  # not us
        assert ends.wait(1)
        assert ends.reports[0].reason is None
        assert ends.reports[0].returncode == -signal.SIGKILL
    finally:
        c.close()


BREAKS_AFTER_PROCEED_THEN_ENDS = MISBEHAVES_AT_THE_GATE.replace(
    "MODE",
    """send({"v": 2, "id": rid, "op": op, "event": "gate"})
json.loads(sys.stdin.readline())
sys.stdout.write("garbage\\n")
sys.stdout.flush()
sys.stdin.read()""",
)


def test_the_end_after_a_violation_past_the_commit_point_is_reported_as_our_closed_input() -> None:
    """R1-F3: the engine that ends at the end of the input we closed (§5.3) ends by our doing: `input_closed`."""
    ends = Ends()
    c = EngineClient(stand_in(BREAKS_AFTER_PROCEED_THEN_ENDS), on_engine_ended=ends)
    try:
        assert isinstance(c.start(), EngineInfo)
        out = bounded_call(c, "save", {}, Job(c))
        assert isinstance(out, EngineFailure)
        assert ends.wait(1)
        assert ends.reports[0].reason == "input_closed"
    finally:
        c.close()


def test_a_close_while_a_call_is_leaving_still_reaches_closed() -> None:
    """R1-F4: `close()` cannot take the call lock while a call is leaving (its exit settles the table holding the
    lock); the close must still be settled once that call has left."""
    events = Events()
    c = EngineClient(engine_command(), log=events)
    real = c._settle  # pyright: ignore[reportPrivateUsage] (the interleaving is forced at this one point)
    in_window, closed = threading.Event(), threading.Event()
    armed = [False]

    def settle() -> None:
        real()
        if armed[0] and threading.current_thread().name == "caller" and not c._in_call:  # pyright: ignore[reportPrivateUsage]
            armed[0] = False
            in_window.set()
            closed.wait(DEADLINE)  # the leaving call still holds the lock

    c._settle = settle  # type: ignore[method-assign]
    try:
        assert isinstance(c.start(), EngineInfo)
        armed[0] = True
        t = threading.Thread(target=c.ping, name="caller")
        t.start()
        assert in_window.wait(DEADLINE)
        c.close()
        closed.set()
        t.join(DEADLINE)
        assert isinstance(c.state, Closed)
        assert "engine.closed" in events.names
    finally:
        closed.set()


PLUGIN = "VALUE = 42\n"


@pytest.mark.skipif(os.name != "posix", reason="the bytecode check reads folders; Windows is the manual CI run")
def test_a_plugin_imported_from_a_user_folder_writes_no_pycache(tmp_path: Path) -> None:
    """R1-F5 (INV-30; §5.1: `-B`, "A user's plugin is compiled from its source on every load and never cached"): an
    import from a user's folder through the real spawn leaves no `__pycache__` there, and nothing in the bytecode
    directory either."""
    folder = tmp_path / "ledger"
    folder.mkdir()
    (folder / "ft_test_plugin.py").write_text(PLUGIN, encoding="utf-8")
    bytecode = tmp_path / "bytecode"
    c = EngineClient(EngineCommand.for_venv(ENGINE_VENV, test_ops=True, bytecode_dir=bytecode))
    try:
        assert c.call("test.import_plugin", {"folder": str(folder)}) == {"value": 42}
        assert not list(folder.rglob("__pycache__")), "the engine wrote bytecode into the user's folder"
        assert not list(folder.rglob("*.pyc"))
        assert not [p for p in bytecode.rglob("*.pyc") if "ft_test_plugin" in p.name]
    finally:
        c.close()


def test_a_failed_bytecode_fill_is_logged_and_never_marked_complete(tmp_path: Path) -> None:
    """@regression-guard (S0057 verifier R1-F6; §5.1: "marked complete last"): a fill that fails leaves no
    `.complete`, so no engine uses a half-filled directory."""
    directory = tmp_path / "bytecode"
    command = EngineCommand.for_venv(ENGINE_VENV, test_ops=True, bytecode_dir=directory)
    assert command.bytecode is not None
    failing = dataclasses.replace(
        command, bytecode=dataclasses.replace(command.bytecode, fill_argv=(sys.executable, "-c", "raise SystemExit(3)"))
    )
    events = Events()
    c = EngineClient(failing, log=events)
    try:
        assert interpreter(c)["pycache_prefix"] is None
        assert wait_until(lambda: "engine.bytecode" in events.names)
        assert [f["outcome"] for e, f in events.seen if e.name == "engine.bytecode"] == ["fill_failed"]
        assert not (directory / ".complete").exists()
        c.end("retire")
        assert interpreter(c)["pycache_prefix"] is None
    finally:
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="the reply pump selects on pipes; Windows is BL-0087")
def test_once_reaped_the_reply_pump_hands_on_what_the_engine_left_before_it_ends_the_stream() -> None:
    """The ordering the reaper relies on (S0057 verifier R1 note), pinned without a race: the engine's last reply is
    already in the pipe when the reaper wakes the pump; the pump hands it on, then ends the stream, although the
    pipe is still held open (by an escaped child, here by the test)."""
    out_read, out_write = os.pipe()
    wake_read, wake_write = os.pipe()
    os.write(out_write, b'{"v":2,"id":1,"op":"x","ok":true,"result":{}}\n{"partial"')
    os.write(wake_write, b"x")  # reaped before the pump ran: both are ready at once
    replies: queue.Queue[object] = queue.Queue()
    try:
        t = threading.Thread(
            target=_pump_lines_until_reaped,
            args=(os.fdopen(out_read, "rb"), replies, wake_read),
            daemon=True,
        )
        t.start()
        t.join(DEADLINE)
        assert not t.is_alive(), "the pump waited for an end of output that the held pipe never gives"
        got = [replies.get_nowait() for _ in range(replies.qsize())]
        assert got == [b'{"v":2,"id":1,"op":"x","ok":true,"result":{}}\n', b'{"partial"', None]
    finally:
        os.close(out_write)
        os.close(wake_write)


class SlowToNotice(PosixGroupOps):
    """The reaper is late: it does not see the leader's exit until released."""

    def __init__(self) -> None:
        self.release = threading.Event()

    @override
    def wait_leader(self, pid: int) -> None:
        self.release.wait()
        super().wait_leader(pid)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows is BL-0087")
def test_an_engine_dead_between_requests_is_noticed_before_its_reaper_sees_it(fake_time: FakeTime) -> None:
    """Found under load in S0057's fix round: the next call took a dead idle engine for a live one when its reaper
    had not yet seen the exit, and failed `crashed` instead of starting a new engine (S0035: "an engine found dead
    between requests ... the request goes on"). The client asks the OS itself, without waiting."""
    ops = SlowToNotice()
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, group_ops=ops, log=events)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        kill(pid)
        assert wait_until(lambda: not alive(pid))  # a zombie: dead, not reaped
        t, out = in_thread(c.ping)
        assert wait_until(lambda: not isinstance(c.state, Ready)), "the dead engine was taken for a live one"
        ops.release.set()
        t.join(DEADLINE)
        assert out == [Pong()]
        assert [f["kind"] for e, f in events.seen if e.name == "engine.stopped"] == ["crashed"]
    finally:
        ops.release.set()
        c.close()


# -- S0057 verifier round 2 -------------------------------------------------------------------------------------------


def gated(c: EngineClient) -> Callable[[EngineEvent], None]:
    def on_event(event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            assert c.proceed()

    return on_event


SECOND_REQUEST = SLOW_WRITE.replace(
    "sys.stdin.read()",
    "req = json.loads(sys.stdin.readline())\n"
    "send({'v': 2, 'id': req['id'], 'op': req['op'], 'ok': True, 'result': {'second': 'ran'}})\n"
    "sys.stdin.read()",
)


def test_a_cancel_at_the_start_of_the_next_call_after_a_proceeded_job_is_not_lost(tmp_path: Path) -> None:
    """R2-F2 (§4: past the commit point is the job's, not the engine's; §5.2): the previous job's commit point must
    not outlive it. A Cancel of the next call, between its start and its request, ends that call."""
    c = EngineClient(EngineCommand(argv=(sys.executable, "-I", "-c", SECOND_REQUEST, str(tmp_path / "m")), env=None))
    try:
        assert isinstance(c.start(), EngineInfo)
        assert bounded_call(c, "save", {}, gated(c)) == {}
        real_feed = c._feed  # pyright: ignore[reportPrivateUsage] (the interleaving is forced at this one point)

        def feed(event: SupervisionEvent) -> object:
            if isinstance(event, Send):  # the second call is in flight, its request not yet handed over
                canceller = threading.Thread(target=lambda: c.end("cancel"))
                canceller.start()
                canceller.join(DEADLINE)
            return real_feed(event)

        c._feed = feed  # type: ignore[method-assign]
        out = bounded_call(c, "second", {}, lambda _e: None)
        assert out == EngineFailure("cancelled", "the engine was ended by us (cancel)")
    finally:
        c.close()


@pytest.mark.parametrize("how", ["retire", "cancel", "close"])
def test_after_a_proceeded_job_an_idle_end_still_ends_the_engine(tmp_path: Path, how: str) -> None:
    """@regression-guard (S0057 verifier R2-F3; §5.1: "An engine that ran user code is ended after its job"): the
    commit point belongs to its request; once the job has finished, a retire, Cancel or close of the idle engine ends
    it, and the next call gets a fresh engine."""
    ends = Ends()
    c = EngineClient(
        EngineCommand(argv=(sys.executable, "-I", "-c", SECOND_REQUEST, str(tmp_path / "m")), env=None),
        on_engine_ended=ends,
    )
    try:
        assert isinstance(c.start(), EngineInfo)
        assert c.serial == 1
        assert bounded_call(c, "save", {}, gated(c)) == {}
        if how == "close":
            c.close()
            assert isinstance(c.state, Closed)
        else:
            c.end(how)  # type: ignore[arg-type]
            assert c.state == Idle(())
        assert ends.wait(1)
        assert ends.reports[0].reason == how
        if how != "close":
            assert isinstance(c.start(), EngineInfo)
            assert c.serial == 2
    finally:
        c.close()


def test_a_close_past_the_commit_point_is_reported_as_our_close(tmp_path: Path) -> None:
    """R2-F1 (the end report, R1-F3): a close during a job past its commit point is settled after the job; the end it
    causes is our `close`, not a failure's `stop`."""
    ends = Ends()
    c = EngineClient(
        EngineCommand(argv=(sys.executable, "-I", "-c", SLOW_WRITE, str(tmp_path / "m")), env=None),
        on_engine_ended=ends,
    )
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("save", {}, on_event=gated(c)))
        assert wait_until(lambda: (tmp_path / "m.half").exists())
        c.close()
        t.join(DEADLINE)
        assert out == [{}]
        assert wait_until(lambda: isinstance(c.state, Closed))
        assert ends.wait(1)
        assert ends.reports[0].reason == "close"
    finally:
        c.close()


def test_the_line_splitter_is_linear() -> None:
    """R2-F4: a 16 MiB reply in 16 KiB pipe chunks is split in time linear in its size (the old `readline` was); the
    first splitter rescanned and copied the buffer on every chunk."""
    lines_queue: queue.Queue[object] = queue.Queue()
    lines = _Lines(lines_queue)  # pyright: ignore[reportArgumentType]
    chunk = b"a" * 16384
    n = (16 * 1024 * 1024 - 1024) // len(chunk)
    began = time.perf_counter()
    for _ in range(n):
        lines.feed(chunk)
    lines.feed(b"\n")
    split = time.perf_counter() - began
    began = time.perf_counter()
    buf = bytearray()
    for _ in range(n):
        buf += chunk
    accumulate = time.perf_counter() - began
    assert lines_queue.qsize() == 1
    assert split < 10 * accumulate + 0.05, (split, accumulate)


def test_many_small_lines_in_one_chunk_are_split_in_order() -> None:
    """@regression-guard (R2-F4): the linear splitter keeps the lines, their order and a last partial line."""
    lines_queue: queue.Queue[object] = queue.Queue()
    lines = _Lines(lines_queue)  # pyright: ignore[reportArgumentType]
    lines.feed(b"".join(b"%d\n" % i for i in range(5000)) + b"tail")
    lines.end()
    got = [lines_queue.get_nowait() for _ in range(lines_queue.qsize())]
    assert got == [b"%d\n" % i for i in range(5000)] + [b"tail", None]


@pytest.mark.skipif(os.name != "posix", reason="the reply pump runs on POSIX; Windows is BL-0087")
def test_the_reply_pump_reads_a_descriptor_above_fd_setsize() -> None:
    """R2-F5: `select.select` refuses a descriptor at or above FD_SETSIZE (1024), which a sidecar with many open files
    can reach; the pump must still read the live engine's reply."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < 1600:
        pytest.skip("the hard limit on open files is below the test's descriptors")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 2048), hard))
    out_read, out_write = os.pipe()
    wake_read, wake_write = os.pipe()
    high_out, high_wake = os.dup2(out_read, 1500), os.dup2(wake_read, 1501)
    os.close(out_read)
    os.close(wake_read)
    replies: queue.Queue[object] = queue.Queue()
    try:
        t = threading.Thread(
            target=_pump_lines_until_reaped, args=(os.fdopen(high_out, "rb"), replies, high_wake), daemon=True
        )
        t.start()
        os.write(out_write, b'{"v":2,"id":1,"op":"x","ok":true,"result":{}}\n')
        assert replies.get(timeout=DEADLINE) == b'{"v":2,"id":1,"op":"x","ok":true,"result":{}}\n'
        os.write(wake_write, b"x")
        t.join(DEADLINE)
        assert replies.get_nowait() is None
    finally:
        os.close(out_write)
        os.close(wake_write)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


# -- the scheduler's seams (S0058; engine-jobs §5.1, §5.3) -------------------------------------------------------


class Spawns:
    """`on_engine_spawned`: each spawn's serial, and whether its end was already reported when it arrived."""

    def __init__(self, ends: Ends) -> None:
        self.serials: list[int] = []
        self.reported_before: list[bool] = []
        self._ends = ends

    def __call__(self, serial: int) -> None:
        self.serials.append(serial)
        self.reported_before.append(any(r.serial == serial for r in self._ends.reports))


def test_each_spawn_is_reported_before_its_end() -> None:
    """S0058: the scheduler names each engine by the queue's number of the `Start` it serves (§5.3), so it must learn
    every engine the client spawns, and before that engine's end can be reported."""
    ends = Ends()
    spawns = Spawns(ends)
    c = EngineClient(engine_command(), on_engine_spawned=spawns, on_engine_ended=ends)
    try:
        assert isinstance(c.start(), EngineInfo)
        first = c.serial
        c.end("retire")
        assert isinstance(c.start(), EngineInfo)
        assert ends.wait(1)
        assert spawns.serials == [first, c.serial]
        assert spawns.reported_before == [False, False]
    finally:
        c.close()


def test_the_bytecode_retry_reports_both_spawns(tmp_path: Path) -> None:
    """§5.1: one start that fails with the directory and starts once without it is two engines; both are reported."""
    directory = tmp_path / "bytecode"
    ends = Ends()
    spawns = Spawns(ends)
    c = EngineClient(bytecode_command(directory), on_engine_spawned=spawns, on_engine_ended=ends)
    try:
        c.start()
        assert wait_until(lambda: (directory / ".complete").is_file(), 120.0), "the fill never completed"
        c.end("retire")
        (damaged,) = [p for p in directory.rglob("server.cpython-*.pyc") if "ledger_engine" in str(p)]
        damaged.write_bytes(damaged.read_bytes()[:16] + b"\x00garbage")
        before = len(spawns.serials)
        assert isinstance(c.start(), EngineInfo)
        assert len(spawns.serials) == before + 2
        assert spawns.serials[-1] == c.serial
    finally:
        c.close()


@pytest.mark.parametrize("how", ["died", "cancelled", "retired", "never started", "closed"])
def test_a_call_that_may_not_spawn_fails_when_no_engine_is_ready(how: str) -> None:
    """S0058: `call(..., spawn=False)` is the scheduler's call inside a job: the job's engine is the one its `Start`
    gave it (§5.1, §5.3: "The crashed job is not run again by itself"), so with no ready engine the call fails and
    starts nothing; only the queue decides about a new engine."""
    ends = Ends()
    spawns = Spawns(ends)
    c = EngineClient(engine_command(), on_engine_spawned=spawns, on_engine_ended=ends)
    try:
        expected = {"died": "crashed", "cancelled": "cancelled", "retired": "cancelled", "never started": "cancelled"}
        if how != "never started":
            assert isinstance(c.start(), EngineInfo)
        if how == "died":
            pid = c.pid
            assert pid is not None
            kill(pid)
            assert ends.wait(1)
        elif how == "cancelled":
            c.end("cancel")
        elif how == "retired":
            c.end("retire")
        elif how == "closed":
            c.close()
        count = len(spawns.serials)
        out = c.call("ping", {}, spawn=False)
        assert isinstance(out, EngineFailure)
        assert out.kind == expected.get(how, "closed")
        assert len(spawns.serials) == count, "a call that may not spawn started an engine"
    finally:
        c.close()


def test_a_call_that_may_not_spawn_uses_the_ready_engine() -> None:
    c = EngineClient(engine_command())
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert c.call("ping", {}, spawn=False) == {"status": "ok"}
        assert c.pid == pid
    finally:
        c.close()
