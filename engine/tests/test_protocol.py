"""The engine's protocol handler (S0035 brief; ADR-0054 decision 2; EP-06, EP-13).

Each request line gets exactly one reply line. Expected values come from the protocol stated in the S0035 brief:
`{"v", "id", "op", "params"}` in, `{"v", "id", "op", "ok", "result" | "error": {"key", "message"}}` out,
`handshake` first, at most `MAX_LINE_BYTES` per line. Every refusal sits beside the accepted case (EP-29).
"""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import Any

import pytest

from ledger_engine.protocol.messages import MAX_LINE_BYTES, PROTOCOL_VERSION
from ledger_engine.protocol.server import Engine


def line(obj: object) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def request(op: str, params: dict[str, Any] | None = None, *, rid: int = 1) -> bytes:
    return line({"v": 1, "id": rid, "op": op, "params": params or {}})


def answer(engine: Engine, raw: bytes) -> dict[str, Any]:
    out = engine.handle(raw)
    assert out.endswith(b"\n")
    assert out.count(b"\n") == 1, "one reply is one line"
    reply = json.loads(out)
    assert isinstance(reply, dict)
    return reply  # type: ignore[return-value]


def ready() -> Engine:
    engine = Engine()
    assert answer(engine, request("handshake"))["ok"] is True
    return engine


def refusal(reply: dict[str, Any]) -> str:
    assert reply["ok"] is False, reply
    assert set(reply) == {"v", "id", "op", "ok", "error"}
    assert set(reply["error"]) == {"key", "message"}
    assert reply["error"]["message"].strip()
    return reply["error"]["key"]


def test_protocol_version_is_one() -> None:
    assert PROTOCOL_VERSION == 1


def test_handshake_reports_versions() -> None:
    reply = answer(Engine(), request("handshake", rid=7))
    assert reply == {
        "v": 1,
        "id": 7,
        "op": "handshake",
        "ok": True,
        "result": {
            "protocol": 1,
            "engine_version": version("finzytrack-engine"),
            "beancount_version": "3.2.0",  # ADR-0004
        },
    }


def test_ping_after_handshake() -> None:
    assert answer(ready(), request("ping", rid=2)) == {
        "v": 1,
        "id": 2,
        "op": "ping",
        "ok": True,
        "result": {"status": "ok"},
    }


def test_handshake_may_repeat() -> None:
    engine = ready()
    assert answer(engine, request("handshake", rid=3))["ok"] is True


def test_any_op_before_the_handshake_is_refused() -> None:
    engine = Engine()
    reply = answer(engine, request("ping", rid=4))
    assert refusal(reply) == "protocol.handshake_required"
    assert (reply["id"], reply["op"]) == (4, "ping")
    # The refusal does not stick: the handshake then the op succeed.
    assert answer(engine, request("handshake", rid=5))["ok"] is True
    assert answer(engine, request("ping", rid=6))["ok"] is True


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json\n",
        b"\xff\xfe{}\n",  # not UTF-8
        b"[1, 2]\n",  # not an object
        b'"text"\n',
        b"\n",  # empty line
        b'{"v": 1, "v": 1, "id": 1, "op": "ping", "params": {}}\n',  # duplicate key
        b'{"v": 1, "id": 1, "op": "ping", "params": {"x": NaN}}\n',  # not JSON (NaN)
        b'{"v": 1, "id": 1, "op": "ping", "params": {"x": Infinity}}\n',
    ],
)
def test_malformed_lines_are_refused_with_no_id(raw: bytes) -> None:
    reply = answer(ready(), raw)
    assert refusal(reply) == "protocol.malformed"
    assert reply["id"] is None
    assert reply["op"] is None
    assert reply["v"] == 1


@pytest.mark.parametrize("depth", [10_000, 200_000])
def test_deeply_nested_json_is_refused_and_the_engine_keeps_serving(depth: int) -> None:
    """S0035 verifier F3: nesting deep enough to exhaust the parser is malformed, not a crash."""
    engine = ready()
    raw = b'{"v": 1, "id": 4, "op": "ping", "params": {"x": ' + b"[" * depth + b"]" * depth + b"}}\n"
    assert len(raw) < MAX_LINE_BYTES
    assert refusal(answer(engine, raw)) == "protocol.malformed"
    assert answer(engine, request("ping", rid=5))["ok"] is True


def test_an_over_long_line_is_refused() -> None:
    padding = "x" * MAX_LINE_BYTES
    raw = line({"v": 1, "id": 1, "op": "ping", "params": {"pad": padding}})
    assert len(raw) > MAX_LINE_BYTES
    assert refusal(answer(ready(), raw)) == "protocol.malformed"


def test_the_line_limit_is_16_mib() -> None:
    assert MAX_LINE_BYTES == 16 * 1024 * 1024


@pytest.mark.parametrize("v", [0, 2, "1", 1.0, None, True])
def test_another_protocol_version_is_refused(v: object) -> None:
    reply = answer(ready(), line({"v": v, "id": 9, "op": "ping", "params": {}}))
    assert refusal(reply) == "protocol.version_mismatch"
    assert reply["id"] == 9  # a valid id is echoed so the caller can match the refusal


def test_a_missing_version_is_a_version_mismatch() -> None:
    assert refusal(answer(ready(), line({"id": 1, "op": "ping", "params": {}}))) == "protocol.version_mismatch"


def test_a_handshake_with_another_version_is_refused() -> None:
    reply = answer(Engine(), line({"v": 2, "id": 1, "op": "handshake", "params": {}}))
    assert refusal(reply) == "protocol.version_mismatch"


@pytest.mark.parametrize(
    "envelope",
    [
        {"v": 1, "op": "ping", "params": {}},  # no id
        {"v": 1, "id": -1, "op": "ping", "params": {}},
        {"v": 1, "id": 2**31, "op": "ping", "params": {}},
        {"v": 1, "id": "1", "op": "ping", "params": {}},
        {"v": 1, "id": 1.5, "op": "ping", "params": {}},
        {"v": 1, "id": True, "op": "ping", "params": {}},
    ],
)
def test_a_bad_id_is_malformed(envelope: dict[str, Any]) -> None:
    reply = answer(ready(), line(envelope))
    assert refusal(reply) == "protocol.malformed"
    assert reply["id"] is None


@pytest.mark.parametrize(
    "envelope",
    [
        {"v": 1, "id": 3, "params": {}},  # no op
        {"v": 1, "id": 3, "op": "ping"},  # no params
        {"v": 1, "id": 3, "op": 5, "params": {}},
        {"v": 1, "id": 3, "op": "ping", "params": []},
        {"v": 1, "id": 3, "op": "ping", "params": {}, "extra": 1},
    ],
)
def test_a_bad_envelope_is_malformed_and_echoes_the_id(envelope: dict[str, Any]) -> None:
    reply = answer(ready(), line(envelope))
    assert refusal(reply) == "protocol.malformed"
    assert reply["id"] == 3


def test_the_largest_id_is_accepted() -> None:
    assert answer(ready(), request("ping", rid=2**31 - 1))["id"] == 2**31 - 1


@pytest.mark.parametrize("op", ["load", "PING", "ping ", "test.stall", "test.fail", "__init__"])
def test_an_unknown_op_is_refused(op: str) -> None:
    reply = answer(ready(), request(op, rid=11))
    assert refusal(reply) == "protocol.unknown_op"
    assert (reply["id"], reply["op"]) == (11, op)


@pytest.mark.parametrize(
    ("op", "params"),
    [("ping", {"extra": 1}), ("handshake", {"protocol": 1}), ("ping", {"status": "ok"})],
)
def test_bad_params_are_refused(op: str, params: dict[str, Any]) -> None:
    reply = answer(ready(), request(op, params, rid=12))
    assert refusal(reply) == "protocol.invalid_params"
    assert (reply["id"], reply["op"]) == (12, op)


def test_test_ops_exist_only_when_enabled() -> None:
    engine = Engine(test_ops=True)
    answer(engine, request("handshake"))
    reply = answer(engine, request("test.fail", rid=13))
    # A handler that raises is an internal refusal; the engine keeps serving.
    assert refusal(reply) == "engine.internal"
    assert (reply["id"], reply["op"]) == (13, "test.fail")
    assert "boom" not in reply["error"]["message"], "internal details stay on stderr"
    assert answer(engine, request("ping", rid=14))["ok"] is True


def test_a_reply_is_compact_json_on_one_line() -> None:
    out = ready().handle(request("ping"))
    assert out == b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"status":"ok"}}\n'
