"""The protocol schema is generated from the engine's message models and committed (ADR-0054 decision 2; EP-14,
EP-16; INV-19). `scripts/check_generated.py` diff-checks it from the repo root; this test does the same from the
engine's side and checks that the engine's real replies and the schema's examples agree with the schema.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ledger_engine.protocol.messages import PROTOCOL_VERSION, model_stem
from ledger_engine.protocol.schema import render
from ledger_engine.protocol.server import Engine, serve

REPO = Path(__file__).resolve().parents[2]
COMMITTED = REPO / "contracts" / "engine-protocol.schema.json"


def committed() -> dict[str, Any]:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def validator(definition: str) -> Draft202012Validator:
    schema = committed()
    return Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})


def valid(definition: str, instance: Any) -> bool:
    return bool(validator(definition).is_valid(instance))  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)


def test_the_committed_schema_is_the_generated_one() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == render()


def test_the_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(committed())


def test_the_schema_says_it_is_generated_and_versioned() -> None:
    schema = committed()
    assert next(iter(schema)) == "$comment"
    assert schema["$comment"].startswith("GENERATED")
    assert schema["x-protocol-version"] == PROTOCOL_VERSION


def test_every_op_has_a_request_and_a_reply_definition() -> None:
    schema = committed()
    assert schema["x-ops"] == [
        "handshake",
        "ping",
        "recover_list",
        "recover_try",
        "recover_act",
        "save",
        "save_outcome",
        "r4_check",
        "r4_confirm",
        "r4_attempt",
        "r4_resume",
    ]
    for op in schema["x-ops"]:
        name = model_stem(op)
        assert f"{name}Request" in schema["$defs"]
        assert f"{name}Reply" in schema["$defs"]
    assert "Refusal" in schema["$defs"]


def test_test_ops_are_not_part_of_the_contract() -> None:
    assert "test." not in COMMITTED.read_text(encoding="utf-8")


def engine_replies() -> list[tuple[str, dict[str, Any]]]:
    """Real replies of the engine for each op and for each kind of refusal it can give."""
    engine = Engine(test_ops=True)
    out: list[tuple[str, dict[str, Any]]] = []

    def send(raw: bytes) -> dict[str, Any]:
        return json.loads(engine.handle(raw))

    def req(op: str, params: object = None, rid: int = 1) -> bytes:
        return json.dumps({"v": 2, "id": rid, "op": op, "params": {} if params is None else params}).encode()

    out.append(("Refusal", send(req("ping"))))  # handshake_required
    out.append(("HandshakeReply", send(req("handshake"))))
    out.append(("PingReply", send(req("ping"))))
    out.append(("Refusal", send(b"{")))  # malformed
    out.append(("Refusal", send(json.dumps({"v": 3, "id": 1, "op": "ping", "params": {}}).encode())))
    out.append(("Refusal", send(req("nope"))))
    out.append(("Refusal", send(req("ping", {"x": 1}))))
    out.append(("Refusal", send(req("test.fail"))))
    out.append(("Refusal", send(req("recover_list", {"app_local_data": "/nowhere"}))))  # no engine lock
    return out


@pytest.mark.parametrize(("definition", "reply"), engine_replies())
def test_real_replies_validate_against_the_schema(definition: str, reply: dict[str, Any]) -> None:
    assert valid(definition, reply), reply


def test_every_refusal_key_the_engine_gives_is_in_the_schema() -> None:
    keys = {r["error"]["key"] for d, r in engine_replies() if d == "Refusal"}
    enum = set(committed()["$defs"]["RefusalKey"]["enum"])
    assert keys <= enum
    assert enum == {
        "protocol.malformed",
        "protocol.version_mismatch",
        "protocol.unknown_op",
        "protocol.invalid_params",
        "protocol.handshake_required",
        "engine.internal",
        "engine.no_engine_lock",
    }


DEFINITIONS = [
    "HandshakeReply",
    "PingReply",
    "Refusal",
    "HandshakeRequest",
    "PingRequest",
    *(
        f"{model_stem(op)}{kind}"
        for op in ("recover_list", "recover_try", "recover_act")
        for kind in ("Request", "Reply")
    ),
]


@pytest.mark.parametrize("definition", DEFINITIONS)
def test_examples_validate(definition: str) -> None:
    examples = committed()["$defs"][definition].get("examples", [])
    assert examples, f"{definition} has no example"
    for example in examples:
        assert valid(definition, example), example


@pytest.mark.parametrize("definition", DEFINITIONS)
def test_a_mutated_example_is_refused_by_the_schema(definition: str) -> None:
    """Must not pass: a message with a required field missing, an unknown key, or another version."""
    example = committed()["$defs"][definition]["examples"][0]
    for key in example:
        broken = copy.deepcopy(example)
        del broken[key]
        assert not valid(definition, broken), f"{definition} without {key} passed"
    assert not valid(definition, {**example, "unknown": 1})
    assert not valid(definition, {**example, "v": 3})


# -- Version 2 (S0057; engine-jobs §5.5 bullet 5): a job's messages and the sidecar's proceed ------------------------

JOB_MESSAGES = ["ProgressEvent", "GateEvent", "CommittedEvent", "ProceedControl"]


def test_the_job_messages_and_the_decision_are_in_the_contract() -> None:
    defs = committed()["$defs"]
    for name in [*JOB_MESSAGES, "Decision", "Phase"]:
        assert name in defs, name
    assert set(defs["Phase"]["enum"]) == {
        "reading",
        "checking_change",
        "waiting_for_files",
        "saving",
        "updating_views",
        "checking_last_change",
    }


@pytest.mark.parametrize("definition", JOB_MESSAGES)
def test_job_message_examples_validate_and_a_mutation_is_refused(definition: str) -> None:
    example = committed()["$defs"][definition]["examples"][0]
    assert valid(definition, example)
    for key in example:
        broken = copy.deepcopy(example)
        del broken[key]
        assert not valid(definition, broken), f"{definition} without {key} passed"
    assert not valid(definition, {**example, "unknown": 1})
    assert not valid(definition, {**example, "v": 1})


def test_a_reply_may_carry_a_decision_of_flat_scalars_only() -> None:
    reply = {"v": 2, "id": 1, "op": "ping", "ok": True, "result": {"status": "ok"}}
    decision = {"table": "gate", "version": 1, "inputs": {"n": 2, "equal": True, "which": "post", "none": None}}
    assert valid("PingReply", {**reply, "decision": {**decision, "output": "pass"}})
    assert not valid("PingReply", {**reply, "decision": {**decision, "inputs": {"x": {"y": 1}}, "output": "pass"}})
    assert not valid("PingReply", {**reply, "decision": {**decision, "inputs": {"x": [1]}, "output": "pass"}})
    assert not valid("PingReply", {**reply, "decision": {**decision, "output": "pass", "extra": 1}})
    assert not valid("PingReply", {**reply, "decision": decision})  # no output


def test_the_engines_real_job_messages_validate(tmp_path: Path) -> None:
    out = io.BytesIO()
    lines = [
        json.dumps({"v": 2, "id": 1, "op": "handshake", "params": {}}),
        json.dumps({"v": 2, "id": 2, "op": "test.gate", "params": {"path": str(tmp_path / "x")}}),
        json.dumps({"v": 2, "id": 2, "op": "test.gate", "control": "proceed"}),
    ]
    serve(io.BytesIO("\n".join(lines).encode() + b"\n"), out, Engine(test_ops=True))
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    by_event = {"progress": "ProgressEvent", "gate": "GateEvent", "committed": "CommittedEvent"}
    events = [m for m in msgs if "event" in m]
    assert len(events) == 4
    for m in events:
        assert valid(by_event[m["event"]], m), m
    reply = msgs[-1]
    assert "decision" in reply
    assert valid("Decision", reply["decision"])
    assert valid("ProceedControl", json.loads(lines[2]))
