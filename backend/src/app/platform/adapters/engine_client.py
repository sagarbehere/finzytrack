"""The ledger-engine client: the `EnginePort` adapter that runs the engine as a child process (ADR-0054 decision 4;
ARCHITECTURE.md §2.0, §5).

- **Spawn.** `EngineCommand` names the interpreter and environment. The engine runs isolated (`python -I`), told
  the client's pid (`--parent-pid`) for its parent-death watchdog, with the client's environment minus `FT_*`
  (the shell token and our own settings) and `PYTHON*` (interpreter overrides). On POSIX it stays in the client's
  process group, so the shell's group kill reaches it; on Windows the Job Object does (§5).
- **Handshake.** Every start sends `handshake` first; another protocol version is `incompatible` and is never
  retried (until `reset`).
- **Requests** are serialized, one in flight, each with the caller's timeout. A reader thread turns the engine's
  stdout into lines, so a wait always ends: by a reply, by the timeout, or by end of stream when the engine dies.
  A timeout or an invalid reply stops the engine (its state is unknown); a request is never resent.
- **Supervision.** An engine that stopped (crash, timeout, violation, failed start) is restarted by the next
  request, after a backoff delay that grows with the stops in the window (`RestartPolicy`). After
  `max_crashes` stops in `window` seconds the client answers `unavailable` without starting it, until the window
  passes or `reset()` (the user's "try again").
- **Shutdown.** `close()` closes the engine's stdin (it finishes the request in hand and exits), then terminates and
  finally kills it if it does not exit in time.
- **Bounded state** (INV-10, INV-11): one engine process, one request in flight, the last `STDERR_LINES` lines of
  the engine's stderr, at most `max_crashes` stop times. The client is owned by the user container
  (`platform/state_registry.py`).
"""

from __future__ import annotations

import contextlib
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
)

STDERR_LINES = 200
STDERR_LINE_BYTES = 64 * 1024
DROPPED_ENV_PREFIXES = ("FT_", "PYTHON")


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
        """The engine installed in a virtual environment (development; the packaged layout arrives with BL-0083)."""
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        argv = (str(python), "-I", "-m", "ledger_engine", *(("--test-ops",) if test_ops else ()))
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


def _pump_stderr(pipe: IO[bytes], tail: deque[str]) -> None:
    while True:
        line = pipe.readline(STDERR_LINE_BYTES)
        if not line:
            return
        tail.append(line.decode("utf-8", errors="replace").rstrip("\n"))


class EngineClient:
    """`EnginePort` over a child process. Thread-safe: calls are serialized."""

    def __init__(
        self,
        command: EngineCommand,
        policy: RestartPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = _real_pause,
    ) -> None:
        self._command = command
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

    # -- the port ---------------------------------------------------------------------------------------------

    def start(self) -> EngineInfo | EngineFailure:
        with self._lock:
            ready = self._ensure_running()
            return ready if isinstance(ready, EngineFailure) else ready[1]

    def ping(self, timeout: float) -> Pong | EngineRefusal | EngineFailure:
        with self._lock:
            out = self._request("ping", {}, timeout)
            if not isinstance(out, Accepted):
                return out
            pong = decode_pong(out.result)
            return self._violated(pong.detail) if isinstance(pong, Violation) else pong

    def call(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
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
        """The last lines the engine wrote to stderr (its log until structured logging arrives)."""
        run = self._run
        return "\n".join(list(run.stderr)) if run is not None else ""

    def reset(self) -> None:
        """Forget past stops and an incompatible verdict, so the next request starts the engine at once."""
        with self._lock:
            self._stops.clear()
            self._incompatible = None

    # -- internals --------------------------------------------------------------------------------------------

    def _request(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Accepted | EngineRefusal | EngineFailure:
        ready = self._ensure_running()
        if isinstance(ready, EngineFailure):
            return ready
        run, _ = ready
        return self._exchange(run, op, params, timeout)

    def _exchange(
        self, run: _Running, op: str, params: Mapping[str, object], timeout: float
    ) -> Accepted | EngineRefusal | EngineFailure:
        self._next_id = self._next_id % MAX_ID + 1  # 1..MAX_ID, wrapping (one request in flight, so ids never clash)
        rid = self._next_id
        try:
            line = encode_request(rid, op, params)
        except (TypeError, ValueError) as e:
            return EngineRefusal("protocol.malformed", f"The request is not JSON data: {e}")
        if len(line) - 1 > MAX_LINE_BYTES:
            return EngineRefusal("protocol.malformed", f"The request is longer than {MAX_LINE_BYTES} bytes.")
        stdin = run.proc.stdin
        if stdin is None:  # pragma: no cover (always a pipe here)
            return self._stopped("crashed", "the engine has no stdin")
        try:
            stdin.write(line)
            stdin.flush()
        except OSError:
            return self._stopped("crashed", self._exit_detail(run))
        try:
            raw = run.replies.get(timeout=timeout)
        except queue.Empty:
            return self._stopped("timeout", f"no reply to {op} within {timeout} s; the engine was stopped")
        if raw is None:
            return self._stopped("crashed", self._exit_detail(run))
        reply = decode_reply(raw, rid, op)
        return self._violated(reply.detail) if isinstance(reply, Violation) else reply

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
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, close_fds=True
            )
        except OSError as e:
            self._stops.append(self._monotonic())
            return EngineFailure("start_failed", f"the engine could not be started: {e}")
        run = _Running(proc)
        self._run = run
        self._info = None
        if proc.stdout is None or proc.stderr is None:  # pragma: no cover (pipes were requested)
            return EngineFailure("start_failed", self._stopped("crashed", "the engine has no output pipes").detail)
        for target, args in ((_pump_lines, (proc.stdout, run.replies)), (_pump_stderr, (proc.stderr, run.stderr))):
            thread = threading.Thread(target=target, args=args, name=f"engine-{target.__name__}", daemon=True)
            thread.start()
            run.threads.append(thread)
        reply = self._exchange(run, "handshake", {}, self._policy.start_timeout)
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
        for pipe in (run.proc.stdin, run.proc.stdout, run.proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
