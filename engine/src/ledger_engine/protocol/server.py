"""The engine's protocol server: one request line in, one reply line out (ADR-0054 decisions 2 and 4).

    python -s -S -B -P -c <bootstrap> <site> [--parent-pid PID] [--engine-lock PATH] [--test-ops]

(the main sidecar's bootstrap adds only the engine's site-packages, then runs this module; engine-jobs §5.1. Never
`-I`: it implies `-E`, which ignores `PYTHONHASHSEED`. Tests may run `python -I -m ledger_engine`.)

- **Channel.** At start the engine keeps private copies of its stdin and stdout for the protocol, points fd 1 at
  stderr and fd 0 at the null device. A `print` or a raw write to fd 1 from code running inside the engine (a
  user's Beancount plugin, later) lands on stderr and can never corrupt a reply; nothing inside can read requests.
  The private copies are not inherited by child processes.
- **Order.** Requests are handled one at a time, in order; each gets exactly one reply.
- **A job's messages** (S0057; protocol version 2; engine-jobs §4, §5.4, §6.1 steps 7 and 9, §12): a handler gets a
  `JobContext`: `progress(phase)` sends a progress message; `gate()` sends `gate` and reads the next line, which must
  be `proceed` for this request: on end of input it raises `InputEnded` (the read loop ends, and so the engine; it
  never proceeds), on any other line `GateRefused` (the request is refused `protocol.malformed`, nothing past the
  gate runs); `committed()` only after `proceed`; `decide(...)` sets the one `decision` its reply carries.
- **Refusals** carry a stable key and a plain message (`messages.RefusalKey`); the engine keeps serving after any
  refusal. A handler that raises, or returns a result that is not its op's, is `engine.internal`: the traceback
  goes to stderr, never into the reply. The engine never dies on input (S0035 independent verifier F1): a refusal
  echoes `op` only when it is valid Unicode and at most `MAX_ECHO_CHARS` long (else null), its message is made
  valid Unicode and cut to `MAX_MESSAGE_CHARS`, and the read loop answers anything that still escapes with a fixed
  `engine.internal` reply.
- **Parent-death watchdog** (spike S1): the engine exits when its stdin reaches EOF (after finishing the request in
  hand), and on POSIX also when its parent is no longer `--parent-pid` (polled from the start, so a parent that
  died while the engine was starting is caught), even mid-request and with stdin still open. A parent that is pid 1
  (a main sidecar running as a container's first process) cannot be told apart from re-parenting, so then only
  stdin EOF applies (S0035 independent verifier F6). **Both end the engine's whole group** (S0057; engine-jobs §5.2):
  the sidecar spawns the engine as the leader of its own process group, so on POSIX the watchdog and the
  end-of-input exit send `killpg(0, SIGKILL)`, the engine included, and a plugin's child cannot outlive the engine
  holding a ledger lock. An engine that does not lead its group (run by hand or by a test) only exits: it never
  signals a group that is not its own (`end_own_group`).
  On Windows the engine ends on stdin EOF when the sidecar is gone (its pipe closes), and the shell's Job Object
  (ARCHITECTURE.md §5) covers the sidecar and the engine.
- **The engine lock** (S0053; `design/specs/engine-jobs.md` §5.1; ADR-0064 amendment of 2026-10-28): started with
  `--engine-lock PATH` (the main sidecar always passes `app_local_data/locks/engine.lock`), the engine takes a
  whole-file `flock` on it right after its handshake reply and before it reads any other request, waiting, and holds
  it for its whole life (the descriptor is never closed; the OS releases it when the process and every process that
  inherited it have ended). So an engine of an earlier session (left by a crash, Quit anyway or a quit) keeps a new
  session's engines from doing any ledger work until it has ended, and the new session's recovery never runs beside
  it. Opened like every later ledger-set lock (`os.open`, so a forked child holds it as it holds those). POSIX only;
  Windows is BL-0087.
- **Recover-all's ops** (S0062; `ledger_engine.protocol.recovery`): `recover_list`, `recover_try` and `recover_act`
  are answered only by an engine that holds the engine lock (`engine.no_engine_lock` otherwise), so the listing,
  each try and the blocked screen's actions never run beside an engine of an earlier session.
- **No resident ledger (INV-10).** The engine holds no ledger between requests. The overlay load arrives with its
  slice (WS-WRITE, BL-0003); the main sidecar's engine job queue runs one job at a time (ADR-0064, BL-0109).
- `--test-ops` adds ops for the process tests (`test.stall`, `test.noise`, `test.fail`, `test.read_stdin`,
  `test.gate`, `test.child`, `test.interpreter`, `test.import_plugin`). They are not part of the protocol schema
  and the main sidecar never passes the flag outside tests.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import IO, Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from ledger_engine.protocol.messages import (
    MAX_ECHO_CHARS,
    MAX_ID,
    MAX_LINE_BYTES,
    OPS,
    PROTOCOL_VERSION,
    REPLY_MODELS,
    CommittedEvent,
    Decision,
    DecisionScalar,
    GateEvent,
    HandshakeResult,
    Message,
    NoParams,
    Phase,
    PingResult,
    ProceedControl,
    ProgressEvent,
    Refusal,
    RefusalBody,
    RefusalKey,
)
from ledger_engine.protocol.recovery import RECOVERY_HANDLERS, BadParamsError

ENVELOPE = frozenset({"v", "id", "op", "params"})
PARENT_POLL_SECONDS = 0.25
EXIT_PARENT_GONE = 3
INIT_PID = 1
MAX_MESSAGE_CHARS = 1000


class JobChannel(Protocol):
    """Where a job's messages go and where its `proceed` comes from: the protocol channel."""

    def send(self, raw: bytes) -> None: ...

    def read_line(self) -> bytes: ...


class InputEnded(Exception):
    """The engine's input ended while a job waited at its gate: the sidecar is gone or closed our input. The job
    never proceeds; the read loop ends, and with it the engine (its end-of-input exit)."""


class GateRefused(Exception):
    """The line read at the gate was not `proceed` for this request: the request is refused, nothing past the gate
    runs."""


class _NoChannel:
    """The channel of a request handled outside a read loop (`Engine.handle` called directly): no input, so a gate
    never proceeds."""

    def send(self, raw: bytes) -> None:
        pass

    def read_line(self) -> bytes:
        return b""


class JobContext:
    """One request's job messages (engine-jobs §4, §5.4, §6.1 steps 7 and 9, §12)."""

    def __init__(self, rid: int, op: str, channel: JobChannel) -> None:
        self._rid = rid
        self._op = op
        self._channel = channel
        self._gated = False
        self._proceeded = False
        self.decision: Decision | None = None

    def _send(self, message: BaseModel) -> None:
        self._channel.send(message.model_dump_json().encode() + b"\n")

    def progress(self, phase: Phase) -> None:
        self._send(ProgressEvent(v=2, id=self._rid, op=self._op, event="progress", phase=phase))

    def gate(self) -> None:
        """Send `gate` and wait for `proceed` (§4: "The engine sends `gate` and waits"). Returns only on `proceed`
        for this request."""
        if self._gated:
            msg = "a second gate in one request"
            raise RuntimeError(msg)
        self._gated = True
        self._send(GateEvent(v=2, id=self._rid, op=self._op, event="gate"))
        line = self._channel.read_line()
        if not line:
            raise InputEnded
        if not _is_proceed(line, self._rid, self._op):
            msg = "Expected proceed for this request at its commit point; nothing was done past it."
            raise GateRefused(msg)
        self._proceeded = True

    def committed(self) -> None:
        """The save landed (§6.1 step 9); only after `proceed`."""
        if not self._proceeded:
            msg = "committed before proceed"
            raise RuntimeError(msg)
        self._send(CommittedEvent(v=2, id=self._rid, op=self._op, event="committed"))

    def decide(self, table: str, version: int, inputs: dict[str, DecisionScalar], output: str) -> None:
        """The decision this request's reply carries (§12); one per reply."""
        if self.decision is not None:
            msg = "one decision per reply"
            raise RuntimeError(msg)
        self.decision = Decision(table=table, version=version, inputs=inputs, output=output)


def _is_proceed(line: bytes, rid: int, op: str) -> bool:
    """The line is exactly `proceed` for this request (strict: no coercion, no other key, this version)."""
    try:
        control = ProceedControl.model_validate_json(line.rstrip(b"\n"), strict=True)
    except (ValueError, RecursionError):
        return False
    return control.id == rid and control.op == op


@dataclass(frozen=True, slots=True)
class Handler:
    params: type[Message]
    run: Callable[[Message, JobContext], Message]
    needs_engine_lock: bool = False


def _handshake(_: Message, __: JobContext) -> Message:
    return HandshakeResult(
        protocol=PROTOCOL_VERSION,
        engine_version=version("finzytrack-engine"),
        beancount_version=version("beancount"),  # package metadata only; Beancount is not imported here (INV-02)
    )


def _ping(_: Message, __: JobContext) -> Message:
    return PingResult(status="ok")


def _stall(_: Message, __: JobContext) -> Message:
    _log("test.stall: stalling until killed")
    threading.Event().wait()
    raise AssertionError("unreachable")


def _noise(_: Message, __: JobContext) -> Message:
    print("noise via print")  # noqa: T201 (the point of this test op)
    sys.stdout.flush()
    os.write(1, b"noise via fd 1\n")
    return PingResult(status="ok")


def _fail(_: Message, __: JobContext) -> Message:
    msg = "boom (test.fail)"
    raise RuntimeError(msg)


def _read_stdin(_: Message, __: JobContext) -> Message:
    """Code inside the engine reading fd 0 gets end of file, never the next request."""
    if os.read(0, 65536):
        msg = "fd 0 still carries the request stream"
        raise RuntimeError(msg)
    return PingResult(status="ok")


class PathParams(Message):
    path: str


class LockParams(Message):
    lock: str


class ChildResult(Message):
    pid: int


class FolderParams(Message):
    folder: str


class ValueResult(Message):
    value: int


class InterpreterResult(BaseModel):
    """What the engine's interpreter runs with (S0057; engine-jobs §5.1)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    hash_abc: int
    hash_seed: str | None
    no_site: int
    no_user_site: int
    safe_path: bool
    isolated: int
    ignore_environment: int
    dont_write_bytecode: bool
    pycache_prefix: str | None


def _gate(params: Message, ctx: JobContext) -> Message:
    """A stand-in for a save: its first durable write (the file at `path`) only after `proceed`."""
    assert isinstance(params, PathParams)  # noqa: S101 (the handler's own params model)
    ctx.progress("checking_change")
    ctx.gate()
    with open(params.path, "w", encoding="utf-8") as fh:
        fh.write("post")
    ctx.committed()
    ctx.progress("updating_views")
    ctx.decide("test.gate", 1, {"wrote": True}, "committed")
    return PingResult(status="ok")


def _child(params: Message, _: JobContext) -> Message:
    """A stand-in for a plugin's child process holding a ledger lock: the engine takes a `flock` on `lock` and starts
    a child that inherits the descriptor (so it holds the lock too) and sleeps."""
    assert isinstance(params, LockParams)  # noqa: S101 (the handler's own params model)
    import fcntl  # POSIX only (test op)

    fd = os.open(params.lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3600)"],
        pass_fds=(fd,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return ChildResult(pid=child.pid)


def _import_plugin(params: Message, _: JobContext) -> Message:
    """A stand-in for a user's plugin: `ft_test_plugin` imported from a user's folder (S0057 verifier R1-F5). A
    fixed name in a plain import statement, as a ledger's plugin import is by name only in the gateway."""
    assert isinstance(params, FolderParams)  # noqa: S101 (the handler's own params model)
    sys.path.insert(0, params.folder)
    try:
        import ft_test_plugin  # pyright: ignore[reportMissingImports]
    finally:
        sys.path.remove(params.folder)
    return ValueResult(value=int(ft_test_plugin.VALUE))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def _interpreter(_: Message, __: JobContext) -> Message:
    flags = sys.flags
    return InterpreterResult(  # pyright: ignore[reportReturnType] (a test op's result is any model)
        hash_abc=hash("abc"),
        hash_seed=os.environ.get("PYTHONHASHSEED"),
        no_site=flags.no_site,
        no_user_site=flags.no_user_site,
        safe_path=flags.safe_path,
        isolated=flags.isolated,
        ignore_environment=flags.ignore_environment,
        dont_write_bytecode=sys.dont_write_bytecode,
        pycache_prefix=sys.pycache_prefix,
    )


PRODUCTION: dict[str, Handler] = {
    "handshake": Handler(NoParams, _handshake),
    "ping": Handler(NoParams, _ping),
    **{op: Handler(OPS[op].params, run, needs_engine_lock=True) for op, run in RECOVERY_HANDLERS.items()},
}
TEST_OPS: dict[str, Handler] = {
    "test.stall": Handler(NoParams, _stall),
    "test.noise": Handler(NoParams, _noise),
    "test.fail": Handler(NoParams, _fail),
    "test.read_stdin": Handler(NoParams, _read_stdin),
    "test.gate": Handler(PathParams, _gate),
    "test.child": Handler(LockParams, _child),
    "test.interpreter": Handler(NoParams, _interpreter),
    "test.import_plugin": Handler(FolderParams, _import_plugin),
}
assert set(PRODUCTION) == set(OPS), "every op of the protocol has a handler"  # noqa: S101 (import-time invariant)
assert all(PRODUCTION[op].needs_engine_lock == OPS[op].needs_engine_lock for op in OPS)  # noqa: S101


def _log(text: str) -> None:
    sys.stderr.write(f"ledger-engine: {text}\n")
    sys.stderr.flush()


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        msg = "duplicate key"
        raise ValueError(msg)
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def _valid_id(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_ID


class Engine:
    """The request handler. Its state: whether the handshake happened, and whether this engine holds the engine
    lock (`took_engine_lock`, called once `hold_engine_lock` returned). An op that reads or writes ledger or journal
    files (`OpSpec.needs_engine_lock`: recover-all's) is refused `engine.no_engine_lock` by an engine that does not
    hold it (S0062; engine-jobs §5.1): so no such work ever runs beside an engine of an earlier session, which may
    have answered its handshake and not yet taken its `flock` when the sidecar waited (BL-0151's gap)."""

    def __init__(self, *, test_ops: bool = False, engine_lock_held: bool = False) -> None:
        self._handlers = {**PRODUCTION, **(TEST_OPS if test_ops else {})}
        self._ready = False
        self._locked = engine_lock_held

    def took_engine_lock(self) -> None:
        self._locked = True

    @property
    def ready(self) -> bool:
        """Whether the handshake happened."""
        return self._ready

    def handle(self, line: bytes, channel: JobChannel | None = None) -> bytes:
        """The reply to one request line. A job's messages go to `channel` before it; without a channel a gate never
        proceeds. Raises `InputEnded` when the input ends at a gate."""
        return self._reply(line, channel or _NoChannel()) + b"\n"

    def _reply(self, line: bytes, channel: JobChannel) -> bytes:
        content = line[:-1] if line.endswith(b"\n") else line
        if len(content) > MAX_LINE_BYTES:
            return _refusal(None, None, "protocol.malformed", f"The line is longer than {MAX_LINE_BYTES} bytes.")
        try:
            obj = json.loads(
                content.decode("utf-8"), object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant
            )
        except UnicodeDecodeError:
            return _refusal(None, None, "protocol.malformed", "The line is not UTF-8.")
        except ValueError:
            return _refusal(None, None, "protocol.malformed", "The line is not one JSON value.")
        except RecursionError:  # S0035 verifier F3: nesting that exhausts the parser is malformed, not a crash
            return _refusal(None, None, "protocol.malformed", "The line nests too deeply.")
        if not isinstance(obj, dict):
            return _refusal(None, None, "protocol.malformed", "A request is a JSON object.")
        request: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType] (json.loads output)
        rid = request.get("id") if _valid_id(request.get("id")) else None
        op = request.get("op") if rid is not None and isinstance(request.get("op"), str) else None
        v = request.get("v")
        if not (type(v) is int and v == PROTOCOL_VERSION):
            return _refusal(
                rid, op, "protocol.version_mismatch", f"This engine speaks protocol {PROTOCOL_VERSION} only."
            )
        if rid is None:
            return _refusal(None, None, "protocol.malformed", f"`id` must be an integer from 0 to {MAX_ID}.")
        if frozenset(request) != ENVELOPE:
            return _refusal(rid, op, "protocol.malformed", "A request has exactly the keys v, id, op and params.")
        if op is None:
            return _refusal(rid, None, "protocol.malformed", "`op` must be a string.")
        if not isinstance(request["params"], dict):
            return _refusal(rid, op, "protocol.malformed", "`params` must be an object.")
        handler = self._handlers.get(op)
        if handler is None:
            return _refusal(rid, op, "protocol.unknown_op", "This engine has no such operation.")
        if not self._ready and op != "handshake":
            return _refusal(rid, op, "protocol.handshake_required", "Send handshake first.")
        try:
            params = handler.params.model_validate(request["params"])
        except ValidationError as e:
            where = "; ".join(f"params.{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            return _refusal(rid, op, "protocol.invalid_params", where or "Invalid params.")
        if handler.needs_engine_lock and not self._locked:
            return _refusal(rid, op, "engine.no_engine_lock", "This engine does not hold the engine lock.")
        ctx = JobContext(rid, op, channel)
        try:
            reply = _accepted(rid, op, handler.run(params, ctx), ctx.decision)
        except InputEnded:
            raise  # not a failure of the request: the read loop ends, and the job never proceeds
        except GateRefused as e:
            return _refusal(rid, op, "protocol.malformed", str(e))
        except BadParamsError as e:
            return _refusal(rid, op, "protocol.invalid_params", str(e))
        except Exception:  # noqa: BLE001 (the engine's request boundary, EP-13: logged, then a typed refusal)
            _log(f"internal error in {op}:\n{traceback.format_exc()}")
            return _refusal(rid, op, "engine.internal", INTERNAL_MESSAGE)
        if op == "handshake":
            self._ready = True
        return reply


INTERNAL_MESSAGE = "The engine failed on this request; details are in its log."


def _accepted(rid: int, op: str, result: BaseModel, decision: Decision | None) -> bytes:
    """The accepted reply, validated against the op's reply model (a wrong result raises here, inside the request
    boundary). The `decision` key is present only when the job made one."""
    model = REPLY_MODELS.get(op)
    if model is None:  # a test op: not in the schema, same envelope
        reply_obj: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "id": rid,
            "op": op,
            "ok": True,
            "result": result.model_dump(mode="json"),
        }
        if decision is not None:
            reply_obj["decision"] = decision.model_dump(mode="json")
        return json.dumps(reply_obj, separators=(",", ":")).encode()
    reply = {"v": PROTOCOL_VERSION, "id": rid, "op": op, "ok": True, "result": result, "decision": decision}
    validated = model.model_validate(reply)
    return validated.model_dump_json(exclude={"decision"} if decision is None else None).encode()


def _echo(op: str | None) -> str | None:
    """The op as it can be echoed exactly, else None: an altered echo would read as a reply to another request."""
    if op is None or len(op) > MAX_ECHO_CHARS:
        return None
    try:
        op.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate, sent as a \ud800 escape
        return None
    return op


def _text(message: str) -> str:
    """A message that can always be encoded: lone surrogates replaced, cut to `MAX_MESSAGE_CHARS`."""
    return message[:MAX_MESSAGE_CHARS].encode("utf-8", "replace").decode("utf-8")


def _refusal(rid: int | None, op: str | None, key: RefusalKey, message: str) -> bytes:
    body = RefusalBody(key=key, message=_text(message))
    refusal = Refusal(v=PROTOCOL_VERSION, id=rid, op=_echo(op), ok=False, error=body)
    return refusal.model_dump_json(exclude={"decision"}).encode()


# The read loop's last resort: fixed bytes, so answering cannot fail too.
LAST_RESORT = _refusal(None, None, "engine.internal", INTERNAL_MESSAGE) + b"\n"


def claim_stdio() -> tuple[IO[bytes], IO[bytes]]:
    """Keep private copies of stdin and stdout for the protocol; point fd 1 at stderr and fd 0 at the null device."""
    channel_in = os.fdopen(os.dup(0), "rb")
    channel_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdout.flush()
    sys.stdout = sys.stderr  # Python-level prints too (fd 1 already points there), unbuffered by line
    return channel_in, channel_out


def _discard_rest_of_line(channel: IO[bytes]) -> None:
    while True:
        chunk = channel.readline(1024 * 1024)
        if not chunk or chunk.endswith(b"\n"):
            return


def hold_engine_lock(path: str) -> None:
    """Take the engine lock, waiting, and keep it for the life of the process (§5.1): the descriptor is never
    closed. Creates the folder if it is missing, as the sidecar's wait does, so the engine never depends on the
    sidecar having waited first."""
    if os.name != "posix":  # pragma: no cover (Windows)
        return  # TODO(BL-0087): the Windows lock (LockFileEx), with the engine's nested Job Object.
    import fcntl  # POSIX only

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)


class _Channel:
    """The protocol channel as a job sees it: a job message is written and flushed at once; a line read at a gate is
    read like a request line (an over-long one cut and the rest discarded)."""

    def __init__(self, channel_in: IO[bytes], channel_out: IO[bytes]) -> None:
        self._in = channel_in
        self._out = channel_out

    def send(self, raw: bytes) -> None:
        self._out.write(raw)
        self._out.flush()

    def read_line(self) -> bytes:
        return _read_line(self._in)


def _read_line(channel_in: IO[bytes]) -> bytes:
    line = channel_in.readline(MAX_LINE_BYTES + 2)  # a full line has at most MAX_LINE_BYTES + 1 bytes
    if len(line) > MAX_LINE_BYTES + 1 and not line.endswith(b"\n"):  # over-long and cut: skip to its end
        _discard_rest_of_line(channel_in)
    return line


def serve(
    channel_in: IO[bytes], channel_out: IO[bytes], engine: Engine, after_handshake: Callable[[], None] | None = None
) -> None:
    """Answer requests until stdin reaches EOF (also at a job's gate) or the reader goes away. `after_handshake` runs
    once, right after the reply that completed the handshake is written and before the next request is read (the
    engine lock)."""
    pending = after_handshake
    channel = _Channel(channel_in, channel_out)
    while True:
        line = _read_line(channel_in)
        if not line:
            return
        try:
            reply = engine.handle(line, channel)
        except InputEnded:
            return  # the input ended at a gate: the job never proceeds
        except OSError:  # a job message could not be written: the reader is gone
            return
        except Exception:  # noqa: BLE001 (the read loop's last resort, EP-13: every line gets one reply)
            _log(f"internal error outside the request boundary:\n{traceback.format_exc()}")
            reply = LAST_RESORT
        try:
            channel_out.write(reply)
            channel_out.flush()
        except OSError:  # the reader is gone (EPIPE): nobody is left to answer
            return
        if pending is not None and engine.ready:
            pending, run_once = None, pending
            run_once()


def leads_own_group() -> bool:
    """POSIX: this process leads its process group (the sidecar spawns it with `start_new_session`)."""
    return os.name == "posix" and os.getpgrp() == os.getpid()


def end_own_group(code: int) -> None:
    """End the engine: its whole process group when it leads one (engine-jobs §5.2: `killpg(0, SIGKILL)`, the engine
    included, so a plugin's child cannot outlive it holding a ledger lock), else only itself. Never returns."""
    sys.stderr.flush()
    if leads_own_group():
        os.killpg(0, signal.SIGKILL)
    os._exit(code)


def watch_parent(parent: int, exit_now: Callable[[int], None] = end_own_group) -> threading.Thread:
    """POSIX: end the process when its parent is not `parent` (the parent died and the engine was re-parented).
    The first check runs at once: a parent that died while the engine was starting is caught too."""

    def run() -> None:
        tick = threading.Event()
        while True:
            if os.getppid() != parent:
                _log("the parent process is gone; exiting")
                exit_now(EXIT_PARENT_GONE)
                return
            tick.wait(PARENT_POLL_SECONDS)

    thread = threading.Thread(target=run, name="parent-watchdog", daemon=True)
    thread.start()
    return thread


def watches_parent(parent: int) -> bool:
    """POSIX only, and not for pid 1: a child of pid 1 cannot tell its parent's death from re-parenting."""
    return os.name == "posix" and parent != INIT_PID


def _pid(text: str) -> int:
    value = int(text)
    if value < INIT_PID:
        msg = f"{text} is not a parent process id"
        raise argparse.ArgumentTypeError(msg)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger_engine", description="Finzytrack ledger engine (ADR-0054)")
    parser.add_argument(
        "--parent-pid",
        type=_pid,
        help="the spawning process; the engine exits when it is no longer its parent (default: the parent at start)",
    )
    parser.add_argument(
        "--engine-lock", help="hold this lock file from the handshake on, for the engine's life (engine-jobs §5.1)"
    )
    parser.add_argument("--test-ops", action="store_true", help="test only: add the test.* ops")
    args = parser.parse_args(argv)
    parent = args.parent_pid if args.parent_pid is not None else os.getppid()
    channel_in, channel_out = claim_stdio()
    if watches_parent(parent):
        watch_parent(parent)
    else:
        _log(f"the parent is pid {parent}; the engine ends on stdin EOF only")
    lock: str | None = args.engine_lock
    engine = Engine(test_ops=args.test_ops)

    def hold() -> None:
        assert lock is not None  # noqa: S101 (only passed with a lock)
        hold_engine_lock(lock)
        engine.took_engine_lock()

    serve(channel_in, channel_out, engine, after_handshake=None if lock is None else hold)
    if leads_own_group():  # the end-of-input exit ends the whole group (engine-jobs §5.2)
        end_own_group(0)
    return 0
