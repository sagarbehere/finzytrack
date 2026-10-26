"""The main side of the engine protocol against the committed schema (ADR-0054 decision 2; EP-06, EP-14, EP-16).

`contracts/engine-protocol.schema.json` is generated from the engine's models. The main side decodes replies with
its own strict decoder (it cannot import the engine). These tests hold the decoder to the schema: the version,
ops, refusal keys and line limit agree; every request the main side encodes is valid; every example in the schema
decodes; and a mutated example is refused by the decoder exactly when the schema refuses it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, get_args

import pytest
from jsonschema import Draft202012Validator

from app.platform.engine import EngineInfo, EngineRefusal, Pong, RefusalKey
from app.platform.engine_protocol import (
    MAX_ID,
    MAX_LINE_BYTES,
    OPS,
    PROTOCOL_VERSION,
    REFUSAL_KEYS,
    Accepted,
    Violation,
    decode_info,
    decode_pong,
    decode_reply,
    encode_request,
)

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "engine-protocol.schema.json"


def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validator(definition: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": schema()["$defs"]})


def valid(definition: str, instance: Any) -> bool:
    return bool(validator(definition).is_valid(instance))  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)


def examples(definition: str) -> list[dict[str, Any]]:
    found = schema()["$defs"][definition].get("examples", [])
    assert found, f"{definition} has no examples"
    return found


def test_version_ops_keys_and_limit_agree_with_the_schema() -> None:
    s = schema()
    assert s["x-protocol-version"] == PROTOCOL_VERSION == 1
    assert s["x-max-line-bytes"] == MAX_LINE_BYTES
    assert tuple(s["x-ops"]) == OPS
    assert s["$defs"]["PingRequest"]["properties"]["id"]["maximum"] == MAX_ID
    assert set(s["$defs"]["RefusalKey"]["enum"]) == set(get_args(RefusalKey.__value__)) == REFUSAL_KEYS


@pytest.mark.parametrize("op", OPS)
def test_every_encoded_request_is_valid(op: str) -> None:
    raw = encode_request(42, op, {})
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert valid(f"{op.capitalize()}Request", json.loads(raw))


def test_an_encoded_request_is_compact() -> None:
    assert encode_request(3, "ping", {}) == b'{"v":1,"id":3,"op":"ping","params":{}}\n'


def decoded(reply: dict[str, Any], op: str) -> object:
    """The decoder's verdict on a reply for `op`: a typed value, an EngineRefusal, or a Violation."""
    rid = reply.get("id") if isinstance(reply.get("id"), int) else 1
    out = decode_reply(json.dumps(reply).encode() + b"\n", rid, op)  # type: ignore[arg-type]
    if not isinstance(out, Accepted):
        return out
    return decode_info(out.result) if op == "handshake" else decode_pong(out.result)


def test_handshake_examples_decode() -> None:
    for ex in examples("HandshakeReply"):
        info = decoded(ex, "handshake")
        assert isinstance(info, EngineInfo), info
        assert info.protocol == ex["result"]["protocol"]
        assert info.beancount_version == ex["result"]["beancount_version"]


def test_ping_examples_decode() -> None:
    for ex in examples("PingReply"):
        assert decoded(ex, "ping") == Pong()


def test_refusal_examples_decode() -> None:
    for ex in examples("Refusal"):
        out = decoded(ex, ex["op"] or "ping")
        assert out == EngineRefusal(ex["error"]["key"], ex["error"]["message"])


def mutations(example: dict[str, Any]) -> list[dict[str, Any]]:
    """Variants of a valid reply: each key dropped, an unknown key, a wrong version, wrong types, a flipped `ok`."""
    out: list[dict[str, Any]] = []
    for key in example:
        broken = copy.deepcopy(example)
        del broken[key]
        out.append(broken)
    out += [
        {**example, "unknown": 1},
        {**example, "v": 2},
        {**example, "v": "1"},
        {**example, "ok": not example["ok"]},
        {**example, "ok": 1},
        {**example, "id": "1"},
        {**example, "id": -1},
        {**example, "id": 1.5},
        {**example, "id": True},  # a JSON boolean is not an integer (S0035 mutant B5)
        {**example, "v": True},
    ]
    if "result" in example:
        for key in example["result"]:
            broken = copy.deepcopy(example)
            del broken["result"][key]
            out.append(broken)
            wrong = copy.deepcopy(example)
            wrong["result"][key] = [] if not isinstance(example["result"][key], list) else "x"
            out.append(wrong)
        out.append({**example, "result": {**example["result"], "unknown": 1}})
        out.append({**example, "result": []})
        if "protocol" in example["result"]:  # S0035 mutant B9: protocol versions start at 1
            out += [{**example, "result": {**example["result"], "protocol": p}} for p in (0, -1, True)]
    if "error" in example:
        out += [
            {**example, "error": {**example["error"], "key": "protocol.other"}},
            {**example, "error": {"key": example["error"]["key"]}},
            {**example, "error": {**example["error"], "message": 5}},
            {**example, "error": {**example["error"], "extra": 1}},
        ]
    return out


@pytest.mark.parametrize(
    ("definition", "op"), [("HandshakeReply", "handshake"), ("PingReply", "ping"), ("Refusal", None)]
)
def test_the_decoder_refuses_exactly_what_the_schema_refuses(definition: str, op: str | None) -> None:
    for ex in examples(definition):
        reply_op = op or ex["op"] or "ping"
        for variant in mutations(ex):
            schema_ok = valid(definition, variant)
            verdict = decoded(variant, reply_op)
            assert schema_ok == (not isinstance(verdict, Violation)), (variant, verdict)


def test_a_reply_to_another_request_is_a_violation() -> None:
    ex = examples("PingReply")[0]
    raw = json.dumps({**ex, "id": 7}).encode() + b"\n"
    assert isinstance(decode_reply(raw, 8, "ping"), Violation)
    assert isinstance(decode_reply(json.dumps({**ex, "op": "handshake"}).encode() + b"\n", ex["id"], "ping"), Violation)


def test_a_refusal_of_an_unreadable_request_has_no_id_and_still_decodes() -> None:
    raw = b'{"v":1,"id":null,"op":null,"ok":false,"error":{"key":"protocol.malformed","message":"not JSON"}}\n'
    assert decode_reply(raw, 4, "ping") == EngineRefusal("protocol.malformed", "not JSON")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\n",
        b"not json\n",
        b"\xff\n",
        b"[]\n",
        b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"status":"ok"}}',  # no newline: the line was cut
        b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"status":"ok"},"ok":true}\n',  # duplicate key
        b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"status":NaN}}\n',
    ],
)
def test_unreadable_replies_are_violations(raw: bytes) -> None:
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)


def test_a_deeply_nested_reply_is_a_violation_not_a_crash() -> None:
    """S0035 verifier F4(a): nesting that exhausts the parser is an invalid reply."""
    depth = 200_000
    raw = b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"x":' + b"[" * depth + b"]" * depth + b"}}\n"
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)


def test_an_over_long_reply_is_a_violation() -> None:
    raw = b'{"v":1,"id":1,"op":"ping","ok":true,"result":{"status":"' + b"o" * MAX_LINE_BYTES + b'"}}\n'
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)
