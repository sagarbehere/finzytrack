"""The ledger-engine client: the `EnginePort` adapter that runs the engine as a child process (ADR-0054 decision 4;
ARCHITECTURE.md §2.0, §5).

- **Spawn.** `EngineCommand` names the interpreter and environment: `for_venv` in development and tests,
  `for_packaged` in the bundle (S0038), where one interpreter serves both processes with two separate
  site-packages (ADR-0056): the engine runs `python -I -S -B -c <bootstrap> <engine site>`, so the interpreter's own
  site-packages, which hold the main sidecar, are never on the engine's path, and the bootstrap adds only the
  engine's site (ADR-0054 decision 5: neither closure reaches the other process). The engine runs isolated (`-I`),
  told the client's pid (`--parent-pid`) for its parent-death watchdog, with the client's environment minus `FT_*`
  (the shell token and our own settings) and `PYTHON*` (interpreter overrides). On POSIX it stays in the client's
  process group, so the shell's group kill reaches it; on Windows it opens no console window (`CREATE_NO_WINDOW`),
  stays in the sidecar's job (the shell's Job Object arrives with M2's sidecar supervisor), and ends on stdin EOF
  when the sidecar is gone.
- **Handshake.** Every start sends `handshake` first; another protocol version is `incompatible` and is never
  retried (until `reset`).
- **Requests** are serialized, one in flight. The caller's timeout bounds the wait for its reply; a start the call
  needs first is bounded by the policy (`RestartPolicy.longest_start`: the largest backoff delay plus the handshake
  timeout), not by the caller's timeout, so a short timeout cannot cut a cold start short and count a healthy
  engine as a stop (S0035 independent verifier F3). A call therefore returns within `longest_start` + its timeout,
  after any call ahead of it on the lock. A reader thread turns the engine's stdout into lines, so a wait always
  ends: by a reply, by the timeout, or by end of stream when the engine dies. A timeout or an invalid reply stops
  the engine (its state is unknown); a request is never resent.
- **Encoding** happens before anything is started or sent: params that are not JSON data raise `ValueError` (a
  caller error, like a bad timeout); a request longer than the line limit is `EngineFailure("too_large")`, which
  leaves the engine untouched and is no stop (S0035 independent verifier F7).
- **Supervision.** An engine that stopped (crash, timeout, violation, failed start) is restarted by the next
  request, after a backoff delay that grows with the stops in the window (`RestartPolicy`). After
  `max_crashes` stops in `window` seconds the client answers `unavailable` without starting it, until the window
  passes or `reset()` (the user's "try again").
- **Shutdown.** `close()` closes the engine's stdin (it finishes the request in hand and exits), then terminates and
  finally kills it if it does not exit in time.
- **Stderr.** Each line the engine writes to stderr is kept in a bounded tail and handed to `on_stderr` if given
  (the composition root forwards it to the log, S0038). A callback that raises is dropped for the rest of that
  engine's run; the tail goes on.
- **Bounded state** (INV-10, INV-11): one engine process, one request in flight, the last `STDERR_LINES` lines of
  the engine's stderr (of the running engine, else of the last one that stopped: the only diagnosis of a crash),
  at most `max_crashes` stop times. The client is owned by the user container
  (`platform/state_registry.py`).
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from app.platform.engine import EngineFailure, EngineInfo, EngineRefusal, FailureKind, Pong
from app.platform.engine_protocol import (
    MAX_ID,
    MAX_LINE_BYTES,
    PROTOCOL_VERSION,
    Accepted,
    Violation,
    decode_info,
    decode_pong,
    decode_reply,
    encode_request,
    foreign_version,
)

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
    """How to start the engine: the argv (without `--parent-pid`, which the client adds) and the environment
    (None: inherit unchanged, for stand-in engines in tests)."""

    argv: tuple[str, ...]
    env: Mapping[str, str] | None

    @classmethod
    def for_venv(cls, venv: Path, *, test_ops: bool = False, environ: Mapping[str, str] | None = None) -> EngineCommand:
        """The engine installed in a virtual environment (development and tests)."""
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        argv = (str(python), "-I", "-m", "ledger_engine", *(("--test-ops",) if test_ops else ()))
        return cls(argv=argv, env=engine_env(os.environ if environ is None else environ))

    @classmethod
    def for_packaged(
        cls, python: Path, site: Path, *, test_ops: bool = False, environ: Mapping[str, str] | None = None
    ) -> EngineCommand:
        """The engine in the bundle: the shared interpreter `python` with only the engine's `site` (S0038)."""
        argv = (*packaged_argv(python, site, PACKAGED_RUN), *(("--test-ops",) if test_ops else ()))
        return cls(argv=argv, env=engine_env(os.environ if environ is None else environ))


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    """Backoff and bound for restarts. `delays[n-1]` is the wait before a start that follows n recent stops (the
    last delay repeats); `max_crashes` stops within `window` seconds make the engine `unavailable`."""

    max_crashes: int = 3
    window: float = 300.0
    delays: tuple[float, ...] = (0.5, 2.0, 8.0)
    start_timeout: float = 15.0
    close_timeout: float = 5.0

    def __post_init__(self) -> None:
        """Every time is finite, so every start and stop is bounded (S0035 independent verifier F3)."""
        positive = {"window": self.window, "start_timeout": self.start_timeout, "close_timeout": self.close_timeout}
        bad = [f"{k}={v!r}" for k, v in positive.items() if not (v > 0 and math.isfinite(v))]
        if not all(d >= 0 and math.isfinite(d) for d in self.delays):
            bad.append(f"delays={self.delays!r}")
        if self.max_crashes < 1:
            bad.append(f"max_crashes={self.max_crashes!r}")
        if bad:
            msg = f"RestartPolicy needs positive, finite times and at least one crash: {', '.join(bad)}"
            raise ValueError(msg)

    @property
    def longest_start(self) -> float:
        """The longest a start can take before the request goes out: the largest backoff plus the handshake."""
        return max(self.delays, default=0.0) + self.start_timeout


def _check_timeout(timeout: float) -> None:
    """A timeout is a positive, finite number of seconds: an infinite one could wait forever on a stuck engine.
    Checked before anything is sent, so a bad value never leaves a request in flight."""
    if not (timeout > 0 and math.isfinite(timeout)):
        msg = f"timeout must be a positive, finite number of seconds, not {timeout!r}"
        raise ValueError(msg)


def _real_pause(seconds: float) -> None:
    threading.Event().wait(seconds)


@dataclass
class _Running:
    proc: subprocess.Popen[bytes]
    replies: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue[bytes | None]())
    stderr: deque[str] = field(default_factory=lambda: deque[str](maxlen=STDERR_LINES))
    threads: list[threading.Thread] = field(default_factory=lambda: list[threading.Thread]())


def _pump_lines(pipe: IO[bytes], replies: queue.Queue[bytes | None]) -> None:
    """Reply lines into the queue; None at end of stream. A line longer than the limit is passed on cut (without
    its newline), so the decoder refuses it."""
    while True:
        line = pipe.readline(MAX_LINE_BYTES + 2)
        if not line:
            replies.put(None)
            return
        replies.put(line)


def _pump_stderr(pipe: IO[bytes], tail: deque[str], sink: Callable[[str], None] | None) -> None:
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
    """`EnginePort` over a child process. Thread-safe: calls are serialized."""

    def __init__(
        self,
        command: EngineCommand,
        policy: RestartPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = _real_pause,
        on_stderr: Callable[[str], None] | None = None,
    ) -> None:
        self._command = command
        self._on_stderr = on_stderr
        self._policy = policy or RestartPolicy()
        self._monotonic = monotonic
        self._pause = pause
        self._lock = threading.RLock()
        self._run: _Running | None = None
        self._info: EngineInfo | None = None
        self._stops: deque[float] = deque(maxlen=max(self._policy.max_crashes, 1))
        self._incompatible: EngineFailure | None = None
        self._closed = False
        self._next_id = 0
        self._last_stderr: deque[str] = deque[str]()

    # -- the port ---------------------------------------------------------------------------------------------

    def start(self) -> EngineInfo | EngineFailure:
        with self._lock:
            ready = self._ensure_running()
            return ready if isinstance(ready, EngineFailure) else ready[1]

    def ping(self, timeout: float) -> Pong | EngineRefusal | EngineFailure:
        _check_timeout(timeout)
        with self._lock:
            out = self._request("ping", {}, timeout)
            if not isinstance(out, Accepted):
                return out
            pong = decode_pong(out.result)
            return self._violated(pong.detail) if isinstance(pong, Violation) else pong

    def call(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        _check_timeout(timeout)
        with self._lock:
            out = self._request(op, params, timeout)
            return out.result if isinstance(out, Accepted) else out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            run, self._run = self._run, None
            if run is not None:
                self._shutdown(run)

    # -- diagnostics ------------------------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The running engine's process id, if one is running."""
        with self._lock:
            return self._run.proc.pid if self._run is not None else None

    @property
    def info(self) -> EngineInfo | None:
        with self._lock:
            return self._info

    def stderr_tail(self) -> str:
        """The last lines the engine wrote to stderr: of the running
        engine, else of the last one that stopped (S0035 independent verifier F4)."""
        run = self._run
        return "\n".join(list(run.stderr if run is not None else self._last_stderr))

    def reset(self) -> None:
        """Forget past stops and an incompatible verdict, so the next request starts the engine at once."""
        with self._lock:
            self._stops.clear()
            self._incompatible = None

    # -- internals --------------------------------------------------------------------------------------------

    def _request(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Accepted | EngineRefusal | EngineFailure:
        line = self._encode(op, params)
        if isinstance(line, EngineFailure):
            return line
        ready = self._ensure_running()
        if isinstance(ready, EngineFailure):
            return ready
        run, _ = ready
        return self._exchange(run, line, timeout)

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
    ) -> Accepted | EngineRefusal | EngineFailure:
        rid, op, line = request
        stdin = run.proc.stdin
        if stdin is None:  # pragma: no cover (always a pipe here)
            return self._stopped("crashed", "the engine has no stdin")
        try:
            stdin.write(line)
            stdin.flush()
        except OSError:
            return self._stopped("crashed", self._exit_detail(run))
        try:
            return self._await_reply(run, rid, op, timeout)
        except BaseException:
            # The request is out and its reply may still come: an engine left running would hand that reply to the
            # next request (S0035 verifier F4c). Stop it, then let the error go on.
            self._stopped("crashed", "the client failed while waiting for the reply")
            raise

    def _await_reply(
        self, run: _Running, rid: int, op: str, timeout: float
    ) -> Accepted | EngineRefusal | EngineFailure:
        try:
            raw = run.replies.get(timeout=timeout)
        except queue.Empty:
            return self._stopped("timeout", f"no reply to {op} within {timeout} s; the engine was stopped")
        if raw is None:
            return self._stopped("crashed", self._exit_detail(run))
        reply = decode_reply(raw, rid, op)
        if isinstance(reply, Violation):
            foreign = foreign_version(raw) if op == "handshake" else None
            if foreign is not None:  # another protocol: `_spawn` stops it and marks it incompatible (verifier F2)
                return EngineRefusal("protocol.version_mismatch", f"the engine speaks protocol {foreign}")
            return self._violated(reply.detail)
        return reply

    def _ensure_running(self) -> tuple[_Running, EngineInfo] | EngineFailure:
        if self._closed:
            return EngineFailure("closed", "the engine client was closed")
        if self._incompatible is not None:
            return self._incompatible
        run = self._run
        if run is not None and run.proc.poll() is None and self._info is not None:
            return run, self._info
        if run is not None:  # it stopped since the last request
            self._stopped("crashed", self._exit_detail(run))
        now = self._monotonic()
        while self._stops and now - self._stops[0] >= self._policy.window:
            self._stops.popleft()
        if len(self._stops) >= self._policy.max_crashes:
            return EngineFailure(
                "unavailable",
                f"the engine stopped {len(self._stops)} times in {self._policy.window:g} s; not restarting yet",
            )
        if self._stops:
            delays = self._policy.delays
            delay = delays[min(len(self._stops), len(delays)) - 1] if delays else 0.0
            wait = self._stops[-1] + delay - now
            if wait > 0:
                self._pause(wait)
        return self._spawn()

    def _spawn(self) -> tuple[_Running, EngineInfo] | EngineFailure:
        argv = [*self._command.argv, "--parent-pid", str(os.getpid())]
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
            self._stops.append(self._monotonic())
            return EngineFailure("start_failed", f"the engine could not be started: {e}")
        run = _Running(proc)
        self._run = run
        self._info = None
        if proc.stdout is None or proc.stderr is None:  # pragma: no cover (pipes were requested)
            return EngineFailure("start_failed", self._stopped("crashed", "the engine has no output pipes").detail)
        pumps = (
            threading.Thread(target=_pump_lines, args=(proc.stdout, run.replies), name="engine-replies", daemon=True),
            threading.Thread(
                target=_pump_stderr, args=(proc.stderr, run.stderr, self._on_stderr), name="engine-stderr", daemon=True
            ),
        )
        for thread in pumps:
            thread.start()
            run.threads.append(thread)
        handshake = self._encode("handshake", {})
        if isinstance(handshake, EngineFailure):  # pragma: no cover (a handshake is a few bytes)
            return EngineFailure("start_failed", self._stopped("crashed", handshake.detail).detail)
        reply = self._exchange(run, handshake, self._policy.start_timeout)
        if isinstance(reply, EngineFailure):
            return EngineFailure("start_failed", f"handshake: {reply.kind}: {reply.detail}")
        if isinstance(reply, EngineRefusal):
            if reply.key == "protocol.version_mismatch":
                return self._incompatible_engine(run, reply.message)
            detail = self._stopped("crashed", f"the engine refused the handshake: {reply.key}").detail
            return EngineFailure("start_failed", detail)
        info = decode_info(reply.result)
        if isinstance(info, Violation):
            return EngineFailure("start_failed", f"handshake: {self._violated(info.detail).detail}")
        if info.protocol != PROTOCOL_VERSION:
            return self._incompatible_engine(run, f"the engine speaks protocol {info.protocol}")
        self._info = info
        return run, info

    def _incompatible_engine(self, run: _Running, what: str) -> EngineFailure:
        """Another protocol version: stop the engine and never start it again (until `reset`)."""
        self._run = None
        self._shutdown(run)
        self._incompatible = EngineFailure("incompatible", f"{what}; this sidecar speaks {PROTOCOL_VERSION}")
        return self._incompatible

    def _violated(self, detail: str) -> EngineFailure:
        return self._stopped("protocol_violation", f"{detail}; the engine was stopped")

    def _stopped(self, kind: FailureKind, detail: str) -> EngineFailure:
        """The running engine is gone or untrusted: stop it, record the stop for the backoff, report `kind`."""
        run, self._run = self._run, None
        self._info = None
        if run is not None:
            self._kill(run)
        self._stops.append(self._monotonic())
        return EngineFailure(kind, detail)

    def _keep_stderr(self, run: _Running) -> None:
        """After the run's threads are joined: its stderr tail outlives it (S0035 independent verifier F4)."""
        self._last_stderr = run.stderr

    def _exit_detail(self, run: _Running) -> str:
        try:
            code = run.proc.wait(self._policy.close_timeout)
        except subprocess.TimeoutExpired:
            return "the engine closed its output but did not exit"
        return f"the engine exited with code {code}"

    def _kill(self, run: _Running) -> None:
        if run.proc.poll() is None:
            run.proc.kill()
        self._reap(run)

    def _shutdown(self, run: _Running) -> None:
        stdin = run.proc.stdin
        if stdin is not None:
            with contextlib.suppress(OSError):
                stdin.close()
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
        try:
            run.proc.wait(self._policy.close_timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover (after SIGKILL the OS ends it)
            return
        for thread in run.threads:
            thread.join(self._policy.close_timeout)
        self._keep_stderr(run)
        for pipe in (run.proc.stdin, run.proc.stdout, run.proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
