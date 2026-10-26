"""The engine's protocol server: one request line in, one reply line out (ADR-0054 decisions 2 and 4).

    python -I -m ledger_engine [--parent-pid PID] [--test-ops]

- **Channel.** At start the engine keeps private copies of its stdin and stdout for the protocol, points fd 1 at
  stderr and fd 0 at the null device. A `print` or a raw write to fd 1 from code running inside the engine (a
  user's Beancount plugin, later) lands on stderr and can never corrupt a reply; nothing inside can read requests.
  The private copies are not inherited by child processes.
- **Order.** Requests are handled one at a time, in order; each gets exactly one reply.
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
  stdin EOF applies (S0035 independent verifier F6).
  On Windows the main sidecar's Job Object (ARCHITECTURE.md §5) ends the engine with its parent.
- **No resident ledger (INV-10).** The engine holds no ledger between requests; today it has no ledger operations
  at all. The overlay load and the heavy-load semaphore arrive with their slices (WS-WRITE, BL-0003).
- `--test-ops` adds ops for the process tests (`test.stall`, `test.noise`, `test.fail`, `test.read_stdin`). They
  are not part of the protocol schema and the main sidecar never passes the flag outside tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import IO, Any

from pydantic import ValidationError

from ledger_engine.protocol.messages import (
    MAX_ECHO_CHARS,
    MAX_ID,
    MAX_LINE_BYTES,
    OPS,
    PROTOCOL_VERSION,
    REPLY_MODELS,
    HandshakeResult,
    Message,
    NoParams,
    PingResult,
    Refusal,
    RefusalBody,
    RefusalKey,
)

ENVELOPE = frozenset({"v", "id", "op", "params"})
PARENT_POLL_SECONDS = 0.25
EXIT_PARENT_GONE = 3
INIT_PID = 1
MAX_MESSAGE_CHARS = 1000


@dataclass(frozen=True, slots=True)
class Handler:
    params: type[Message]
    run: Callable[[Message], Message]


def _handshake(_: Message) -> Message:
    return HandshakeResult(
        protocol=PROTOCOL_VERSION,
        engine_version=version("finzytrack-engine"),
        beancount_version=version("beancount"),  # package metadata only; Beancount is not imported here (INV-02)
    )


def _ping(_: Message) -> Message:
    return PingResult(status="ok")


def _stall(_: Message) -> Message:
    _log("test.stall: stalling until killed")
    threading.Event().wait()
    raise AssertionError("unreachable")


def _noise(_: Message) -> Message:
    print("noise via print")  # noqa: T201 (the point of this test op)
    sys.stdout.flush()
    os.write(1, b"noise via fd 1\n")
    return PingResult(status="ok")


def _fail(_: Message) -> Message:
    msg = "boom (test.fail)"
    raise RuntimeError(msg)


def _read_stdin(_: Message) -> Message:
    """Code inside the engine reading fd 0 gets end of file, never the next request."""
    if os.read(0, 65536):
        msg = "fd 0 still carries the request stream"
        raise RuntimeError(msg)
    return PingResult(status="ok")


PRODUCTION: dict[str, Handler] = {"handshake": Handler(NoParams, _handshake), "ping": Handler(NoParams, _ping)}
TEST_OPS: dict[str, Handler] = {
    "test.stall": Handler(NoParams, _stall),
    "test.noise": Handler(NoParams, _noise),
    "test.fail": Handler(NoParams, _fail),
    "test.read_stdin": Handler(NoParams, _read_stdin),
}
assert set(PRODUCTION) == set(OPS), "every op of the protocol has a handler"  # noqa: S101 (import-time invariant)


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
    """The request handler. Its only state is whether the handshake happened."""

    def __init__(self, *, test_ops: bool = False) -> None:
        self._handlers = {**PRODUCTION, **(TEST_OPS if test_ops else {})}
        self._ready = False

    def handle(self, line: bytes) -> bytes:
        return self._reply(line) + b"\n"

    def _reply(self, line: bytes) -> bytes:
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
        try:
            reply = _accepted(rid, op, handler.run(params))
        except Exception:  # noqa: BLE001 (the engine's request boundary, EP-13: logged, then a typed refusal)
            _log(f"internal error in {op}:\n{traceback.format_exc()}")
            return _refusal(rid, op, "engine.internal", INTERNAL_MESSAGE)
        if op == "handshake":
            self._ready = True
        return reply


INTERNAL_MESSAGE = "The engine failed on this request; details are in its log."


def _accepted(rid: int, op: str, result: Message) -> bytes:
    """The accepted reply, validated against the op's reply model (a wrong result raises here, inside the request
    boundary)."""
    model = REPLY_MODELS.get(op)
    if model is None:  # a test op: not in the schema, same envelope
        return json.dumps(
            {"v": PROTOCOL_VERSION, "id": rid, "op": op, "ok": True, "result": result.model_dump(mode="json")},
            separators=(",", ":"),
        ).encode()
    reply = {"v": PROTOCOL_VERSION, "id": rid, "op": op, "ok": True, "result": result}
    return model.model_validate(reply).model_dump_json().encode()


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
    return Refusal(v=PROTOCOL_VERSION, id=rid, op=_echo(op), ok=False, error=body).model_dump_json().encode()


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


def serve(channel_in: IO[bytes], channel_out: IO[bytes], engine: Engine) -> None:
    """Answer requests until stdin reaches EOF or the reader goes away."""
    while True:
        line = channel_in.readline(MAX_LINE_BYTES + 2)  # a full line has at most MAX_LINE_BYTES + 1 bytes
        if not line:
            return
        if len(line) > MAX_LINE_BYTES + 1 and not line.endswith(b"\n"):  # over-long and cut: skip to its end
            _discard_rest_of_line(channel_in)
        try:
            reply = engine.handle(line)
        except Exception:  # noqa: BLE001 (the read loop's last resort, EP-13: every line gets one reply)
            _log(f"internal error outside the request boundary:\n{traceback.format_exc()}")
            reply = LAST_RESORT
        try:
            channel_out.write(reply)
            channel_out.flush()
        except OSError:  # the reader is gone (EPIPE): nobody is left to answer
            return


def watch_parent(parent: int, exit_now: Callable[[int], None] = os._exit) -> threading.Thread:
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
    parser.add_argument("--test-ops", action="store_true", help="test only: add the test.* ops")
    args = parser.parse_args(argv)
    parent = args.parent_pid if args.parent_pid is not None else os.getppid()
    channel_in, channel_out = claim_stdio()
    if watches_parent(parent):
        watch_parent(parent)
    else:
        _log(f"the parent is pid {parent}; the engine ends on stdin EOF only")
    serve(channel_in, channel_out, Engine(test_ops=args.test_ops))
    return 0
