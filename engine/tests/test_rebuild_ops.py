"""The rebuild's op on the protocol (`ledger_engine.protocol.rebuild`; `design/specs/engine-jobs.md` §5.1, §6.7, §7;
ADR-0068 D1; S0070), on the real file system (macOS, fresh `tmp_path` folders) after a real `save` through the same
server: `rebuild` loads the saved ledger with Beancount's own loader and answers counts; it is answered only by an
engine that holds the engine lock, and only for a fingerprint of its root. The R4 op is gone."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_save_ops import Channel, engine, request
from tests.test_save_ops import setup as setup  # the fixture, shared

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
]


def saved(ald: Path, params: dict[str, Any]) -> dict[str, Any]:
    reply = json.loads(engine(ald).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["result"]["outcome"] == "saved", reply
    return {"root": params["root"], "fingerprint": params["fingerprint"]}


def call(ald: Path | None, op: str, params: dict[str, Any]) -> dict[str, Any]:
    events = Channel(2, proceed=False)
    reply = json.loads(engine(ald).handle(request(op, params, 2), events))
    reply["events"] = [m.get("phase") for m in events.sent]
    return reply


def test_a_saved_ledger_is_rebuilt_through_the_protocol(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    target = saved(ald, params)
    before = sorted(str(p) for p in main.parent.rglob("*"))
    reply = call(ald, "rebuild", target)
    assert reply["ok"], reply
    result = reply["result"]
    assert (result["outcome"], result["errors"]) == ("loaded", 0)
    assert result["entries"] > 0
    assert reply["events"] == ["updating_views"]
    assert reply["decision"]["table"] == "engine.rebuild"
    assert sorted(str(p) for p in main.parent.rglob("*")) == before  # it writes nothing (no pickle cache either)


def test_the_r4_op_is_gone(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """ADR-0068 D1 (S0070): no comparison after a save."""
    _, ald, params = setup
    saved(ald, params)
    reply = call(ald, "r4_check", {"app_local_data": str(ald), "key": "k1", "name": "s1-j7"})
    assert reply["error"]["key"] == "protocol.unknown_op"


def test_no_rebuild_runs_in_an_engine_without_the_engine_lock(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    _, ald, params = setup
    target = saved(ald, params)
    assert call(None, "rebuild", target)["error"]["key"] == "engine.no_engine_lock"


@pytest.mark.parametrize(
    "bad",
    [{"fingerprint": "not a fingerprint"}, {"root": "/elsewhere/main.bean"}, {"root": "relative"}, {"fingerprint": ""}],
)
def test_malformed_params_are_refused(setup: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]) -> None:
    _, ald, params = setup
    target = saved(ald, params)
    reply = call(ald, "rebuild", {**target, **bad})
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "protocol.invalid_params"
