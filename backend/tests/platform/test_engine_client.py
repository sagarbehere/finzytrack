"""The engine client against the real engine process (ADR-0054 decision 4; S0035 brief; EP-07, EP-13).

The real engine runs from `engine/.venv` (sync it with `uv sync --locked` in `engine/`). A few cases need an engine
that misbehaves in one exact way; those run a small stand-in script instead. Backoff is checked with an injected
monotonic clock and pause, so no test waits for real backoff time (EP-28).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.platform.adapters.engine_client import EngineClient, EngineCommand, join_all, next_request_id
from app.platform.engine import EngineFailure, EngineInfo, EnginePort, EngineRefusal, Pong
from app.platform.engine_protocol import MAX_ID
from app.platform.engine_supervision import Busy, Closed, Crashed, Idle, Ready, RestartPolicy, Unavailable
from app.platform.observability import Event

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 20.0


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
    assert info == EngineInfo(protocol=1, engine_version=engine_version(), beancount_version="3.2.0")
    assert client.pid is not None
    assert alive(client.pid)


def test_ping_starts_the_engine_when_needed(client: EngineClient) -> None:
    assert client.pid is None
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid is not None


def test_a_refusal_comes_back_as_a_refusal(client: EngineClient) -> None:
    out = client.call("no.such.op", {}, timeout=DEADLINE)
    assert isinstance(out, EngineRefusal)
    assert out.key == "protocol.unknown_op"
    assert client.ping(timeout=DEADLINE) == Pong()  # a refusal does not end the engine


def test_an_engine_killed_mid_request_is_a_failure_not_a_hang(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    first = client.pid
    assert first is not None
    t, out = in_thread(lambda: client.call("test.stall", {}, timeout=60.0))
    assert wait_until(lambda: "stalling" in client.stderr_tail()), "the engine never started the request"
    kill(first)
    t.join(DEADLINE)
    assert not t.is_alive(), "the caller hung after the engine died"
    assert len(out) == 1
    failure = out[0]
    assert isinstance(failure, EngineFailure)
    assert failure.kind == "crashed"
    # The next request restarts the engine, after the first backoff delay.
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid not in (None, first)
    assert fake_time.pauses == [0.5]


def test_a_request_is_never_retried_after_a_crash(client: EngineClient) -> None:
    """A write is not idempotent: the caller decides; the client never resends (S0035 decision)."""
    client.start()
    pid = client.pid
    assert pid is not None
    t, out = in_thread(lambda: client.call("test.stall", {}, timeout=60.0))
    assert wait_until(lambda: "stalling" in client.stderr_tail())
    kill(pid)
    t.join(DEADLINE)
    assert isinstance(out[0], EngineFailure)
    assert client.pid is None  # no new engine was started, so nothing was resent on the caller's behalf


def test_a_stalled_request_times_out_and_the_engine_is_killed(client: EngineClient) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    began = time.monotonic()
    out = client.call("test.stall", {}, timeout=0.5)
    assert time.monotonic() - began < 10.0, "the caller's timeout was not honoured"  # S0035 mutant C13
    assert isinstance(out, EngineFailure)
    assert out.kind == "timeout"
    assert stopped(pid), "the stalled engine was left running"
    assert client.ping(timeout=DEADLINE) == Pong()


def test_an_engine_that_died_between_requests_is_restarted(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    kill(pid)
    assert stopped(pid)
    assert client.ping(timeout=DEADLINE) == Pong()
    assert fake_time.pauses == [0.5]


def test_restarts_are_bounded_then_allowed_again_after_the_window(client: EngineClient, fake_time: FakeTime) -> None:
    for _ in range(3):
        assert client.ping(timeout=DEADLINE) == Pong()
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    out = client.ping(timeout=DEADLINE)
    assert isinstance(out, EngineFailure)
    assert out.kind == "unavailable"
    now = client.pid
    assert now is None or not alive(now)
    assert fake_time.pauses == [0.5, 2.0]
    fake_time.now += RestartPolicy().window
    assert client.ping(timeout=DEADLINE) == Pong()


def test_reset_allows_a_restart_at_once(client: EngineClient) -> None:
    for _ in range(3):
        client.ping(timeout=DEADLINE)
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    assert isinstance(client.ping(timeout=DEADLINE), EngineFailure)
    client.reset()
    assert client.ping(timeout=DEADLINE) == Pong()


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
MISMATCH = """{"v": 1, "id": req["id"], "op": "handshake", "ok": False,
         "error": {"key": "protocol.version_mismatch", "message": "this engine speaks protocol 2"}}"""
NEWER = """{"v": 1, "id": req["id"], "op": "handshake", "ok": True,
         "result": {"protocol": 2, "engine_version": "9", "beancount_version": "3.2.0"}}"""
# S0035 verifier F2: a real protocol-2 engine stamps its own version on every reply, in a shape this side cannot
# know; the client must still recognise it by `v` and call it incompatible.
V2_REFUSAL = """{"v": 2, "id": req["id"], "op": "handshake", "ok": False,
         "error": {"key": "protocol.version_mismatch", "message": "this engine speaks protocol 2"}}"""
V2_REPLY = """{"v": 2, "id": req["id"], "op": "handshake", "ok": True, "result": {"protocol": 2, "new": True}}"""
V2_OTHER_SHAPE = """{"v": 2, "kind": "hello", "speaks": [2]}"""


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
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
sys.stdout.write(BAD + "\n")
sys.stdout.flush()
sys.stdin.read()
"""
NOT_A_REPLY = '"this is not a reply"'
DEEP_REPLY = '\'{"v":1,"id":%d,"op":"ping","ok":true,"result":{"x":%s%s}}\' % (req["id"], "[" * 200000, "]" * 200000)'


@pytest.mark.parametrize("bad", [NOT_A_REPLY, DEEP_REPLY])
def test_a_reply_that_breaks_the_protocol_is_a_failure_and_ends_the_engine(fake_time: FakeTime, bad: str) -> None:
    """Includes S0035 verifier F4(a): a reply nested deeply enough to exhaust the parser."""
    script = GARBAGE.replace("BAD", bad)
    c = EngineClient(stand_in(script), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        out = c.ping(timeout=DEADLINE)
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
    broken = """{"v": 1, "id": req["id"], "ok": True}"""
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
        client.call(op, params, timeout=DEADLINE)
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid == pid


def test_a_request_longer_than_the_line_limit_is_not_sent_and_is_no_stop(client: EngineClient) -> None:
    """S0035 independent verifier F7: a too-large request depends on the data, so it is an outcome the caller
    handles, distinct from the engine's refusals; nothing is sent and it does not count towards the restart bound."""
    client.start()
    pid = client.pid
    big = {"x": "a" * (16 * 1024 * 1024)}
    for _ in range(RestartPolicy().max_crashes + 1):
        out = client.call("ping", big, timeout=DEADLINE)
        assert isinstance(out, EngineFailure)
        assert out.kind == "too_large"
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid == pid


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_a_timeout_that_is_not_a_positive_number_is_refused_before_sending(
    client: EngineClient, timeout: float
) -> None:
    """S0035 verifier F4(c): a bad timeout raises before anything is written, so no reply is left queued to be
    mistaken for the next request's."""
    client.start()
    pid = client.pid
    with pytest.raises(ValueError, match="timeout"):
        client.call("ping", {}, timeout=timeout)
    with pytest.raises(ValueError, match="timeout"):
        client.ping(timeout=timeout)
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid == pid


@pytest.mark.parametrize(
    "policy",
    [
        {"start_timeout": float("inf")},
        {"start_timeout": 0.0},
        {"start_timeout": float("nan")},
        {"close_timeout": float("inf")},
        {"close_timeout": -1.0},
        {"delays": (0.5, float("inf"))},
        {"delays": (-1.0,)},
        {"delays": (float("nan"),)},
        {"window": float("inf")},
        {"window": 0.0},
        {"max_crashes": 0},
    ],
)
def test_a_restart_policy_without_finite_bounds_is_refused(policy: dict[str, Any]) -> None:
    """S0035 independent verifier F3: starting is bounded by the policy, not by the caller's timeout, so every
    time in the policy must be finite."""
    with pytest.raises(ValueError, match="RestartPolicy"):
        RestartPolicy(**policy)


def test_the_longest_start_is_the_largest_backoff_plus_the_start_timeout() -> None:
    assert RestartPolicy().longest_start == 8.0 + 15.0
    assert RestartPolicy(delays=(), start_timeout=2.0).longest_start == 2.0


SILENT = "import sys; sys.stdin.read()"  # starts, never answers the handshake


def test_a_start_is_bounded_by_the_policy_not_by_the_callers_timeout(fake_time: FakeTime) -> None:
    """S0035 independent verifier F3 (decided: the caller's timeout covers the wait for its reply; a start that the
    call needs is bounded by `RestartPolicy.longest_start`). A short timeout must not cut a cold start short: that
    would stop a healthy engine that is still importing and count it towards `unavailable`."""
    policy = RestartPolicy(start_timeout=1.0)
    c = EngineClient(stand_in(SILENT), policy, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        began = time.monotonic()
        out = c.ping(timeout=0.2)
        took = time.monotonic() - began
        assert isinstance(out, EngineFailure)
        assert out.kind == "start_failed"
        assert 1.0 <= took < policy.longest_start + 5.0
    finally:
        c.close()


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
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
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
        out = c.ping(timeout=DEADLINE)
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
    out = client.ping(timeout=DEADLINE)
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
    assert command.env == {"HOME": "/var/empty", "PATH": "/bin", "TZ": "Asia/Kolkata", "LANG": "C.UTF-8"}


def test_the_engine_is_told_its_parent(client: EngineClient) -> None:
    """The engine watches the pid the client gives it, so a parent that dies during start-up is caught."""
    client.start()
    pid = client.pid
    assert pid is not None
    # -ww: procps on Linux cuts the column to the terminal width (80 without one); the manual CI run of
    # 2026-10-26 saw "... -m ledger_eng" with the flag cut off. macOS ps takes -ww too.
    args = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert f"--parent-pid {os.getpid()}" in args.stdout


def test_the_engine_command_runs_the_venv_interpreter_isolated() -> None:
    command = EngineCommand.for_venv(ENGINE_VENV, environ={})
    assert Path(command.argv[0]).parent.parent == ENGINE_VENV
    assert command.argv[1:] == ("-I", "-m", "ledger_engine")
    assert EngineCommand.for_venv(ENGINE_VENV, environ={}, test_ops=True).argv[-1] == "--test-ops"


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
        assert c.ping(timeout=DEADLINE) == Pong()
        second = c.pid
    finally:
        c.close()
    assert events.names == ["engine.ready", "engine.stopped", "engine.ready", "engine.closed"]
    assert events.seen[0][1]["pid"] == first
    assert events.seen[1][1]["kind"] == "crashed"
    assert events.seen[2][1]["pid"] == second
    assert events.seen[2][1]["protocol"] == 1


def test_a_timeout_is_logged_as_a_stop_of_kind_timeout(fake_time: FakeTime) -> None:
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        out = c.call("test.stall", {}, timeout=0.5)
        assert isinstance(out, EngineFailure)
        assert out.kind == "timeout"
    finally:
        c.close()
    assert events.names == ["engine.ready", "engine.stopped", "engine.closed"]
    assert events.seen[1][1]["kind"] == "timeout"


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
            c.ping(timeout=DEADLINE)
        assert isinstance(c.state, Crashed)
        assert c.ping(timeout=DEADLINE) == Pong()
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
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "ping", "ok": True, "result": {"status": "no"}}) + "\n")
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
        out = c.ping(timeout=DEADLINE)
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert stopped(pid)
        assert isinstance(c.state, Crashed)
    finally:
        c.close()


WRONG_INFO = """{"v": 1, "id": req["id"], "op": "handshake", "ok": True, "result": {"protocol": "one"}}"""


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
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
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

    def interrupt() -> None:
        if wait_until(lambda: "marker: request read" in c.stderr_tail()):
            signal.pthread_kill(waiting, signal.SIGINT)

    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        threading.Thread(target=interrupt, daemon=True).start()
        began = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            c.call("anything", {}, timeout=60.0)
        assert time.monotonic() - began < 30.0, "the signal did not reach the wait (the test proves nothing then)"
        assert stopped(pid)
        state = c.state
        assert isinstance(state, Crashed)
        assert state.last == "crashed"
        assert isinstance(c.start(), EngineInfo)  # usable: a new engine starts
    finally:
        c.close()


def test_the_state_is_readable_at_once_while_a_request_is_in_flight(fake_time: FakeTime) -> None:
    """S0040 verifier F1: `state` is what /health and /api/boot will read, so it never waits behind a call in flight
    (that wait can be `longest_start` plus the call's timeout), and it shows the passing state `Busy`."""
    c = EngineClient(stand_in(STALLS), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        t, out = in_thread(lambda: c.call("anything", {}, timeout=DEADLINE))
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
        t, out = in_thread(lambda: c.call("test.stall", {}, timeout=60.0))
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
        assert c.ping(timeout=DEADLINE) == Pong()  # restarted
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
        assert c.ping(timeout=DEADLINE) == Pong()
    finally:
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="SIGSTOP is POSIX; Windows is the manual CI run")
def test_a_frozen_engine_times_out_is_ended_and_replaced(client: EngineClient) -> None:
    """@regression-guard (S0041, failure-modes row E2): an engine frozen by the OS (SIGSTOP: its own watchdog is frozen
    too) is a hang. The caller's timeout notices it, the client ends the frozen process (SIGKILL reaches a stopped
    process), and the next request starts a new engine."""
    client.start()
    pid = client.pid
    assert pid is not None
    os.kill(pid, signal.SIGSTOP)
    out = client.ping(timeout=0.5)
    assert isinstance(out, EngineFailure)
    assert out.kind == "timeout"
    assert stopped(pid), "the frozen engine was left behind"
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid not in (None, pid)


# -- Sending a request has a time limit too (S0042; area review 2026-10-26 F3) --------------------------------------

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


@pytest.mark.skipif(os.name != "posix", reason="SIGSTOP is POSIX; Windows is the manual CI run")
def test_a_large_request_to_a_frozen_engine_ends_within_its_limit_and_ends_the_engine(fake_time: FakeTime) -> None:
    """Area review 2026-10-26 F3 (failure-modes row E2): the request was written with no time limit, so a request
    larger than the pipe's buffer to an engine that does not read never returned, holding the lock. The caller's
    limit covers the write: the call returns its timeout while the engine is still frozen, and the engine is ended."""
    events = Events()
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause, log=events)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        os.kill(pid, signal.SIGSTOP)
        t, out = in_thread(lambda: c.call("no.such.op", LARGE, timeout=0.5))
        assert frozen_until_released(pid, t) == [], "the call did not return while the engine was frozen"
        failure = out[0]
        assert isinstance(failure, EngineFailure)
        assert failure.kind == "timeout"
        assert stopped(pid), "the frozen engine was left behind"
        assert [f.get("kind") for e, f in events.seen if e.name == "engine.stopped"] == ["timeout"]
        assert c.ping(timeout=DEADLINE) == Pong()
        assert c.pid not in (None, pid)
    finally:
        c.close()


STOPS_READING = r"""
import json, sys, threading
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
threading.Event().wait()
"""


def test_a_large_request_to_an_engine_that_stops_reading_times_out(fake_time: FakeTime) -> None:
    """The same hang without a signal (every OS): an engine stuck after its handshake reads nothing more."""
    c = EngineClient(stand_in(STOPS_READING), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        t, out = in_thread(lambda: c.call("anything", LARGE, timeout=0.5))
        t.join(DEADLINE)
        if t.is_alive():
            kill(pid)  # release the hung call, so the test ends
            t.join(DEADLINE)
            pytest.fail("the call did not return while the engine was not reading")
        failure = out[0]
        assert isinstance(failure, EngineFailure)
        assert failure.kind == "timeout"
        assert stopped(pid)
        assert isinstance(c.state, Crashed)
    finally:
        c.close()


@pytest.mark.skipif(os.name != "posix", reason="SIGSTOP is POSIX; Windows is the manual CI run")
def test_close_returns_while_a_large_request_to_a_frozen_engine_is_out(fake_time: FakeTime) -> None:
    """Area review F3: quit (close) waited behind the hung call's lock forever. Now it waits at most for that call's
    limit and its stop."""
    c = EngineClient(engine_command(), monotonic=fake_time.monotonic, pause=fake_time.pause)
    assert isinstance(c.start(), EngineInfo)
    pid = c.pid
    assert pid is not None
    os.kill(pid, signal.SIGSTOP)
    t, out = in_thread(lambda: c.call("no.such.op", LARGE, timeout=0.5))
    assert wait_until(lambda: isinstance(c.state, Busy))
    closer = threading.Thread(target=c.close, daemon=True)
    closer.start()
    assert frozen_until_released(pid, t, closer) == [], "close() or the call hung on the frozen engine"
    failure = out[0]
    assert isinstance(failure, EngineFailure)
    assert failure.kind == "timeout"
    assert isinstance(c.state, Closed)
    assert stopped(pid)


HOLDS_OUTPUT = r"""
import json, subprocess, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
child = subprocess.Popen([sys.executable, "-I", "-c", "import threading; threading.Event().wait()"],
                         stdin=subprocess.DEVNULL)  # inherits the engine's stdout and stderr
sys.stderr.write(f"marker: child {child.pid}\n")
sys.stderr.flush()
sys.stdin.read()
"""


@pytest.mark.skipif(os.name != "posix", reason="ends a grandchild by pid; Windows is the manual CI run")
def test_close_returns_when_a_child_of_the_engine_holds_its_output() -> None:
    """S0042 (same class as F3): a process the engine started (a user's plugin, say) keeps the engine's output pipes
    open after the engine exits, so the client's readers never see their end. Closing a pipe while its reader is
    inside `readline` waits for that reader, so close() hung after the joins timed out. Each reader now closes its own
    pipe when it ends; close() leaves a pipe that is still being read alone."""
    c = EngineClient(stand_in(HOLDS_OUTPUT), RestartPolicy(close_timeout=0.5))
    grandchild: int | None = None
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        assert wait_until(lambda: "marker: child " in c.stderr_tail())
        grandchild = int(c.stderr_tail().split("marker: child ")[1].split()[0])
        closer = threading.Thread(target=c.close, daemon=True)
        closer.start()
        closer.join(DEADLINE)
        hung = closer.is_alive()
        if hung:
            kill(grandchild)  # releases the readers, so the test ends
            grandchild = None
            closer.join(DEADLINE)
        assert not hung, "close() hung on a pipe held open by the engine's child"
        assert isinstance(c.state, Closed)
        assert stopped(pid)
    finally:
        if grandchild is not None:
            kill(grandchild)


def test_a_large_request_to_a_reading_engine_is_answered(client: EngineClient) -> None:
    """@regression-guard (S0042): the must-not-pass beside the time limit on the write: a large request to an engine
    that reads is delivered whole and answered, and is no stop."""
    out = client.call("no.such.op", LARGE, timeout=DEADLINE)
    assert isinstance(out, EngineRefusal)
    assert out.key == "protocol.unknown_op"
    pid = client.pid
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid == pid


CLOSES_ITS_INPUT = r"""
import json, os, sys, threading
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
os.close(0)
sys.stderr.write("marker: input closed\n")
sys.stderr.flush()
threading.Event().wait()
"""


def test_a_request_the_engine_cannot_take_is_a_crash_at_once_not_a_timeout(fake_time: FakeTime) -> None:
    """@regression-guard (S0042): a write that fails (the engine closed its input but runs on, its output open) is
    reported by the writer at once as a crash; the caller does not wait out its limit for a reply that cannot come."""
    c = EngineClient(stand_in(CLOSES_ITS_INPUT), RestartPolicy(close_timeout=0.5), monotonic=fake_time.monotonic)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        assert wait_until(lambda: "marker: input closed" in c.stderr_tail())
        t, out = in_thread(lambda: c.call("anything", {}, timeout=600.0))
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


MARKS_ITS_END = r"""
import json, signal, sys
signal.signal(signal.SIGTERM, lambda *_: (open(sys.argv[1], "w").write("terminated"), sys.exit(0)))
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
sys.stdin.read()
open(sys.argv[1], "w").write("end of input")
"""


@pytest.mark.skipif(os.name != "posix", reason="a SIGTERM handler; Windows is the manual CI run")
def test_close_ends_the_engine_by_closing_its_input(tmp_path: Path) -> None:
    """@regression-guard (S0042: the writer thread now closes stdin): close() lets the engine see the end of its input
    and exit by itself, which lets it finish the request in hand; it is not terminated."""
    end = tmp_path / "end"
    command = EngineCommand(argv=(sys.executable, "-I", "-c", MARKS_ITS_END, str(end)), env=None)
    c = EngineClient(command)
    assert isinstance(c.start(), EngineInfo)
    c.close()
    assert end.read_text() == "end of input"


class Joinable:
    """A stand-in thread that records how long it was asked to wait and takes that long on the given clock."""

    def __init__(self, clock: FakeTime) -> None:
        self.clock = clock
        self.waits: list[float] = []

    def join(self, timeout: float | None = None) -> None:
        assert timeout is not None
        self.waits.append(timeout)
        self.clock.now += timeout  # as if the thread never ended


def test_threads_are_joined_under_one_deadline_not_one_each() -> None:
    """S0042: a reap waits at most `close_timeout` in all, however many threads its run has."""
    clock = FakeTime()
    threads = [Joinable(clock) for _ in range(3)]
    began = clock.now
    join_all(threads, 2.0, clock.monotonic)  # pyright: ignore[reportArgumentType] (duck-typed threads)
    assert clock.now - began == 2.0
    assert [t.waits for t in threads] == [[2.0], [0.0], [0.0]]


def engine_threads(before: set[threading.Thread]) -> list[str]:
    """The engine threads started since `before` that still run (another test's leftovers do not count)."""
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("engine-") and t not in before)


def test_a_stopped_engine_leaves_no_thread_behind(client: EngineClient) -> None:
    """S0042: the reap ends the writer as well as the readers, after a stop (here a timeout) and at close: no engine
    thread or pipe outlives its engine, so restarts do not pile them up (INV-10, INV-11)."""
    before = set(threading.enumerate())
    client.start()
    assert engine_threads(before) == ["engine-replies", "engine-requests", "engine-stderr"]
    out = client.call("test.stall", {}, timeout=0.5)
    assert isinstance(out, EngineFailure)
    assert out.kind == "timeout"
    assert engine_threads(before) == []
    assert client.ping(timeout=DEADLINE) == Pong()
    client.close()
    assert engine_threads(before) == []


# -- S0042 verifier round 1 (F1-F3) ---------------------------------------------------------------------------------


class NeverEnds:
    """A stand-in for a pipe thread whose pipe a child of the engine holds: each join waits out its whole timeout."""

    def __init__(self, waits: list[float]) -> None:
        self.waits = waits

    def join(self, timeout: float | None = None) -> None:
        assert timeout is not None, "a reap must never join without a limit"
        self.waits.append(timeout)
        threading.Event().wait(timeout)


def test_the_reap_waits_close_timeout_in_all_for_threads_that_do_not_end(fake_time: FakeTime) -> None:
    """S0042 verifier F1: the reap itself, not only `join_all`, waits `close_timeout` in all for threads that never
    end (before the fix to the test gap, `_reap` joining each thread for `close_timeout` passed every test)."""
    policy = RestartPolicy(close_timeout=0.5)
    c = EngineClient(engine_command(), policy, monotonic=fake_time.monotonic, pause=fake_time.pause)
    assert isinstance(c.start(), EngineInfo)
    run = c._run  # pyright: ignore[reportPrivateUsage] (the reap's threads have no public handle)
    assert run is not None
    real = list(run.threads)
    waits: list[float] = []
    run.threads[:] = [NeverEnds(waits) for _ in real]  # pyright: ignore[reportArgumentType, reportCallIssue]
    c.close()
    for t in real:
        t.join(DEADLINE)
    assert len(waits) == len(real)
    assert sum(waits) <= policy.close_timeout, f"the reap waited {waits}: close_timeout each, not in all"


def test_every_pipe_of_a_stopped_engine_is_closed(client: EngineClient) -> None:
    """S0042 verifier F3: each pipe thread closes its own pipe when it ends (the reap closes none), so a stopped
    engine leaves no open pipe behind, whatever the garbage collector does."""
    client.start()
    run = client._run  # pyright: ignore[reportPrivateUsage] (the pipes have no public handle)
    assert run is not None
    pipes = (run.proc.stdin, run.proc.stdout, run.proc.stderr)
    out = client.call("test.stall", {}, timeout=0.5)
    assert isinstance(out, EngineFailure)
    assert out.kind == "timeout"
    assert [p is not None and p.closed for p in pipes] == [True, True, True]


def test_the_longest_call_and_close_are_derived_from_the_policy() -> None:
    """S0042 verifier F2: the stated bounds, as code: a stop is three waits of `close_timeout`, a call at most two
    stops plus a start plus its own wait, a close at most two stops."""
    policy = RestartPolicy()
    assert policy.longest_stop == 3 * 5.0
    assert policy.longest_call(2.0) == 8.0 + 15.0 + 2.0 + 2 * 15.0
    assert policy.longest_close == 2 * 15.0


WORST_PATH = r"""
import json, os, signal, subprocess, sys, threading
work = sys.argv[1]
runs = os.path.join(work, "runs")
n = int(open(runs).read()) + 1 if os.path.exists(runs) else 1
open(runs, "w").write(str(n))
signal.signal(signal.SIGTERM, signal.SIG_IGN)
req = json.loads(sys.stdin.readline())
child = subprocess.Popen([sys.executable, "-I", "-c", "import threading; threading.Event().wait()"],
                         stdin=subprocess.DEVNULL)  # holds this engine's stdout and stderr
open(os.path.join(work, "children"), "a").write(f"{child.pid}\n")
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1 if n == 1 else 2, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
if n == 1:
    sys.exit(0)
threading.Event().wait()
"""


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM ignored, grandchildren; Windows is the manual CI run")
def test_a_call_on_the_slowest_path_stays_within_the_stated_bound(tmp_path: Path) -> None:
    """S0042 verifier F2: the path that waits longest today: an engine found dead first whose output a child holds
    (its reap waits out `close_timeout`), then a new engine that speaks another protocol and ignores both the end of
    its input and SIGTERM (a shutdown: three waits). The call ends within `RestartPolicy.longest_call`, an upper
    bound by design, so this checks the stated bound, not a time budget."""
    policy = RestartPolicy(delays=(0.0,), start_timeout=5.0, close_timeout=1.0)
    command = EngineCommand(argv=(sys.executable, "-I", "-c", WORST_PATH, str(tmp_path)), env=None)
    c = EngineClient(command, policy)
    children = tmp_path / "children"
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        assert stopped(pid), "the first engine did not exit"
        began = time.monotonic()
        out = c.call("anything", {}, timeout=0.01)
        took = time.monotonic() - began
        assert isinstance(out, EngineFailure)
        assert out.kind == "incompatible"
        assert took >= 4 * policy.close_timeout, "the path did not wait as expected: the test proves nothing then"
        assert took <= policy.longest_call(0.01)
    finally:
        c.close()
        for line in children.read_text().split() if children.exists() else []:
            with contextlib.suppress(ProcessLookupError):
                kill(int(line))
