"""The ledger-engine client: the `EnginePort` adapter that runs the engine as a child process (ADR-0054 decision 4;
ARCHITECTURE.md §2.0, §5, §7; `design/specs/engine-jobs.md` §5.1-§5.5 since S0057).

- **Supervision is a statechart** (S0040, amended by S0057). Every lifecycle decision is the pure table's
  (`app.platform.engine_supervision`): when to start the engine, the backoff before a restart, the bound after
  which it is `unavailable`, `incompatible`, `closed`, which ends are stops, and what is logged. This client is its
  shell: it observes the process, feeds each observation to `transition` as an event, and carries out the effects.
  `state` is the table's current state, a read that never waits for a call in flight. An exception that reaches a
  public call while a start, a backoff or a request is under way is fed in as `Interrupted` before it goes on, so the
  table always rests in a state the next call can use.
- **No time limits** (§5.4). A start, a handshake and a request wait until the engine answers, ends, or is ended by
  us. A slow job and a hung one look the same from outside; ending either is the user's decision.
- **Our ends** (§5.2, §5.3). `end(reason)` (`cancel`, `retire`, `quit_anyway`) records the reason, then signals the
  engine's group once, and never waits; it may come from any thread, also while a call waits for a start, a
  handshake, a backoff pause or a reply. The call then observes the end and the table calls it `Cancelled` (the call
  fails `cancelled`) or `Retired`, never a stop: a recorded reason wins over the exit status. A retire is for an idle
  engine (after the job's final reply); during a call it is a bug and raises.
- **The engine's process group** (§5.2). The engine is spawned as the leader of its own group (`start_new_session`).
  Each engine has a reaper thread that holds its leader unreaped once it exits, signals the group until it has no
  live member, then reaps it (`app.platform.adapters.engine_group`); after that no signal goes to the group. Once
  drained, the end is reported to `on_engine_ended` (its spawn serial, pid, our reason, exit, peak memory). A new
  engine is spawned only after the previous one is drained (§5.1). Windows has no groups: the engine is ended and
  waited for alone until its nested Job Object (BL-0087).
- **`close()`** (§5.5, §9.3 step 4) records `close`, signals the group once and returns: it takes no call lock (a call
  to a hung engine cannot hold it) and never waits for the reap. The call in flight fails `closed`.
- **Spawn** (§5.1). `EngineCommand` names the interpreter and environment: `for_venv` in development and tests,
  `for_packaged` in the bundle (S0038), where one interpreter serves both processes with two separate
  site-packages (ADR-0056). Both run `python -s -S -B -P -c <bootstrap> <engine site>`: `-S` keeps the interpreter's
  own site-packages off the path and the bootstrap adds only the engine's site (ADR-0054 decision 5); never `-I`,
  which implies `-E` and so ignores `PYTHONHASHSEED`. The environment is the client's minus `FT_*` (the shell token,
  our settings) and every `PYTHON*` variable, plus `PYTHONHASHSEED=0` (R4's digests must not depend on hash order).
  The engine is told the client's pid (`--parent-pid`) and, when the command names one, the engine lock
  (`--engine-lock`, S0053). On Windows it opens no console window (`CREATE_NO_WINDOW`).
- **The bytecode directory** (§5.1). When the command names one (the composition root: `app_local_data/engine/
  bytecode/<build>/`), it is filled once in the background by `compileall` under `-X pycache_prefix` (the engine's
  interpreter library, site-packages and code), marked complete last, and then used read-only by every engine with
  `-X pycache_prefix`. The table discards it when an engine fails to start while using it, and starts once without.
- **Handshake.** Every start sends `handshake` first; another protocol version is `incompatible` and is never
  retried (until `reset`).
- **Requests** are serialized, one in flight. A writer thread owns the engine's stdin (S0042), so an engine that
  stops reading cannot hold the caller in a write: the caller only waits for the reply queue, and `end` ends that
  wait. A request is never resent.
- **A job's messages** (protocol version 2). `call(..., on_event=...)` delivers the engine's progress, its `gate`,
  `committed` and the reply's `decision`, in order, on the calling thread. `proceed()` answers a pending gate (from
  any thread); with no gate pending it sends nothing and returns False, since a stray `proceed` would be read as a
  request. A second gate, `committed` before `proceed`, or a reply while the gate waits, is a protocol violation.
  **After `proceed`** (the commit point) a violation does not signal the engine: its input is closed and its output
  read to the end, then the stop is counted (§5.3).
- **Encoding** happens before anything is started or sent: params that are not JSON data raise `ValueError`; a
  request longer than the line limit is `EngineFailure("too_large")`, which leaves the engine untouched and is no
  stop (S0035 independent verifier F7). Neither reaches the table.
- **Log.** The table's `Log` effects go to the `log` sink (S0039): `engine.ready`, `engine.start_failed`,
  `engine.stopped`, `engine.unavailable`, `engine.ended`, `engine.bytecode`, `engine.closed`. A sink that raises
  loses that line and changes nothing else (S0041).
- **Stderr.** Each line the engine writes to stderr is kept in a bounded tail and handed to `on_stderr` if given
  (S0038). A callback that raises is dropped for the rest of that engine's run; the tail goes on.
- **Bounded state** (INV-10, INV-11): one engine process with four threads (writer, reader, stderr, reaper), the
  previous engine until it is drained, one request in flight, the last `STDERR_LINES` stderr lines, one bytecode fill,
  one supervision state holding at most `max_crashes` stop times. The client is owned by the user container
  (`platform/state_registry.py`).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import queue
import select
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal

from app.platform.adapters.engine_group import EngineGroup, GroupOps, PosixGroupOps, Reaped
from app.platform.engine import (
    Committed,
    EndReason,
    EngineEnd,
    EngineEvent,
    EngineFailure,
    EngineInfo,
    EngineRefusal,
    GateReached,
    Pong,
    Progress,
)
from app.platform.engine_protocol import (
    MAX_ID,
    MAX_LINE_BYTES,
    Accepted,
    Violation,
    decode_info,
    decode_pong,
    decode_reply,
    encode_proceed,
    encode_request,
    foreign_version,
)
from app.platform.engine_supervision import (
    RESTING,
    Answered,
    BackoffElapsed,
    Busy,
    Cancelled,
    Close,
    Closed,
    CloseInput,
    DiscardBytecode,
    Effect,
    End,
    Ended,
    Exited,
    Fail,
    Handshake,
    HandshakeFailed,
    HandshakeForeign,
    HandshakeOk,
    Idle,
    Interrupted,
    Log,
    Pause,
    Ready,
    Request,
    Reset,
    RestartPolicy,
    Retired,
    Send,
    Spawn,
    Spawned,
    SpawnFailed,
    SupervisionEvent,
    SupervisionState,
    Violated,
    ViolatedPastCommit,
    transition,
)
from app.platform.observability import Event, EventSink
from app.platform.observability.events import ENGINE_BYTECODE

STDERR_LINES = 200
STDERR_LINE_BYTES = 64 * 1024
DROPPED_ENV_PREFIXES = ("FT_", "PYTHON")
HASH_SEED = "0"
# The packaged layout (ADR-0056; `shell/sidecar/build_sidecar.py` builds it): `<sidecar>/python` is the interpreter's
# prefix, and the engine's own site-packages sit beside it at `<sidecar>/engine/site-packages`.
ENGINE_SITE_PARTS = ("engine", "site-packages")
# The engine's flags (§5.1): no user site, no `site` import, no bytecode written, no unsafe path first. Never `-I`.
ENGINE_FLAGS = ("-s", "-S", "-B", "-P")
FILL_FLAGS = ("-s", "-S", "-P")  # the bytecode fill writes bytecode (under `-X pycache_prefix` only)
# The bootstrap: `-S` left the interpreter's own site-packages out; this adds the engine's site (the first argument
# after the code), processing its .pth files, and nothing else.
PATH_SETUP = "import site, sys; site.addsitedir(sys.argv.pop(1))"
ENGINE_RUN = "import runpy; runpy.run_module('ledger_engine', run_name='__main__', alter_sys=True)"
# The fill compiles every folder on the engine's path (the interpreter's library, the engine's site-packages and, in
# development, its source through the venv's .pth); a file that does not compile is left to its source.
BYTECODE_FILL = (
    "import compileall, os, sys; "
    "[compileall.compile_dir(p, quiet=2, workers=0) for p in list(sys.path) if os.path.isdir(p)]"
)
BYTECODE_COMPLETE = ".complete"
CREATE_NO_WINDOW = 0x08000000  # Windows process creation flag: a console program opens no window


def packaged_engine_site(prefix: Path) -> Path:
    """The engine's site-packages in the bundle, from the bundled interpreter's prefix (`sys.prefix`)."""
    return prefix.parent.joinpath(*ENGINE_SITE_PARTS)


def venv_site(venv: Path) -> Path:
    """A virtual environment's site-packages (the engine's, in development); a path that does not exist when there is
    none, so the start fails as a start, never by finding the engine elsewhere."""
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    found = sorted(venv.glob("lib/python*/site-packages"))
    return found[0] if len(found) == 1 else venv / "lib" / "site-packages"


def bootstrap_argv(python: Path, site: Path, code: str, flags: tuple[str, ...] = ENGINE_FLAGS) -> tuple[str, ...]:
    """`python` with `flags`, running `code` after the path setup has added `site` (and nothing else)."""
    return (str(python), *flags, "-c", f"{PATH_SETUP}; {code}", str(site))


def bytecode_build_dir(app_local_data: Path, python: Path, site: Path, version: str) -> Path:
    """`app_local_data/engine/bytecode/<build>/` (§5.1): one folder per interpreter, engine site and app version."""
    build = hashlib.sha256(f"{python}\0{site}\0{version}".encode()).hexdigest()[:16]
    return app_local_data / "engine" / "bytecode" / build


def creation_flags(os_name: str) -> int:
    """Process creation flags for the engine: no console window on Windows."""
    return CREATE_NO_WINDOW if os_name == "nt" else 0


def next_request_id(previous: int) -> int:
    """The id after `previous`: 1..MAX_ID, wrapping (one request is in flight, so ids never clash)."""
    return previous % MAX_ID + 1


def engine_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The engine's environment: the parent's, without `FT_*` and `PYTHON*` variables, with `PYTHONHASHSEED=0`."""
    kept = {k: v for k, v in environ.items() if not k.upper().startswith(DROPPED_ENV_PREFIXES)}
    return {**kept, "PYTHONHASHSEED": HASH_SEED}


@dataclass(frozen=True, slots=True)
class EngineBytecode:
    """The bytecode directory of one engine build and the command that fills it (§5.1)."""

    directory: Path
    fill_argv: tuple[str, ...]

    def ready(self) -> bool:
        """Filled completely: the fill marks it last."""
        return (self.directory / BYTECODE_COMPLETE).is_file()

    def discard(self) -> None:
        """Delete it, the mark first, so a half-deleted folder is never taken as filled."""
        with contextlib.suppress(OSError):
            (self.directory / BYTECODE_COMPLETE).unlink(missing_ok=True)
        shutil.rmtree(self.directory, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class EngineCommand:
    """How to start the engine: the argv (without `--parent-pid`, which the client adds), the environment (None:
    inherit unchanged, for stand-in engines in tests), the engine lock every engine holds for its life (S0053;
    engine-jobs §5.1: the composition root names `app_local_data/locks/engine.lock`; None for the packaged
    self-check, which does no ledger work, and for stand-ins), and the bytecode directory (None: none used)."""

    argv: tuple[str, ...]
    env: Mapping[str, str] | None
    engine_lock: Path | None = None
    bytecode: EngineBytecode | None = None

    @classmethod
    def for_venv(
        cls,
        venv: Path,
        *,
        test_ops: bool = False,
        environ: Mapping[str, str] | None = None,
        engine_lock: Path | None = None,
        bytecode_dir: Path | None = None,
    ) -> EngineCommand:
        """The engine installed in a virtual environment (development and tests), under the same bootstrap as the
        bundle's, with the venv's site-packages."""
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return cls._bootstrapped(python, venv_site(venv), test_ops, environ, engine_lock, bytecode_dir)

    @classmethod
    def for_packaged(
        cls,
        python: Path,
        site: Path,
        *,
        test_ops: bool = False,
        environ: Mapping[str, str] | None = None,
        engine_lock: Path | None = None,
        bytecode_dir: Path | None = None,
    ) -> EngineCommand:
        """The engine in the bundle: the shared interpreter `python` with only the engine's `site` (S0038)."""
        return cls._bootstrapped(python, site, test_ops, environ, engine_lock, bytecode_dir)

    @classmethod
    def _bootstrapped(
        cls,
        python: Path,
        site: Path,
        test_ops: bool,
        environ: Mapping[str, str] | None,
        engine_lock: Path | None,
        bytecode_dir: Path | None,
    ) -> EngineCommand:
        argv = (*bootstrap_argv(python, site, ENGINE_RUN), *(("--test-ops",) if test_ops else ()))
        bytecode = (
            None
            if bytecode_dir is None
            else EngineBytecode(bytecode_dir, bootstrap_argv(python, site, BYTECODE_FILL, FILL_FLAGS))
        )
        env = engine_env(os.environ if environ is None else environ)
        return cls(argv=argv, env=env, engine_lock=engine_lock, bytecode=bytecode)

    def spawn_argv(self, parent_pid: int, pycache: Path | None = None) -> list[str]:
        """The argv the client spawns: the interpreter (reading bytecode from `pycache` when given), the command,
        the parent's pid for the engine's watchdog, and the engine lock."""
        prefix = ["-X", f"pycache_prefix={pycache}"] if pycache is not None else []
        lock = ["--engine-lock", str(self.engine_lock)] if self.engine_lock is not None else []
        return [self.argv[0], *prefix, *self.argv[1:], "--parent-pid", str(parent_pid), *lock]


def _no_log(event: Event, /, **fields: object) -> None:
    """The sink when none is given (the packaged self-check, tests)."""


class WriteFailed:
    """The writer could not hand a request to the engine (its stdin is closed: the engine ended or closed it). The
    class itself is the marker in the reply queue (a type, so no module-level instance, INV-11)."""


type _Reply = bytes | type[WriteFailed] | None
"""A reply line, a failed write, or None at the end of the engine's output."""
type _Ours = EndReason | Literal["close"]
"""Why we ended an engine: recorded before its group is signalled (§5.2)."""


class _AloneProcess:
    """Windows: no process group; the engine alone is ended and waited for. TODO(BL-0087): the engine's Job Object
    nested in the shell's, with kill-on-close (engine-jobs §5.2)."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._lock = threading.Lock()
        self._reaping = False
        self.exited = threading.Event()

    @property
    def reaped(self) -> bool:
        with self._lock:
            return self._reaping

    def leader_exited(self) -> bool:
        return self.exited.is_set()

    def signal(self) -> bool:
        with self._lock:
            if self._reaping:
                return False
            with contextlib.suppress(OSError):
                self._proc.kill()
            return True

    def drain(self) -> Reaped:
        code = self._proc.wait()
        with self._lock:
            self._reaping = True
        self.exited.set()
        return Reaped(code, 0)


@dataclass(eq=False)
class _Running:
    proc: subprocess.Popen[bytes]
    serial: int
    group: EngineGroup | _AloneProcess
    replies: queue.Queue[_Reply] = field(default_factory=lambda: queue.Queue[_Reply]())
    requests: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue[bytes | None]())
    stderr: deque[str] = field(default_factory=lambda: deque[str](maxlen=STDERR_LINES))
    threads: list[threading.Thread] = field(default_factory=lambda: list[threading.Thread]())
    drained: threading.Event = field(default_factory=threading.Event)
    wake: int | None = None  # POSIX: the write end the reaper uses to end the reply stream once the engine is reaped
    reason: _Ours | None = None
    cause: Literal["stop", "input_closed"] | None = None  # an end we caused without a reason of the caller's (§5.2)
    past_commit: bool = False
    in_flight: tuple[int, str] | None = None
    gate: Literal["none", "pending", "proceeded"] = "none"
    committed: bool = False


@dataclass(frozen=True, slots=True)
class _Lost:
    """No valid answer to a request that went out (an observation; the table decides what it means)."""

    kind: Literal["crashed", "protocol_violation"]
    detail: str


@dataclass(frozen=True, slots=True)
class _Foreign:
    """The handshake was answered in another protocol."""

    what: str


def _pump_requests(pipe: IO[bytes], requests: queue.Queue[bytes | None], replies: queue.Queue[_Reply]) -> None:
    """Each line to the engine's stdin, until None (close stdin: the engine finishes and exits) or a failed write
    (reported as `WriteFailed` beside the replies). The only user of stdin: the caller waits for the reply queue,
    never for this write, so an engine that does not read cannot hold the caller (S0042). The pipe is closed here,
    when the thread ends, because closing it from another thread would wait for a write in progress."""
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


DRAIN_AFTER_REAP_BYTES = 4 * 1024 * 1024
"""After the engine is reaped, at most this much is still read from its output: all a dead engine can have left in
the pipe (a pipe's buffer is 16 KiB to 1 MiB), so a child that escaped the group and keeps writing cannot hold us."""


class _Lines:
    """Splits the output into reply lines as `readline(MAX_LINE_BYTES + 2)` would: a line longer than the limit is
    passed on cut, without its newline, so the decoder refuses it. Linear in the output: each byte is scanned once
    (S0057 verifier R2-F4)."""

    def __init__(self, replies: queue.Queue[_Reply]) -> None:
        self._replies = replies
        self._buf = bytearray()
        self._scanned = 0  # the bytes before this offset hold no newline

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        limit = MAX_LINE_BYTES + 2
        start = 0
        while True:
            end = self._buf.find(b"\n", max(self._scanned, start), start + limit)
            if end >= 0:
                self._replies.put(bytes(self._buf[start : end + 1]))
                start = end + 1
            elif len(self._buf) - start >= limit:
                self._replies.put(bytes(self._buf[start : start + limit]))
                start += limit
            else:
                self._scanned = len(self._buf)
                break
            self._scanned = start
        del self._buf[:start]
        self._scanned -= start

    def end(self) -> None:
        if self._buf:
            self._replies.put(bytes(self._buf))  # a last line without its newline: the decoder calls it cut off
            self._buf = bytearray()
        self._replies.put(None)


def _pump_lines_until_reaped(pipe: IO[bytes], replies: queue.Queue[_Reply], wake: int) -> None:
    """POSIX: reply lines into the queue; None at the end of the stream, or once the engine is reaped (the reaper
    writes to `wake`): then what the dead engine left in the pipe is read first, so its last reply is never lost
    behind the end, and a child that left the group (`setsid`, §15) holding the output open cannot hold the call
    (S0057 verifier R1-F2). Raw reads on the descriptor; the buffered `pipe` object is never read."""
    fd = pipe.fileno()
    lines = _Lines(replies)
    both, out = select.poll(), select.poll()  # poll, not select: any descriptor number (R2-F5)
    for poller, fds in ((both, (fd, wake)), (out, (fd,))):
        for each in fds:
            poller.register(each, select.POLLIN)
    try:
        while True:
            ready = {each for each, _ in both.poll()}
            if wake in ready:  # the engine is reaped: read what it left, without waiting, then end the stream
                left = DRAIN_AFTER_REAP_BYTES
                while left > 0 and out.poll(0):
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    left -= len(chunk)
                    lines.feed(chunk)
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            lines.feed(chunk)
    except (OSError, ValueError):
        pass
    finally:
        lines.end()
        with contextlib.suppress(OSError):
            os.close(wake)
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


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


class _Filler:
    """The one background fill of the bytecode directory (§5.1); ended with the client."""

    def __init__(self, bytecode: EngineBytecode, env: Mapping[str, str] | None, ops: GroupOps, log: EventSink) -> None:
        self._bytecode = bytecode
        self._env = dict(env) if env is not None else None
        self._ops = ops
        self._log = log
        self._lock = threading.Lock()
        self._stopped = False
        self._group: EngineGroup | _AloneProcess | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="engine-bytecode-fill", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._group is not None:
                self._group.signal()

    def _run(self) -> None:
        directory = self._bytecode.directory
        failure: str | None = None
        try:
            if directory.exists():  # a folder left by a fill that did not finish
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
            argv = self._bytecode.fill_argv
            with self._lock:
                if self._stopped:
                    return
                proc = subprocess.Popen(  # noqa: S603 (argv from EngineCommand, built by the composition root)
                    [argv[0], "-X", f"pycache_prefix={directory}", *argv[1:]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=self._env,
                    close_fds=True,
                    start_new_session=os.name == "posix",
                    creationflags=creation_flags(os.name),
                )
                group = EngineGroup(self._ops, proc.pid) if os.name == "posix" else _AloneProcess(proc)
                self._group = group
            reaped = group.drain()
            proc.returncode = reaped.returncode
            if reaped.returncode == 0 and not self._stopped:
                (directory / BYTECODE_COMPLETE).write_bytes(b"")
            elif not self._stopped:
                failure = f"the fill exited with code {reaped.returncode}"
        except OSError as e:
            failure = f"the fill failed: {type(e).__name__}: {e}"
        if failure is not None:
            with contextlib.suppress(Exception):
                self._log(ENGINE_BYTECODE, outcome="fill_failed", detail=failure)


class EngineClient:
    """`EnginePort` over a child process, supervised by the table in `app.platform.engine_supervision`.
    Thread-safe: calls are serialized; `end`, `proceed` and `close` never wait for a call."""

    def __init__(
        self,
        command: EngineCommand,
        policy: RestartPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        log: EventSink | None = None,
        group_ops: GroupOps | None = None,
        on_engine_ended: Callable[[EngineEnd], None] | None = None,
    ) -> None:
        self._command = command
        self._on_stderr = on_stderr
        self._log: EventSink = log or _no_log
        self._policy = policy or RestartPolicy()
        self._monotonic = monotonic
        self._pause = pause or self._wait_unless_ended
        self._ops: GroupOps = group_ops or PosixGroupOps()
        self._on_engine_ended = on_engine_ended
        self._lock = threading.RLock()  # serializes the public calls
        self._signals = threading.Condition(threading.Lock())  # guards what `end`, `proceed` and `close` touch
        self._state: SupervisionState = Idle()
        self._run: _Running | None = None
        self._previous: _Running | None = None
        self._in_call = False
        self._pending: _Ours | None = None
        self._closing = False
        self._needs_settle = False
        self._serial = 0
        self._next_id = 0
        self._last_stderr: deque[str] = deque[str]()
        self._filler: _Filler | None = None

    # -- the port ---------------------------------------------------------------------------------------------

    def start(self) -> EngineInfo | EngineFailure:
        with self._call():
            ready = self._ensure_running()
            return ready if isinstance(ready, EngineFailure) else ready[1]

    def ping(self) -> Pong | EngineRefusal | EngineFailure:
        with self._call():
            out = self._request("ping", {}, decode_pong, None)
            return Pong() if isinstance(out, Accepted) else out

    def call(
        self, op: str, params: Mapping[str, object], *, on_event: Callable[[EngineEvent], None] | None = None
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        with self._call():
            out = self._request(op, params, None, on_event)
            return out.result if isinstance(out, Accepted) else out

    def proceed(self) -> bool:
        """`proceed` for the gate of the request in flight; False, and nothing sent, if no gate is pending."""
        with self._signals:
            run = self._run
            if run is None or run.reason is not None or run.gate != "pending" or run.in_flight is None:
                return False
            rid, op = run.in_flight
            run.gate = "proceeded"
            run.past_commit = True  # until this call ends: only Quit anyway ends this engine now (§4)
            run.requests.put(encode_proceed(rid, op))
            return True

    def end(self, reason: EndReason) -> None:
        """Record `reason` and signal the engine's group once (§5.2). Never waits."""
        with self._signals:
            if reason == "retire" and self._in_call:
                msg = "retire ends an idle engine after its job's final reply, never a call in flight"
                raise RuntimeError(msg)
            self._mark_ended(reason)
            self._needs_settle = True
        self._settle_if_idle()

    def close(self) -> None:
        """Record `close`, signal the engine's group once and return, without waiting for a call or the reap."""
        with self._signals:
            self._closing = True
            self._mark_ended("close")
            self._needs_settle = True
            filler = self._filler
        if filler is not None:
            filler.stop()
        self._settle_if_idle()

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
    def serial(self) -> int | None:
        """The running engine's spawn number in this client (its `EngineEnd` carries the same), if one is running."""
        run = self._run
        return run.serial if run is not None else None

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

    # -- our ends ---------------------------------------------------------------------------------------------

    def _mark_ended(self, reason: _Ours) -> None:
        """Under `_signals`: record the reason on the running engine and signal its group once; a call in flight
        that has no engine to signal (a start, a backoff) sees `_pending` and ends there. **Past the commit point**
        (`proceed` sent for the call in flight) only Quit anyway ends the engine (§4: "a writing job past its commit
        point: nothing: the job finishes"; INV-01): a Cancel or a close changes nothing, and the close is settled
        when the job has finished."""
        run = self._run
        if reason != "quit_anyway" and run is not None and run.past_commit:
            return
        if run is not None and run.reason is None and not (run.group.exited.is_set() or run.group.leader_exited()):
            run.reason = reason  # an engine that already ended by itself keeps that truth: it is no end of ours
            run.group.signal()
        if self._in_call and self._pending is None:
            self._pending = reason
        self._signals.notify_all()

    def _settle_if_idle(self) -> None:
        """With no call in flight, feed our end of the idle engine (and a close) to the table at once, so `state` is
        current. `end` and `close` set `_needs_settle` before they try the call lock; whoever holds the lock then
        runs this again after releasing it, so a request to settle is never lost between the two (S0057 verifier
        R1-F4). A call in flight settles at its own end."""
        while True:
            with self._signals:
                if not self._needs_settle:
                    return
            if not self._lock.acquire(blocking=False):
                return  # its holder settles once it has released the lock
            try:
                if self._in_call:
                    return  # the call in flight (this thread's, from its on_event) settles at its end
                with self._signals:
                    self._needs_settle = False
                self._settle()
            finally:
                self._lock.release()

    def _settle(self) -> None:
        """At rest: an engine we ended, or one that died by itself, is fed to the table; then a pending close."""
        run = self._run
        if run is not None and isinstance(self._state, Ready):
            if run.reason is not None:
                self._drive(self._ours(run.reason, run))
            elif run.group.exited.is_set() or run.group.leader_exited():  # it died since the last request: a stop
                self._drive(Exited("the engine exited between requests", self._monotonic()))
        if self._closing and not isinstance(self._state, Closed):
            with self._signals:
                run = self._run
                if run is not None and run.reason is None and not run.group.exited.is_set():
                    run.reason = "close"  # a close deferred past a commit point is still ours (R2-F1)
            self._drive(Close())

    def _ours(self, reason: _Ours, run: _Running | None = None) -> SupervisionEvent:
        if reason == "close":
            return Close()
        if reason == "retire":
            return Retired()
        past = run is not None and run.past_commit
        return Cancelled(reason, self._monotonic(), past_commit=past)

    def _wait_unless_ended(self, seconds: float) -> None:
        """The real backoff pause: it ends early when the waiting call is ended (`end`, `close`)."""
        with self._signals:
            self._signals.wait_for(lambda: self._pending is not None, timeout=seconds)

    # -- the shell around the table ---------------------------------------------------------------------------

    @contextlib.contextmanager
    def _call(self) -> Generator[None]:
        """A public call: it holds the lock, and an exception that leaves the table in a passing state (a start, a
        backoff or a request under way) is fed in as `Interrupted` before it goes on, so the next call finds the table
        at rest, wherever the exception landed (a signal's included). For a request that is out, the table stops the
        engine: its reply may still come and would be handed to the next request (S0035 verifier F4c)."""
        try:
            with self._lock:
                if self._in_call:
                    msg = "an engine call from inside a call's on_event"
                    raise RuntimeError(msg)
                with self._signals:
                    self._in_call = True
                    self._pending = None
                try:
                    self._settle()
                    yield
                except BaseException as e:
                    if not isinstance(self._state, RESTING):
                        self._feed(Interrupted(f"the client failed: {type(e).__name__}", self._monotonic()))
                    raise
                finally:
                    with self._signals:
                        self._in_call = False
                        self._pending = None
                        if self._run is not None:
                            self._run.past_commit = False  # the commit point is the request's; it ends with the call
                    self._settle()
        finally:
            self._settle_if_idle()  # an end or close that came while this call held the lock (R1-F4)

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
            case End():
                self._end_run()
            case CloseInput():
                run = self._run
                if run is not None:
                    run.cause = "input_closed"  # its end at the end of this input is ours (R1-F3)
                    run.requests.put(None)  # the writer closes stdin: the engine finishes the request in hand
            case DiscardBytecode():
                if self._command.bytecode is not None:
                    self._command.bytecode.discard()
                self._filler = None  # the next spawn that may use it fills it again
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

    def _end_run(self) -> None:
        """The table's `End`: signal the engine's group once (unless already signalled or reaped) and let it go; its
        reaper drains and reaps it. The next spawn waits for that drain."""
        with self._signals:
            run, self._run = self._run, None
            if run is None:
                return
            if run.reason is None:
                if run.cause is None and not (run.group.exited.is_set() or run.group.leader_exited()):
                    run.cause = "stop"  # the table ends a live engine after a failure: our kill, not the OS's
                run.group.signal()
            self._previous = run
        run.requests.put(None)  # an idle writer ends; one inside a write ends when the write fails
        self._last_stderr = run.stderr

    def _perform(self, effect: Spawn | Handshake | Pause) -> SupervisionEvent:
        match effect:
            case Spawn(bytecode=bytecode):
                return self._spawn(bytecode)
            case Handshake():
                return self._handshake()
            case Pause(seconds=seconds):
                self._pause(seconds)
                with self._signals:
                    pending = self._pending
                return BackoffElapsed() if pending is None else self._ours(pending)

    def _ensure_running(self) -> tuple[_Running, EngineInfo] | EngineFailure:
        self._settle()
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
        validate: Callable[[Mapping[str, object]], object] | None,
        on_event: Callable[[EngineEvent], None] | None,
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
        out = self._exchange(run, line, on_event)  # an exception from here on is fed in as `Interrupted` (`_call`)
        if isinstance(out, Accepted) and validate is not None:
            checked = validate(out.result)
            if isinstance(checked, Violation):
                out = _Lost("protocol_violation", f"{checked.detail}; the engine was stopped")
        if isinstance(out, _Lost):
            event = self._lost(run, out)
            failure = self._feed(event)
            if isinstance(event, ViolatedPastCommit):  # `Finishing`: the input is closed; read to the end
                self._discard_until_end(run)
                failure = self._feed(self._observed_end(run) or Ended("the engine's output ended", self._monotonic()))
            if not isinstance(failure, EngineFailure):  # pragma: no cover (every end in Busy fails the caller)
                msg = "the supervision table ended a request without failing it"
                raise RuntimeError(msg)  # noqa: TRY004 (a broken table, not a wrong argument)
            return failure
        self._feed(Answered())
        return out

    def _observed_end(self, run: _Running) -> SupervisionEvent | None:
        """Our end of this engine, if we recorded one: it wins over what the output did (§5.2)."""
        return None if run.reason is None else self._ours(run.reason, run)

    def _lost(self, run: _Running, out: _Lost) -> SupervisionEvent:
        ours = self._observed_end(run)
        if ours is not None:
            return ours
        if out.kind == "crashed":
            return Ended(out.detail, self._monotonic())
        if self._gate_of(run) == "proceeded":  # §5.3: past the commit point, never signal; close the input, wait
            return ViolatedPastCommit(out.detail)
        return Violated(out.detail, self._monotonic())

    @staticmethod
    def _discard_until_end(run: _Running) -> None:
        """Read and discard the engine's output until it ends (§5.3: discarding keeps the pipe from filling and
        blocking the engine)."""
        while run.replies.get() is not None:
            pass

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
        self, run: _Running, request: tuple[int, str, bytes], on_event: Callable[[EngineEvent], None] | None
    ) -> Accepted | EngineRefusal | _Lost:
        """Write the request and read its job messages and its answer: an observation only; the table decides."""
        out = self._exchange_any(run, request, on_event, handshake=False)
        if isinstance(out, _Foreign):  # pragma: no cover (read only for our own handshake)
            return _Lost("protocol_violation", out.what)
        return out

    def _exchange_any(
        self,
        run: _Running,
        request: tuple[int, str, bytes],
        on_event: Callable[[EngineEvent], None] | None,
        *,
        handshake: bool,
    ) -> Accepted | EngineRefusal | _Lost | _Foreign:
        """`_exchange`; for our own handshake (`handshake`), a reply in another protocol is `_Foreign`. The request is
        handed to the writer thread and the caller waits for the reply queue only, with no time limit (§5.4)."""
        rid, op, line = request
        with self._signals:
            run.in_flight, run.gate, run.committed = (rid, op), "none", False
        run.requests.put(line)
        try:
            while True:
                raw = run.replies.get()
                if raw is None:
                    return _Lost("crashed", "the engine's output ended")
                if not isinstance(raw, bytes):  # WriteFailed: the request never reached the engine
                    return _Lost("crashed", "the engine's input closed")
                message = decode_reply(raw, rid, op)
                if isinstance(message, Violation):
                    foreign = foreign_version(raw) if handshake else None
                    if foreign is not None:  # another protocol: incompatible, never retried (S0035 verifier F2)
                        return _Foreign(f"the engine speaks protocol {foreign}")
                    return _Lost("protocol_violation", f"{message.detail}; the engine was stopped")
                if isinstance(message, (Progress, GateReached, Committed)):
                    wrong = "a job message during the handshake" if handshake else self._out_of_order(run, message)
                    if wrong is not None:
                        return _Lost("protocol_violation", f"{wrong}; the engine was stopped")
                    if on_event is not None:
                        on_event(message)
                    continue
                if self._gate_of(run) == "pending":
                    return _Lost("protocol_violation", "a reply while the gate waited for proceed")
                decision = message.decision
                if decision is not None and on_event is not None:
                    on_event(decision)
                return message
        finally:
            with self._signals:
                run.in_flight = None

    def _gate_of(self, run: _Running) -> Literal["none", "pending", "proceeded"]:
        """Where the request in flight stands at its gate (`proceed()` moves it from another thread)."""
        with self._signals:
            return run.gate

    def _out_of_order(self, run: _Running, message: Progress | GateReached | Committed) -> str | None:
        """A job message's place (§4, §6.1): one gate; `committed` once, only after `proceed`."""
        with self._signals:
            if isinstance(message, GateReached):
                if run.gate != "none":
                    return "a second gate in one request"
                run.gate = "pending"
            elif isinstance(message, Committed):
                if run.gate != "proceeded" or run.committed:
                    return "committed before proceed" if run.gate != "proceeded" else "committed twice"
                run.committed = True
        return None

    def _spawn(self, use_bytecode: bool) -> SupervisionEvent:
        """Start the engine once the previous one is drained (§5.1), unless the waiting call was ended meanwhile."""
        command = self._command
        with self._signals:
            previous = self._previous
            self._signals.wait_for(lambda: self._pending is not None or previous is None or previous.drained.is_set())
            if self._pending is not None:
                return self._ours(self._pending)
            self._previous = None
            pycache = self._bytecode_for_spawn(use_bytecode)
            argv = command.spawn_argv(os.getpid(), pycache)
            env = dict(command.env) if command.env is not None else None
            try:
                proc = subprocess.Popen(  # noqa: S603 (argv from EngineCommand, built by the composition root)
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    close_fds=True,
                    start_new_session=os.name == "posix",
                    creationflags=creation_flags(os.name),
                )
            except OSError as e:
                return SpawnFailed(f"the engine could not be started: {e}", self._monotonic())
            self._serial += 1
            group = EngineGroup(self._ops, proc.pid) if os.name == "posix" else _AloneProcess(proc)
            run = _Running(proc, self._serial, group)
            self._run = run  # at once, so an interrupted start ends it (the table's `End`)
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:  # pragma: no cover (pipes were requested)
            return SpawnFailed("the engine has no pipes", self._monotonic())
        if os.name == "posix":
            wake_read, run.wake = os.pipe()
            replies = threading.Thread(
                target=_pump_lines_until_reaped,
                args=(proc.stdout, run.replies, wake_read),
                name="engine-replies",
                daemon=True,
            )
        else:  # TODO(BL-0087): Windows pipes cannot be selected; the replies end only at the end of the output
            replies = threading.Thread(
                target=_pump_lines, args=(proc.stdout, run.replies), name="engine-replies", daemon=True
            )
        threads = (
            threading.Thread(target=self._reap, args=(run,), name="engine-reaper", daemon=True),
            threading.Thread(
                target=_pump_requests, args=(proc.stdin, run.requests, run.replies), name="engine-requests", daemon=True
            ),
            replies,
            threading.Thread(
                target=_pump_stderr, args=(proc.stderr, run.stderr, self._on_stderr), name="engine-stderr", daemon=True
            ),
        )
        for thread in threads:
            thread.start()
            run.threads.append(thread)
        return Spawned(proc.pid, bytecode=pycache is not None)

    def _bytecode_for_spawn(self, allowed: bool) -> Path | None:
        """The bytecode directory to start with, if allowed and complete; else None, and a fill begins (once)."""
        bytecode = self._command.bytecode
        if not allowed or bytecode is None:
            return None
        if bytecode.ready():
            return bytecode.directory
        if self._filler is None and not self._closing:
            self._filler = _Filler(bytecode, self._command.env, self._ops, self._log)
            self._filler.start()
        return None

    def _reap(self, run: _Running) -> None:
        """The engine's reaper thread: drain its group, reap it, then report its end (§5.2)."""
        reaped = run.group.drain()
        run.proc.returncode = reaped.returncode  # reaped here: `Popen` must never wait for it again
        if run.wake is not None:
            # Reaped and drained: nothing more can come from the engine. The reply pump reads what it left and ends
            # the stream, so a child that left the group holding the output cannot hold the call (R1-F2).
            with contextlib.suppress(OSError):
                os.write(run.wake, b"x")
            with contextlib.suppress(OSError):
                os.close(run.wake)
        with self._signals:
            run.drained.set()
            self._signals.notify_all()
        if self._on_engine_ended is not None:
            end = EngineEnd(run.serial, run.proc.pid, run.reason or run.cause, reaped.returncode, reaped.max_rss_kib)
            with contextlib.suppress(Exception):  # the report's receiver must not take the reaper down
                self._on_engine_ended(end)

    def _handshake(self) -> SupervisionEvent:
        run = self._run
        if run is None:  # pragma: no cover (the table sends a handshake only right after a spawn)
            return HandshakeFailed("handshake: no engine process", self._monotonic())
        request = self._encode("handshake", {})
        if isinstance(request, EngineFailure):  # pragma: no cover (a handshake is a few bytes)
            return HandshakeFailed(f"handshake: {request.detail}", self._monotonic())
        out = self._exchange_any(run, request, None, handshake=True)
        ours = self._observed_end(run)
        if ours is not None and not isinstance(out, (Accepted, EngineRefusal)):
            return ours
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


__all__ = [
    "BYTECODE_COMPLETE",
    "ENGINE_FLAGS",
    "EngineBytecode",
    "EngineClient",
    "EngineCommand",
    "bootstrap_argv",
    "bytecode_build_dir",
    "creation_flags",
    "engine_env",
    "next_request_id",
    "packaged_engine_site",
    "venv_site",
]
