"""The main side of the engine protocol against the committed schema (ADR-0054 decision 2; EP-06, EP-14, EP-16).

`contracts/engine-protocol.schema.json` is generated from the engine's models. The main side decodes replies with
its own strict decoder (it cannot import the engine). These tests hold the decoder to the schema: the version,
ops, refusal keys and line limit agree; every request the main side encodes is valid; every example in the schema
decodes; and a mutated example is refused by the decoder exactly when the schema refuses it.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from jsonschema import Draft202012Validator

from app.platform.engine import (
    PHASES,
    Committed,
    Decision,
    EngineInfo,
    EngineRefusal,
    GateReached,
    JournalListing,
    Pong,
    Progress,
    RefusalKey,
    TryOutcome,
)
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
    decode_r4,
    decode_recover_act,
    decode_recover_list,
    decode_recover_try,
    decode_reply,
    decode_save,
    decode_save_outcome,
    encode_proceed,
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
    assert s["x-protocol-version"] == PROTOCOL_VERSION == 2
    assert s["x-max-line-bytes"] == MAX_LINE_BYTES
    assert tuple(s["x-ops"]) == OPS
    assert s["$defs"]["PingRequest"]["properties"]["id"]["maximum"] == MAX_ID
    assert set(s["$defs"]["RefusalKey"]["enum"]) == set(get_args(RefusalKey.__value__)) == REFUSAL_KEYS


def stem(op: str) -> str:
    """The schema's name for an op's messages (the engine's `messages.model_stem`)."""
    return "".join(part.capitalize() for part in op.split("_"))


@pytest.mark.parametrize("op", OPS)
def test_every_encoded_request_is_valid(op: str) -> None:
    params = examples(f"{stem(op)}Request")[0]["params"]
    raw = encode_request(42, op, params)
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert valid(f"{stem(op)}Request", json.loads(raw))


def test_an_encoded_request_is_compact() -> None:
    assert encode_request(3, "ping", {}) == b'{"v":2,"id":3,"op":"ping","params":{}}\n'


def decoded(reply: dict[str, Any], op: str) -> object:
    """The decoder's verdict on a reply for `op`: a typed value, an EngineRefusal, or a Violation."""
    rid = reply.get("id") if isinstance(reply.get("id"), int) else 1
    out = decode_reply(json.dumps(reply).encode() + b"\n", rid, op)  # type: ignore[arg-type]
    if not isinstance(out, Accepted):
        return out
    return DECODERS[op](out.result)


DECODERS: dict[str, Any] = {
    "handshake": decode_info,
    "ping": decode_pong,
    "recover_list": decode_recover_list,
    "recover_try": decode_recover_try,
    "recover_act": decode_recover_act,
    "save": decode_save,
    "save_outcome": decode_save_outcome,
    **{op: functools.partial(decode_r4, op) for op in ("r4_check", "r4_confirm", "r4_attempt", "r4_resume")},
}


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
        {**example, "v": 3},
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
    ("definition", "op"),
    [
        ("HandshakeReply", "handshake"),
        ("PingReply", "ping"),
        ("Refusal", None),
        ("RecoverListReply", "recover_list"),
        ("RecoverTryReply", "recover_try"),
        ("RecoverActReply", "recover_act"),
        ("SaveReply", "save"),
        ("SaveOutcomeReply", "save_outcome"),
        ("R4CheckReply", "r4_check"),
        ("R4ConfirmReply", "r4_confirm"),
        ("R4AttemptReply", "r4_attempt"),
        ("R4ResumeReply", "r4_resume"),
    ],
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
    raw = b'{"v":2,"id":null,"op":null,"ok":false,"error":{"key":"protocol.malformed","message":"not JSON"}}\n'
    assert decode_reply(raw, 4, "ping") == EngineRefusal("protocol.malformed", "not JSON")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\n",
        b"not json\n",
        b"\xff\n",
        b"[]\n",
        b'{"v":2,"id":1,"op":"ping","ok":true,"result":{"status":"ok"}}',  # no newline: the line was cut
        b'{"v":2,"id":1,"op":"ping","ok":true,"result":{"status":"ok"},"ok":true}\n',  # duplicate key
        b'{"v":2,"id":1,"op":"ping","ok":true,"result":{"status":NaN}}\n',
    ],
)
def test_unreadable_replies_are_violations(raw: bytes) -> None:
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)


def test_a_deeply_nested_reply_is_a_violation_not_a_crash() -> None:
    """S0035 verifier F4(a): nesting that exhausts the parser is an invalid reply."""
    depth = 200_000
    raw = b'{"v":2,"id":1,"op":"ping","ok":true,"result":{"x":' + b"[" * depth + b"]" * depth + b"}}\n"
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)


def test_an_over_long_reply_is_a_violation() -> None:
    raw = b'{"v":2,"id":1,"op":"ping","ok":true,"result":{"status":"' + b"o" * MAX_LINE_BYTES + b'"}}\n'
    assert isinstance(decode_reply(raw, 1, "ping"), Violation)


# -- Version 2 (S0057; engine-jobs §5.5 bullet 5): a job's messages, the sidecar's proceed, the decision ------------

JOB_MESSAGES = {"ProgressEvent": "progress", "GateEvent": "gate", "CommittedEvent": "committed"}


def test_the_phases_agree_with_the_schema() -> None:
    assert set(schema()["$defs"]["Phase"]["enum"]) == set(PHASES)


def test_an_encoded_proceed_is_valid_and_compact() -> None:
    raw = encode_proceed(7, "save")
    assert raw == b'{"v":2,"id":7,"op":"save","control":"proceed"}\n'
    assert valid("ProceedControl", json.loads(raw))


def decoded_line(msg: dict[str, Any], rid: int, op: str) -> object:
    return decode_reply(json.dumps(msg).encode() + b"\n", rid, op)


@pytest.mark.parametrize("definition", list(JOB_MESSAGES))
def test_job_message_examples_decode(definition: str) -> None:
    for ex in examples(definition):
        out = decoded_line(ex, ex["id"], ex["op"])
        expected = {"progress": Progress(ex.get("phase", "")), "gate": GateReached(), "committed": Committed()}
        assert out == expected[JOB_MESSAGES[definition]], out


def job_mutations(example: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in example:
        broken = copy.deepcopy(example)
        del broken[key]
        out.append(broken)
    out += [
        {**example, "unknown": 1},
        {**example, "v": 1},
        {**example, "event": "other"},
        {**example, "event": 1},
        {**example, "ok": True},
        {**example, "id": True},
    ]
    if "phase" in example:
        out += [{**example, "phase": "thinking"}, {**example, "phase": 1}]
    else:
        out.append({**example, "phase": "reading"})
    return out


@pytest.mark.parametrize("definition", list(JOB_MESSAGES))
def test_the_decoder_refuses_exactly_what_the_schema_refuses_for_job_messages(definition: str) -> None:
    for ex in examples(definition):
        for variant in job_mutations(ex):
            schema_ok = valid(definition, variant)
            verdict = decoded_line(variant, ex["id"], ex["op"])
            assert schema_ok == (not isinstance(verdict, Violation)), (variant, verdict)


def test_a_job_message_for_another_request_is_a_violation() -> None:
    ex = examples("GateEvent")[0]
    assert isinstance(decoded_line(ex, ex["id"] + 1, ex["op"]), Violation)
    assert isinstance(decoded_line(ex, ex["id"], "other"), Violation)


INPUTS: dict[str, Any] = {"n": 2, "equal": True, "which": "post", "none": None}
DECISION: dict[str, Any] = {"table": "gate", "version": 1, "inputs": INPUTS}


def test_a_decision_on_a_reply_is_decoded() -> None:
    reply: dict[str, Any] = {**examples("PingReply")[0], "decision": {**DECISION, "output": "pass"}}
    out = decoded_line(reply, reply["id"], "ping")
    assert isinstance(out, Accepted)
    assert out.decision == Decision("gate", 1, {"n": 2, "equal": True, "which": "post", "none": None}, "pass")
    refused: dict[str, Any] = {**examples("Refusal")[0], "decision": {**DECISION, "output": "refused"}}
    verdict = decoded_line(refused, refused["id"], refused["op"])
    assert isinstance(verdict, EngineRefusal)
    assert verdict.decision == Decision("gate", 1, INPUTS, "refused")


@pytest.mark.parametrize(
    "decision",
    [
        {**DECISION, "output": "pass", "extra": 1},
        {**DECISION, "inputs": {"x": {"y": 1}}, "output": "pass"},
        {**DECISION, "inputs": {"x": [1]}, "output": "pass"},
        {**DECISION, "inputs": {"x": 1.5}, "output": "pass"},
        {**DECISION, "inputs": [], "output": "pass"},
        {**DECISION, "version": 0, "output": "pass"},
        {**DECISION, "version": True, "output": "pass"},
        {**DECISION, "table": "", "output": "pass"},
        {**DECISION, "output": ""},
        {**DECISION, "output": 1},
        DECISION,
    ],
)
def test_the_decoder_refuses_exactly_what_the_schema_refuses_for_a_decision(decision: dict[str, Any]) -> None:
    reply: dict[str, Any] = {**examples("PingReply")[0], "decision": decision}
    schema_ok = valid("PingReply", reply)
    verdict = decoded_line(reply, reply["id"], "ping")
    assert schema_ok == (not isinstance(verdict, Violation)), (decision, verdict)
    assert not schema_ok


# -- Recover-all's ops (S0062) --------------------------------------------------------------------------------------

RECOVER_OPS = (  # and the save's (S0063) and R4's (S0066)
    "recover_list",
    "recover_try",
    "recover_act",
    "save",
    "save_outcome",
    "r4_check",
    "r4_confirm",
    "r4_attempt",
    "r4_resume",
)


def nested_mutations(value: object) -> list[object]:
    """Every variant of `value` with one change at one place inside it: a key dropped, an unknown key, a value made
    a list, a string, null, a negative number, a relative path, or an empty string; a list emptied or given a bad
    item. The decoder must refuse exactly the variants the schema refuses."""
    out: list[object] = []
    replacements: tuple[object, ...] = ([], "x", None, -1, "relative/path", "", True, {"k": 1}, "/abs")
    if isinstance(value, dict):
        obj = cast("dict[str, object]", value)
        for key in obj:
            out.append({k: v for k, v in obj.items() if k != key})
            out += [{**obj, key: repl} for repl in replacements]
            out += [{**obj, key: m} for m in nested_mutations(obj[key])]
        out.append({**obj, "unknown": 1})
    elif isinstance(value, list):
        items = cast("list[object]", value)
        out.append([])
        out.append([*items, "x"])
        for i, item in enumerate(items):
            out += [[*items[:i], m, *items[i + 1 :]] for m in nested_mutations(item)]
    return out


@pytest.mark.parametrize("op", RECOVER_OPS)
def test_recover_examples_decode(op: str) -> None:
    for ex in examples(f"{stem(op)}Reply"):
        out = decoded(ex, op)
        assert not isinstance(out, Violation), out


@pytest.mark.parametrize("op", RECOVER_OPS)
def test_the_decoder_refuses_exactly_what_the_schema_refuses_inside_a_recover_result(op: str) -> None:
    definition = f"{stem(op)}Reply"
    for ex in examples(definition):
        variants = nested_mutations(ex["result"])
        assert len(variants) > 20
        for result in variants:
            variant = {**ex, "result": result}
            schema_ok = valid(definition, variant)
            verdict = decoded(variant, op)
            assert schema_ok == (not isinstance(verdict, Violation)), (result, verdict)


@pytest.mark.parametrize(
    ("op", "foreign"),
    [("r4_attempt", "pass"), ("r4_check", "mismatch"), ("r4_confirm", "pass"), ("r4_resume", "counted")],
)
def test_an_r4_outcome_of_another_op_breaks_the_protocol(op: str, foreign: str) -> None:
    """S0066: each R4 op answers only its own outcomes (a `pass` from `r4_attempt` would let a save go on)."""
    definition = f"{stem(op)}Reply"
    ex = examples(definition)[0]
    variant = {**ex, "result": {**ex["result"], "outcome": foreign}}
    assert not valid(definition, variant)
    assert isinstance(decoded(variant, op), Violation)


def test_the_try_result_decodes_to_its_fields() -> None:
    ex = examples("RecoverTryReply")[0]
    out = decoded(ex, "recover_try")
    assert isinstance(out, TryOutcome)
    assert (out.outcome, out.cause, out.path) == ("stopped", "kept_copy", ex["result"]["path"])
    assert out.inspection is not None
    [main] = out.inspection
    assert main.kept[0].name == "main.bean.kept-s1-j7"
    assert main.kept[0].seen.sha256 == "0" * 64


def test_the_listing_decodes_to_its_fields() -> None:
    out = decoded(examples("RecoverListReply")[0], "recover_list")
    assert isinstance(out, JournalListing)
    [j] = out.journals
    assert (j.name, j.readable, j.workspace_key, j.lineage) == ("s1-j7", True, "k1", "lin1")
    assert j.files[0].created is False


def test_no_engine_lock_is_a_refusal_key() -> None:
    assert "engine.no_engine_lock" in REFUSAL_KEYS
