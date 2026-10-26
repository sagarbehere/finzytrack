"""The main side's codec for the engine protocol (ADR-0054 decision 2; EP-06: one parser per edge).

The protocol's source is the engine's message models; `contracts/engine-protocol.schema.json` is generated from
them. The main sidecar cannot import the engine, so this module decodes replies strictly by hand, and
`tests/platform/test_engine_protocol.py` holds it to the committed schema: same version, ops, refusal keys and line
limit, every schema example decodes, and a mutated reply is refused here exactly when the schema refuses it.

Pure: no I/O. `decode_reply` returns `Accepted`, an `EngineRefusal`, or a `Violation` (the reply is not a valid
reply to this request; the client then stops the engine).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.platform.engine import EngineInfo, EngineRefusal, Pong, RefusalKey

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_ID = 2**31 - 1
OPS = ("handshake", "ping")
# The members of `RefusalKey` (the contract test checks both against the schema).
REFUSAL_KEYS = frozenset(
    {
        "protocol.malformed",
        "protocol.version_mismatch",
        "protocol.unknown_op",
        "protocol.invalid_params",
        "protocol.handshake_required",
        "engine.internal",
    }
)
OK_KEYS = frozenset({"v", "id", "op", "ok", "result"})
REFUSAL_REPLY_KEYS = frozenset({"v", "id", "op", "ok", "error"})


@dataclass(frozen=True, slots=True)
class Accepted:
    """An accepted reply's `result` object, still plain data; the op's decoder types it."""

    result: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Violation:
    """Not a valid reply to the request in flight."""

    detail: str


def encode_request(rid: int, op: str, params: Mapping[str, object]) -> bytes:
    """One request line. `params` must be JSON data (no NaN or infinity)."""
    body = {"v": PROTOCOL_VERSION, "id": rid, "op": op, "params": dict(params)}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        msg = "duplicate key"
        raise ValueError(msg)
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def _is_id(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_ID


def _is_int(value: object) -> bool:
    return type(value) is int


def decode_reply(line: bytes, rid: int, op: str) -> Accepted | EngineRefusal | Violation:
    """Decode one reply line to the request `rid` for `op`."""
    if not line.endswith(b"\n"):
        return Violation("the reply line was cut off (no newline before the end of the stream)")
    if len(line) - 1 > MAX_LINE_BYTES:
        return Violation(f"the reply is longer than {MAX_LINE_BYTES} bytes")
    try:
        data: object = json.loads(
            line.decode("utf-8"), object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant
        )
    except ValueError:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return Violation("the reply is not one JSON value in UTF-8")
    if not isinstance(data, dict):
        return Violation("the reply is not a JSON object")
    reply = cast("dict[str, object]", data)
    ok = reply.get("ok")
    if type(ok) is not bool:
        return Violation("`ok` must be true or false")
    if frozenset(reply) != (OK_KEYS if ok else REFUSAL_REPLY_KEYS):
        return Violation(f"unexpected keys {sorted(reply)}")
    if not (_is_int(reply["v"]) and reply["v"] == PROTOCOL_VERSION):
        return Violation(f"`v` is not {PROTOCOL_VERSION}")
    if ok:
        if not (_is_id(reply["id"]) and reply["id"] == rid) or reply["op"] != op:
            return Violation("the reply answers another request")
        result = reply["result"]
        if not isinstance(result, dict):
            return Violation("`result` must be an object")
        return Accepted(cast("dict[str, object]", result))
    return _refusal(reply, rid, op)


def _refusal(reply: dict[str, object], rid: int, op: str) -> EngineRefusal | Violation:
    rid_ok = reply["id"] is None or (_is_id(reply["id"]) and reply["id"] == rid)
    op_ok = reply["op"] is None or reply["op"] == op
    if not (rid_ok and op_ok):
        return Violation("the refusal answers another request")
    error = reply["error"]
    if not isinstance(error, dict):
        return Violation("`error` must be an object")
    body = cast("dict[str, object]", error)
    key, message = body.get("key"), body.get("message")
    if set(body) != {"key", "message"} or key not in REFUSAL_KEYS or not isinstance(message, str) or not message:
        return Violation("`error` must have a known key and a message")
    return EngineRefusal(cast("RefusalKey", key), message)


def decode_info(result: Mapping[str, object]) -> EngineInfo | Violation:
    """The `handshake` result."""
    protocol, engine, beancount = result.get("protocol"), result.get("engine_version"), result.get("beancount_version")
    if set(result) != {"protocol", "engine_version", "beancount_version"}:
        return Violation(f"unexpected handshake keys {sorted(result)}")
    if not (
        _is_int(protocol) and cast("int", protocol) >= 1 and isinstance(engine, str) and isinstance(beancount, str)
    ):
        return Violation("the handshake result has the wrong types")
    return EngineInfo(protocol=cast("int", protocol), engine_version=engine, beancount_version=beancount)


def decode_pong(result: Mapping[str, object]) -> Pong | Violation:
    """The `ping` result."""
    if dict(result) != {"status": "ok"}:
        return Violation("the ping result is not {status: ok}")
    return Pong()
