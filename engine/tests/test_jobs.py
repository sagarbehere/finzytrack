"""A job's messages inside one request: progress, the commit-point gate, `committed` and the decision (S0057;
`design/specs/engine-jobs.md` §4 "the commit point is decided in one table step", §5.4, §5.5 bullet 5, §6.1 steps 7
and 9, §12; INV-01).

Written from the spec: "The engine sends `gate` and waits. In one step the scheduler either ends the engine (a Cancel
is pending: outcome `cancelled`, disk unchanged) or marks the job past the commit point and sends `proceed`." So the
engine never passes its gate on anything but a `proceed` for that request: end of input (the sidecar is gone, or
closed our input) never proceeds, and any other line refuses the request with nothing done past the gate. `committed`
says the save landed (§6.1 step 9), so it can only follow `proceed`. Each engine decision rides on its reply as
`decision {table, version, inputs, output}` (§12). Every refusal sits beside the accepted case (EP-29).

`test.gate` (a test op) writes the file named by `path` only after `proceed`: the stand-in for a save's first durable
write.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ledger_engine.protocol.messages import PROTOCOL_VERSION
from ledger_engine.protocol.server import Engine, JobContext, serve

V = PROTOCOL_VERSION


def line(obj: object) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def request(op: str, rid: int, params: dict[str, Any] | None = None) -> bytes:
    return line({"v": V, "id": rid, "op": op, "params": params or {}})


def proceed(rid: int, op: str) -> bytes:
    return line({"v": V, "id": rid, "op": op, "control": "proceed"})


def run(*lines: bytes) -> list[dict[str, Any]]:
    """Feed these lines to a test engine's read loop until its input ends; every line it wrote, decoded."""
    out = io.BytesIO()
    serve(io.BytesIO(b"".join(lines)), out, Engine(test_ops=True))
    raw = out.getvalue()
    assert raw == b"" or raw.endswith(b"\n")
    return [json.loads(x) for x in raw.splitlines()]


def gate_op(path: Path) -> bytes:
    return request("test.gate", 2, {"path": str(path)})


HANDSHAKE = request("handshake", 1)


def test_progress_gate_proceed_committed_and_the_reply_in_order(tmp_path: Path) -> None:
    target = tmp_path / "written"
    msgs = run(HANDSHAKE, gate_op(target), proceed(2, "test.gate"))
    assert msgs[0]["id"] == 1
    assert msgs[1:] == [
        {"v": V, "id": 2, "op": "test.gate", "event": "progress", "phase": "checking_change"},
        {"v": V, "id": 2, "op": "test.gate", "event": "gate"},
        {"v": V, "id": 2, "op": "test.gate", "event": "committed"},
        {"v": V, "id": 2, "op": "test.gate", "event": "progress", "phase": "updating_views"},
        {
            "v": V,
            "id": 2,
            "op": "test.gate",
            "ok": True,
            "result": {"status": "ok"},
            "decision": {"table": "test.gate", "version": 1, "inputs": {"wrote": True}, "output": "committed"},
        },
    ]
    assert target.read_text(encoding="utf-8") == "post"


def test_end_of_input_at_the_gate_never_proceeds_and_ends_the_read_loop(tmp_path: Path) -> None:
    target = tmp_path / "written"
    msgs = run(HANDSHAKE, gate_op(target))
    assert [m.get("event") for m in msgs[1:]] == ["progress", "gate"]  # no committed, no reply
    assert not target.exists(), "the engine passed its gate without proceed"


@pytest.mark.parametrize(
    "answer",
    [
        request("ping", 3),  # a new request instead of proceed
        proceed(9, "test.gate"),  # proceed for another request
        proceed(2, "ping"),  # proceed for another op
        line({"v": V, "id": 2, "op": "test.gate", "control": "go"}),
        line({"v": V, "id": 2, "op": "test.gate", "control": "proceed", "extra": 1}),
        line({"v": 1, "id": 2, "op": "test.gate", "control": "proceed"}),
        line({"v": V, "id": 2, "op": "test.gate", "control": True}),
        b"not json\n",
        b"[]\n",
    ],
)
def test_any_other_line_at_the_gate_refuses_the_request_and_never_proceeds(tmp_path: Path, answer: bytes) -> None:
    target = tmp_path / "written"
    msgs = run(HANDSHAKE, gate_op(target), answer, request("ping", 4))
    events = [m for m in msgs if "event" in m]
    assert [m["event"] for m in events] == ["progress", "gate"]
    refused = [m for m in msgs if m.get("id") == 2 and "ok" in m]
    assert len(refused) == 1
    assert refused[0]["ok"] is False
    assert refused[0]["error"]["key"] == "protocol.malformed"
    assert not target.exists(), "the engine passed its gate without proceed"
    assert msgs[-1] == {"v": V, "id": 4, "op": "ping", "ok": True, "result": {"status": "ok"}}  # it keeps serving


def test_a_stray_proceed_with_no_gate_pending_is_refused_and_changes_nothing() -> None:
    msgs = run(HANDSHAKE, proceed(2, "test.gate"), request("ping", 3))
    assert msgs[1]["ok"] is False
    assert msgs[1]["error"]["key"] == "protocol.malformed"
    assert msgs[2]["result"] == {"status": "ok"}


class Recorder:
    """A job's channel: what the context sent, and the lines it may read."""

    def __init__(self, *incoming: bytes) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming = list(incoming)

    def send(self, raw: bytes) -> None:
        assert raw.endswith(b"\n")
        self.sent.append(json.loads(raw))

    def read_line(self) -> bytes:
        return self._incoming.pop(0) if self._incoming else b""


def test_committed_before_proceed_is_refused_inside_the_engine() -> None:
    ctx = JobContext(5, "save", Recorder())
    with pytest.raises(RuntimeError, match="proceed"):
        ctx.committed()


def test_committed_after_proceed_is_sent() -> None:
    chan = Recorder(proceed(5, "save"))
    ctx = JobContext(5, "save", chan)
    ctx.gate()
    ctx.committed()
    assert [m["event"] for m in chan.sent] == ["gate", "committed"]


def test_a_second_gate_in_one_request_is_refused_inside_the_engine() -> None:
    ctx = JobContext(5, "save", Recorder(proceed(5, "save")))
    ctx.gate()
    with pytest.raises(RuntimeError, match="gate"):
        ctx.gate()


def test_one_decision_per_reply() -> None:
    ctx = JobContext(5, "save", Recorder())
    ctx.decide("gate", 1, {"fingerprint_equal": True}, "pass")
    with pytest.raises(RuntimeError, match="decision"):
        ctx.decide("gate", 1, {}, "pass")


@pytest.mark.parametrize(
    ("table", "version", "inputs", "output"),
    [
        ("", 1, {}, "pass"),
        ("gate", 0, {}, "pass"),
        ("gate", 1, {"nested": {"a": 1}}, "pass"),  # inputs are flat scalars: no ledger content rides on them
        ("gate", 1, {"files": [1, 2]}, "pass"),
        ("gate", 1, {}, ""),
    ],
)
def test_a_decision_outside_its_shape_is_refused(table: str, version: int, inputs: Any, output: str) -> None:
    ctx = JobContext(5, "save", Recorder())
    with pytest.raises(ValidationError):
        ctx.decide(table, version, inputs, output)


def test_progress_names_one_of_the_specs_phases() -> None:
    chan = Recorder()
    ctx = JobContext(5, "save", chan)
    for phase in (
        "reading",
        "checking_change",
        "waiting_for_files",
        "saving",
        "updating_views",
        "checking_last_change",
    ):
        ctx.progress(phase)
    assert len(chan.sent) == 6
    with pytest.raises(ValidationError):
        ctx.progress("thinking")  # type: ignore[arg-type]
