"""The ledger-engine client: the `EnginePort` adapter that runs the engine as a child process (ADR-0054 decision 4;
ARCHITECTURE.md §2.0, §5, §7).

- **Supervision is a statechart** (S0040). Every lifecycle decision is the pure table's
  (`app.platform.engine_supervision`): when to start the engine, the backoff before a restart, the bound after
  which it is `unavailable`, `incompatible`, `closed`, and what is logged. This client is its shell: it observes the
  process, feeds each observation to `transition` as an event, and carries out the effects (spawn, handshake,
  pause, kill, shut down, log, fail the caller). `state` is the table's current state, a read that never waits for
  a call in flight. An exception that reaches a public call while a start, a backoff or a request is under way is
  fed in as `Interrupted` before it goes on, so the table always rests in a state the next call can use.
- **Spawn.** `EngineCommand` names the interpreter and environment: `for_venv` in development and tests,
  `for_packaged` in the bundle (S0038), where one interpreter serves both processes with two separate
  site-packages (ADR-0056): the engine runs `python -I -S -B -c <bootstrap> <engine site>`, so the interpreter's own
  site-packages, which hold the main sidecar, are never on the engine's path, and the bootstrap adds only the
  engine's site (ADR-0054 decision 5: neither closure reaches the other process). The engine runs isolated (`-I`),
  told the client's pid (`--parent-pid`) for its parent-death watchdog and, when the command names one, the engine
  lock it must hold for its life (`--engine-lock`, S0053; engine-jobs §5.1), with the client's environment minus `FT_*`
  (the shell token and our own settings) and `PYTHON*` (interpreter overrides). On POSIX it stays in the client's
  process group, so the shell's group kill reaches it; on Windows it opens no console window (`CREATE_NO_WINDOW`),
  stays in the sidecar's job (the shell's Job Object arrives with M2's sidecar supervisor), and ends on stdin EOF
  when the sidecar is gone.
- **Handshake.** Every start sends `handshake` first; another protocol version is `incompatible` and is never
  retried (until `reset`).
- **Requests** are serialized, one in flight. The caller's timeout covers sending the request and waiting for its
  reply, as one wait (S0042): a writer thread owns the engine's stdin and writes each request, a reader thread turns
  the engine's stdout into lines, and the caller only waits for the reply queue, so a wait always ends: by a reply,
  by the timeout, by a failed write, or by end of stream when the engine dies. An engine that stops reading (frozen,
  stuck) cannot hold the caller in a write, however large the request (area review 2026-10-26 F3). A start the call
  needs first is bounded by the policy (`RestartPolicy.longest_start`: the largest backoff delay plus the handshake
  timeout), not by the caller's timeout, so a short timeout cannot cut a cold start short and count a healthy
  engine as a stop (S0035 independent verifier F3). A timeout or an invalid reply stops the engine (its state is
  unknown); a request is never resent.
- **Bounds.** Every wait in the client is bounded. Ending one engine waits at most `RestartPolicy.longest_stop`
  (3 x `close_timeout`: a shutdown's wait for the exit at the end of input, its wait after SIGTERM, and the reap,
  which waits for the exit and the reader and writer threads under one deadline; a kill waits less). A call carries
  out at most two stops (an engine found dead first, then the stop of its own start or request), so it returns
  within `RestartPolicy.longest_call(timeout)` = `longest_start` + its timeout + 2 x `longest_stop`, after any call
  ahead of it on the lock; `close()` within `RestartPolicy.longest_close` (2 x `longest_stop`) after the call in
  flight. The bounds are conservative, read off the code's worst paths (S0042 verifier F2: an exact per-path sum
  was wrong once). The one wait left to the OS is `Popen` (fork and exec). A thread that cannot end
  (a pipe that a child of the engine still holds open) is left behind with its pipe, never waited for: closing a pipe
  while its thread is inside a read or write would wait for that thread (S0042).
- **Encoding** happens before anything is started or sent: params that are not JSON data raise `ValueError` (a
  caller error, like a bad timeout); a request longer than the line limit is `EngineFailure("too_large")`, which
  leaves the engine untouched and is no stop (S0035 independent verifier F7). Neither reaches the table.
- **Shutdown.** `close()` has the writer close the engine's stdin (it finishes the request in hand and exits), then
  terminates and finally kills it if it does not exit in time.
- **Log.** The table's `Log` effects go to the `log` sink (the structured log, S0039): `engine.ready` and
  `engine.start_failed` on every start, `engine.stopped`, `engine.unavailable`, `engine.closed`. A sink that raises
  loses that line and changes nothing else: the remaining effects run and the caller gets its own outcome (S0041;
  before, the exception reached the caller after the state had moved).
- **Stderr.** Each line the engine writes to stderr is kept in a bounded tail and handed to `on_stderr` if given
  (the composition root forwards it to the log, S0038). A callback that raises is dropped for the rest of that
  engine's run; the tail goes on.
- **Bounded state** (INV-10, INV-11): one engine process with three threads (writer, reader, stderr), one request in
  flight, the last `STDERR_LINES` lines of the engine's stderr (of the running engine, else of the last one that
  stopped: the only diagnosis of a crash), one supervision state holding at most `max_crashes` stop times. The
  client is owned by the user container (`platform/state_registry.py`).
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal

from app.platform.engine import EngineFailure, EngineInfo, EngineRefusal, Pong
from app.platform.engine_protocol import (
    MAX_ID,
    MAX_LINE_BYTES,
    Accepted,
    Violation,
    decode_info,
    decode_pong,
    decode_reply,
    encode_request,
    foreign_version,
)
from app.platform.engine_supervision import (
    RESTING,
    Answered,
    BackoffElapsed,
    Busy,
    Close,
    Effect,
    Ended,
    Exited,
    Fail,
    Handshake,
    HandshakeFailed,
    HandshakeForeign,
    HandshakeOk,
    Idle,
    Interrupted,
    Kill,
    Log,
    Pause,
    Ready,
    Request,
    Reset,
    RestartPolicy,
    Send,
    Shutdown,
    Spawn,
    Spawned,
    SpawnFailed,
    SupervisionEvent,
    SupervisionState,
    TimedOut,
    Violated,
    transition,
)
from app.platform.observability import Event, EventSink

STDERR_LINES = 200
STDERR_LINE_BYTES = 64 * 1024
DROPPED_ENV_PREFIXES = ("FT_", "PYTHON")
# The packaged layout (ADR-0056; `shell/sidecar/build_sidecar.py` builds it): `<sidecar>/python` is the interpreter's
# prefix, and the engine's own site-packages sit beside it at `<sidecar>/engine/site-packages`.
ENGINE_SITE_PARTS = ("engine", "site-packages")
# The packaged bootstrap: `-S` left the interpreter's own site-packages out; this adds the engine's site (the first
# argument after the code), processing its .pth files, and nothing else.
PACKAGED_PATH_SETUP = "import site, sys; site.addsitedir(sys.argv.pop(1))"
PACKAGED_RUN = "import runpy; runpy.run_module('ledger_engine', run_name='__main__', alter_sys=True)"
CREATE_NO_WINDOW = 0x08000000  # Windows process creation flag: a console program opens no window


def packaged_engine_site(prefix: Path) -> Path:
    """The engine's site-packages in the bundle, from the bundled interpreter's prefix (`sys.prefix`)."""
    return prefix.parent.joinpath(*ENGINE_SITE_PARTS)


def packaged_argv(python: Path, site: Path, code: str) -> tuple[str, ...]:
    """`python` without its own site-packages (`-S`), isolated and writing no bytecode (the bundle is read-only),
    running `code` after the path setup has added `site`."""
    return (str(python), "-I", "-S", "-B", "-c", f"{PACKAGED_PATH_SETUP}; {code}", str(site))


def creation_flags(os_name: str) -> int:
    """Process creation flags for the engine: no console window on Windows."""
    return CREATE_NO_WINDOW if os_name == "nt" else 0


def next_request_id(previous: int) -> int:
    """The id after `previous`: 1..MAX_ID, wrapping (one request is in flight, so ids never clash)."""
    return previous % MAX_ID + 1


def engine_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The engine's environment: the parent's, without `FT_*` and `PYTHON*` variables."""
    return {k: v for k, v in environ.items() if not k.upper().startswith(DROPPED_ENV_PREFIXES)}


@dataclass(frozen=True, slots=True)
class EngineCommand:
    """How to start the engine: the argv (without `--parent-pid`, which the client adds), the environment (None:
    inherit unchanged, for stand-in engines in tests) and the engine lock every engine holds for its life (S0053;
    engine-jobs §5.1: the composition root names `app_local_data/locks/engine.lock`; None for the packaged
    self-check, which does no ledger work, and for stand-ins)."""

    argv: tuple[str, ...]
    env: Mapping[str, str] | None
    engine_lock: Path | None = None

    @classmethod
    def for_venv(
        cls,
        venv: Path,
        *,
        test_ops: bool = False,
        environ: Mapping[str, str] | None = None,
        engine_lock: Path | None = None,
    ) -> EngineCommand:
        """The engine installed in a virtual environment (development and tests)."""
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        argv = (str(python), "-I", "-m", "ledger_engine", *(("--test-ops",) if test_ops else ()))
        return cls(argv=argv, env=engine_env(os.environ if environ is None else environ), engine_lock=engine_lock)

    @classmethod
    def for_packaged(
        cls,
        python: Path,
        site: Path,
        *,
        test_ops: bool = False,
        environ: Mapping[str, str] | None = None,
        engine_lock: Path | None = None,
    ) -> EngineCommand:
        """The engine in the bundle: the shared interpreter `python` with only the engine's `site` (S0038)."""
        argv = (*packaged_argv(python, site, PACKAGED_RUN), *(("--test-ops",) if test_ops else ()))
        return cls(argv=argv, env=engine_env(os.environ if environ is None else environ), engine_lock=engine_lock)

    def spawn_argv(self, parent_pid: int) -> list[str]:
        """The argv the client spawns: the command, the parent's pid for the engine's watchdog, and the engine lock."""
        lock = ["--engine-lock", str(self.engine_lock)] if self.engine_lock is not None else []
        return [*self.argv, "--parent-pid", str(parent_pid), *lock]


def _check_timeout(timeout: float) -> None:
    """A timeout is a positive, finite number of seconds: an infinite one could wait forever on a stuck engine.
    Checked before anything is sent, so a bad value never leaves a request in flight."""
    if not (timeout > 0 and math.isfinite(timeout)):
        msg = f"timeout must be a positive, finite number of seconds, not {timeout!r}"
        raise ValueError(msg)


def _real_pause(seconds: float) -> None:
    threading.Event().wait(seconds)


def _no_log(event: Event, /, **fields: object) -> None:
    """The sink when none is given (the packaged self-check, tests)."""


class WriteFailed:
    """The writer could not hand a request to the engine (its stdin is closed: the engine ended or closed it). The
    class itself is the marker in the reply queue (a type, so no module-level instance, INV-11)."""


type _Reply = bytes | type[WriteFailed] | None
"""A reply line, a failed write, or None at the end of the engine's output."""


@dataclass
class _Running:
    proc: subprocess.Popen[bytes]
    replies: queue.Queue[_Reply] = field(default_factory=lambda: queue.Queue[_Reply]())
    requests: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue[bytes | None]())
    stderr: deque[str] = field(default_factory=lambda: deque[str](maxlen=STDERR_LINES))
    threads: list[threading.Thread] = field(default_factory=lambda: list[threading.Thread]())


@dataclass(frozen=True, slots=True)
class _Lost:
    """No valid answer to a request that went out (an observation; the table decides what it means)."""

    kind: Literal["timeout", "crashed", "protocol_violation"]
    detail: str


@dataclass(frozen=True, slots=True)
class _Foreign:
    """The handshake was answered in another protocol."""

    what: str


def join_all(threads: Iterable[threading.Thread], timeout: float, clock: Callable[[], float] = time.monotonic) -> None:
    """Join `threads` under one shared deadline of `timeout` seconds, not `timeout` each (S0042): a reap waits at
    most `close_timeout` however many threads its run has."""
    deadline = clock() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - clock()))


def _pump_requests(pipe: IO[bytes], requests: queue.Queue[bytes | None], replies: queue.Queue[_Reply]) -> None:
    """Each request line to the engine's stdin, until None (close stdin: the engine finishes and exits) or a failed
    write (reported as `WriteFailed` beside the replies). The only user of stdin: the caller waits for the reply
    queue, never for this write, so an engine that does not read cannot hold the caller (S0042). The pipe is closed
    here, when the thread ends, because closing it from another thread would wait for a write in progress."""
    try:
        while (line := requests.get()) is not None:
            try:
                pipe.write(line)
                pipe.flush()
            except (OSError, ValueError):
                replies.put(WriteFailed)
                return
    finally:
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


def _pump_lines(pipe: IO[bytes], replies: queue.Queue[_Reply]) -> None:
    """Reply lines into the queue; None at end of stream. A line longer than the limit is passed on cut (without
    its newline), so the decoder refuses it. The pipe is closed here, when the stream ends (see `_pump_requests`)."""
    with contextlib.closing(pipe):
        while True:
            line = pipe.readline(MAX_LINE_BYTES + 2)
            if not line:
                replies.put(None)
                return
            replies.put(line)


def _pump_stderr(pipe: IO[bytes], tail: deque[str], sink: Callable[[str], None] | None) -> None:
    with contextlib.closing(pipe):
        while True:
            line = pipe.readline(STDERR_LINE_BYTES)
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            tail.append(text)
            if sink is not None:
                try:
                    sink(text)
                except Exception:  # noqa: BLE001 (a broken log sink must not stop the tail or the engine)
                    sink = None


class EngineClient:
    """`EnginePort` over a child process, supervised by the table in `app.platform.engine_supervision`.
    Thread-safe: calls are serialized."""

    def __init__(
        self,
        command: EngineCommand,
        policy: RestartPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = _real_pause,
        on_stderr: Callable[[str], None] | None = None,
        log: EventSink | None = None,
    ) -> None:
        self._command = command
        self._on_stderr = on_stderr
        self._log: EventSink = log or _no_log
        self._policy = policy or RestartPolicy()
        self._monotonic = monotonic
        self._pause = pause
        self._lock = threading.RLock()
        self._state: SupervisionState = Idle()
        self._run: _Running | None = None
        self._next_id = 0
        self._last_stderr: deque[str] = deque[str]()

    # -- the port ---------------------------------------------------------------------------------------------

    def start(self) -> EngineInfo | EngineFailure:
        with self._call():
            ready = self._ensure_running()
            return ready if isinstance(ready, EngineFailure) else ready[1]

    def ping(self, timeout: float) -> Pong | EngineRefusal | EngineFailure:
        _check_timeout(timeout)
        with self._call():
            out = self._request("ping", {}, timeout, decode_pong)
            return Pong() if isinstance(out, Accepted) else out

    def call(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        _check_timeout(timeout)
        with self._call():
            out = self._request(op, params, timeout, None)
            return out.result if isinstance(out, Accepted) else out

    def close(self) -> None:
        with self._call():
            self._notice_exit()  # an engine that died since the last request is a stop, logged before the close
            self._drive(Close())

    # -- diagnostics ------------------------------------------------------------------------------------------

    @property
    def state(self) -> SupervisionState:
        """The supervision table's current state (S0040): what the future /health and /api/boot report. A read never
        waits for the call lock: each transition publishes its new state as one reference, so a reader sees the last
        published state, a passing one (`Busy`, `Starting`, `Handshaking`, `Restarting`) included. `Ready` and `Busy`
        carry the pid and the handshake's facts, so one read of `state` is a consistent view (S0040 verifier F1)."""
        return self._state

    @property
    def pid(self) -> int | None:
        """The running engine's process id, if one is running (a read that never waits, like `state`)."""
        run = self._run
        return run.proc.pid if run is not None else None

    @property
    def info(self) -> EngineInfo | None:
        state = self._state
        return state.info if isinstance(state, (Ready, Busy)) else None

    def stderr_tail(self) -> str:
        """The last lines the engine wrote to stderr: of the running
        engine, else of the last one that stopped (S0035 independent verifier F4)."""
        run = self._run
        return "\n".join(list(run.stderr if run is not None else self._last_stderr))

    def reset(self) -> None:
        """Forget past stops and an incompatible verdict, so the next request starts the engine at once."""
        with self._call():
            self._drive(Reset())

    # -- the shell around the table ---------------------------------------------------------------------------

    @contextlib.contextmanager
    def _call(self) -> Generator[None]:
        """A public call: it holds the lock, and an exception that leaves the table in a passing state (a start, a
        backoff or a request under way) is fed in as `Interrupted` before it goes on, so the next call finds the table
        at rest, wherever the exception landed (a signal's included). For a request that is out, the table stops the
        engine: its reply may still come and would be handed to the next request (S0035 verifier F4c)."""
        with self._lock:
            try:
                yield
            except BaseException as e:
                if not isinstance(self._state, RESTING):
                    self._feed(Interrupted(f"the client failed: {type(e).__name__}", self._monotonic()))
                raise

    def _drive(self, event: SupervisionEvent) -> EngineFailure | None:
        """Feed `event`, then carry out each follow-up (spawn, handshake, pause) and feed its outcome, until the table
        rests or fails the caller."""
        follow = self._feed(event)
        while not isinstance(follow, EngineFailure) and follow is not None:
            follow = self._feed(self._perform(follow))
        return follow

    def _feed(self, event: SupervisionEvent) -> EngineFailure | Spawn | Handshake | Pause | None:
        """One transition: its effects carried out in order; returns the caller's failure or the follow-up."""
        step = transition(self._policy, self._state, event)
        self._state = step.state  # published as one reference: `state` reads it without the lock
        out: EngineFailure | Spawn | Handshake | Pause | None = None
        for effect in step.effects:
            out = self._effect(effect) or out
        return out

    def _effect(self, effect: Effect) -> EngineFailure | Spawn | Handshake | Pause | None:
        match effect:
            case Kill():
                run, self._run = self._run, None
                if run is not None:
                    self._kill(run)
            case Shutdown():
                run, self._run = self._run, None
                if run is not None:
                    self._shutdown(run)
            case Log(event=event, fields=fields):
                # The log is diagnostics: a failing sink loses this line, never the supervision (S0041). Interrupts
                # (`BaseException`) still go on, to the `_call` guard.
                with contextlib.suppress(Exception):
                    self._log(event, **fields)
            case Fail(failure=failure):
                return failure
            case Spawn() | Handshake() | Pause():
                return effect
        return None

    def _perform(self, effect: Spawn | Handshake | Pause) -> SupervisionEvent:
        match effect:
            case Spawn():
                return self._spawn()
            case Handshake():
                return self._handshake()
            case Pause(seconds=seconds):
                self._pause(seconds)
                return BackoffElapsed()

    def _notice_exit(self) -> None:
        """An engine found dead while `Ready` (it stopped since the last request) is fed in as `Exited`: a stop."""
        run = self._run
        if isinstance(self._state, Ready) and run is not None and run.proc.poll() is not None:
            self._drive(Exited(self._exit_detail(run), self._monotonic()))

    def _ensure_running(self) -> tuple[_Running, EngineInfo] | EngineFailure:
        self._notice_exit()
        failure = self._drive(Request(self._monotonic()))
        if failure is not None:
            return failure
        state, run = self._state, self._run
        if not isinstance(state, Ready) or run is None:  # pragma: no cover (the table rests in Ready or fails)
            msg = f"the supervision table rested in {type(state).__name__} without a failure"
            raise RuntimeError(msg)
        return run, state.info

    def _request(
        self,
        op: str,
        params: Mapping[str, object],
        timeout: float,
        validate: Callable[[Mapping[str, object]], object] | None,
    ) -> Accepted | EngineRefusal | EngineFailure:
        """One request through the table: `Send`, then the answer or its absence. `validate` checks a result's
        shape (a `Violation` stops the engine like any invalid reply)."""
        line = self._encode(op, params)
        if isinstance(line, EngineFailure):
            return line
        ready = self._ensure_running()
        if isinstance(ready, EngineFailure):
            return ready
        run, _ = ready
        self._feed(Send())
        out = self._exchange(run, line, timeout)  # an exception from here on is fed in as `Interrupted` (`_call`)
        if isinstance(out, Accepted) and validate is not None:
            checked = validate(out.result)
            if isinstance(checked, Violation):
                out = _Lost("protocol_violation", f"{checked.detail}; the engine was stopped")
        if isinstance(out, _Lost):
            failure = self._feed(self._lost(out))
            if not isinstance(failure, EngineFailure):  # pragma: no cover (every stop in Busy fails the caller)
                msg = "the supervision table stopped a request without failing it"
                raise RuntimeError(msg)  # noqa: TRY004 (a broken table, not a wrong argument)
            return failure
        self._feed(Answered())
        return out

    def _lost(self, out: _Lost) -> TimedOut | Ended | Violated:
        now = self._monotonic()
        if out.kind == "timeout":
            return TimedOut(out.detail, now)
        if out.kind == "crashed":
            return Ended(out.detail, now)
        return Violated(out.detail, now)

    def _encode(self, op: str, params: Mapping[str, object]) -> tuple[int, str, bytes] | EngineFailure:
        """The next request line, made before anything is started or sent (S0035 independent verifier F7)."""
        self._next_id = next_request_id(self._next_id)
        try:
            line = encode_request(self._next_id, op, params)
        except (TypeError, ValueError, RecursionError) as e:
            msg = f"the request is not JSON data ({type(e).__name__}); nothing was sent"
            raise ValueError(msg) from e
        if len(line) - 1 > MAX_LINE_BYTES:
            return EngineFailure("too_large", f"the request is longer than {MAX_LINE_BYTES} bytes; nothing was sent")
        return self._next_id, op, line

    def _exchange(
        self, run: _Running, request: tuple[int, str, bytes], timeout: float
    ) -> Accepted | EngineRefusal | _Lost:
        """Write the request and read its answer: an observation only; the table decides what follows."""
        out = self._exchange_any(run, request, timeout, handshake=False)
        if isinstance(out, _Foreign):  # pragma: no cover (read only for our own handshake)
            return _Lost("protocol_violation", out.what)
        return out

    def _exchange_any(
        self, run: _Running, request: tuple[int, str, bytes], timeout: float, *, handshake: bool
    ) -> Accepted | EngineRefusal | _Lost | _Foreign:
        """`_exchange`; for our own handshake (`handshake`), a reply in another protocol is `_Foreign`. The request is
        handed to the writer thread and the timeout covers its write and the reply together: one wait (S0042)."""
        rid, op, line = request
        run.requests.put(line)
        try:
            raw = run.replies.get(timeout=timeout)
        except queue.Empty:
            return _Lost("timeout", f"no reply to {op} within {timeout} s; the engine was stopped")
        if raw is None:
            return _Lost("crashed", self._exit_detail(run))
        if not isinstance(raw, bytes):  # WriteFailed: the request never reached the engine
            return _Lost("crashed", self._exit_detail(run, "input"))
        reply = decode_reply(raw, rid, op)
        if isinstance(reply, Violation):
            foreign = foreign_version(raw) if handshake else None
            if foreign is not None:  # another protocol: incompatible, never retried (S0035 verifier F2)
                return _Foreign(f"the engine speaks protocol {foreign}")
            return _Lost("protocol_violation", f"{reply.detail}; the engine was stopped")
        return reply

    def _spawn(self) -> Spawned | SpawnFailed:
        argv = self._command.spawn_argv(os.getpid())
        env = dict(self._command.env) if self._command.env is not None else None
        try:
            proc = subprocess.Popen(  # noqa: S603 (argv from EngineCommand, built by the composition root)
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                close_fds=True,
                creationflags=creation_flags(os.name),
            )
        except OSError as e:
            return SpawnFailed(f"the engine could not be started: {e}", self._monotonic())
        run = _Running(proc)
        self._run = run  # at once, so an interrupted start kills it (the table's `Kill`)
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:  # pragma: no cover (pipes were requested)
            self._run = None
            self._kill(run)
            return SpawnFailed("the engine has no pipes", self._monotonic())
        pumps = (
            threading.Thread(
                target=_pump_requests, args=(proc.stdin, run.requests, run.replies), name="engine-requests", daemon=True
            ),
            threading.Thread(target=_pump_lines, args=(proc.stdout, run.replies), name="engine-replies", daemon=True),
            threading.Thread(
                target=_pump_stderr, args=(proc.stderr, run.stderr, self._on_stderr), name="engine-stderr", daemon=True
            ),
        )
        for thread in pumps:
            thread.start()
            run.threads.append(thread)
        return Spawned(proc.pid)

    def _handshake(self) -> HandshakeOk | HandshakeForeign | HandshakeFailed:
        run = self._run
        if run is None:  # pragma: no cover (the table sends a handshake only right after a spawn)
            return HandshakeFailed("handshake: no engine process", self._monotonic())
        request = self._encode("handshake", {})
        if isinstance(request, EngineFailure):  # pragma: no cover (a handshake is a few bytes)
            return HandshakeFailed(f"handshake: {request.detail}", self._monotonic())
        out = self._exchange_any(run, request, self._policy.start_timeout, handshake=True)
        if isinstance(out, _Foreign):
            return HandshakeForeign(out.what)
        if isinstance(out, _Lost):
            return HandshakeFailed(f"handshake: {out.kind}: {out.detail}", self._monotonic())
        if isinstance(out, EngineRefusal):
            if out.key == "protocol.version_mismatch":
                return HandshakeForeign(out.message)
            return HandshakeFailed(f"the engine refused the handshake: {out.key}", self._monotonic())
        info = decode_info(out.result)
        if isinstance(info, Violation):
            return HandshakeFailed(f"handshake: {info.detail}; the engine was stopped", self._monotonic())
        return HandshakeOk(info)

    def _keep_stderr(self, run: _Running) -> None:
        """After the run's threads are joined: its stderr tail outlives it (S0035 independent verifier F4)."""
        self._last_stderr = run.stderr

    def _exit_detail(self, run: _Running, closed: Literal["input", "output"] = "output") -> str:
        try:
            code = run.proc.wait(self._policy.close_timeout)
        except subprocess.TimeoutExpired:
            return f"the engine closed its {closed} but did not exit"
        return f"the engine exited with code {code}"

    def _kill(self, run: _Running) -> None:
        if run.proc.poll() is None:
            run.proc.kill()
        self._reap(run)

    def _shutdown(self, run: _Running) -> None:
        run.requests.put(None)  # the writer closes stdin: the engine finishes the request in hand and exits
        try:
            run.proc.wait(self._policy.close_timeout)
        except subprocess.TimeoutExpired:
            run.proc.terminate()
            try:
                run.proc.wait(self._policy.close_timeout)
            except subprocess.TimeoutExpired:
                run.proc.kill()
        self._reap(run)

    def _reap(self, run: _Running) -> None:
        """Wait for the exit and the run's threads under one deadline of `close_timeout` (real time: these are real
        waits). Each thread closes its own pipe when it ends; a thread still running (a child of the engine holds its
        pipe open) is left with its pipe, never waited for again (S0042)."""
        deadline = time.monotonic() + self._policy.close_timeout
        run.requests.put(None)  # an idle writer ends; one inside a write ends when the write fails
        try:
            run.proc.wait(self._policy.close_timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover (after SIGKILL the OS ends it)
            return
        join_all(run.threads, max(0.0, deadline - time.monotonic()))
        self._keep_stderr(run)
