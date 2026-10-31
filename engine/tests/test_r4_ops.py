"""R4's ops on the protocol (`ledger_engine.protocol.r4`; `design/specs/engine-jobs.md` §5.1, §6.7; S0066), on the real
file system (macOS, fresh `tmp_path` folders) after a real `save` through the same server: `r4_check` passes and says
it loaded; `r4_attempt` counts; `r4_resume` lifts a `.mismatch` only; each is answered only by an engine that holds
the engine lock, only for the app's own folder, and only for a record name and one key folder."""

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


def saved(main: Path, ald: Path, params: dict[str, Any]) -> dict[str, Any]:
    reply = json.loads(engine(ald).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["result"]["outcome"] == "saved", reply
    return {"app_local_data": str(ald), "key": "k1", "name": "s1-j7"}


def call(ald: Path | None, op: str, params: dict[str, Any]) -> dict[str, Any]:
    events = Channel(2, proceed=False)
    reply = json.loads(engine(ald).handle(request(op, params, 2), events))
    reply["events"] = [m.get("phase") for m in events.sent]
    return reply


def test_a_saved_ledger_passes_r4_through_the_protocol(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    target = saved(main, ald, params)
    reply = call(ald, "r4_check", target)
    assert reply["ok"], reply
    assert reply["result"] == {
        "outcome": "pass",
        "loaded": True,
        "record": str(ald / "local" / "k1" / "journal" / "s1-j7.done"),
        "backup": str(ald / "local" / "k1" / "backups" / "s1-j7"),
    }
    assert reply["events"] == ["checking_last_change"]
    assert reply["decision"]["table"] == "engine.r4"


def test_attempts_then_resume_through_the_protocol(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    target = saved(main, ald, params)
    assert call(ald, "r4_resume", target)["result"]["outcome"] == "gone"  # a pending check is never lifted
    outs = [call(ald, "r4_attempt", target)["result"]["outcome"] for _ in range(3)]
    assert outs == ["counted", "counted", "could_not_check"]
    assert call(ald, "r4_check", target)["result"]["outcome"] == "gone"  # R4 never touches a `.mismatch`
    assert call(ald, "r4_resume", target)["result"]["outcome"] == "done"
    assert (ald / "local" / "k1" / "journal" / "s1-j7.done").is_file()


@pytest.mark.parametrize("op", ["r4_check", "r4_confirm", "r4_attempt", "r4_resume"])
def test_no_r4_op_runs_in_an_engine_without_the_engine_lock(setup: tuple[Path, Path, dict[str, Any]], op: str) -> None:
    main, ald, params = setup
    target = saved(main, ald, params)
    assert call(None, op, target)["error"]["key"] == "engine.no_engine_lock"
    assert (ald / "local" / "k1" / "journal" / "s1-j7.closed").is_file()


@pytest.mark.parametrize(
    "bad",
    [
        {"key": ".."},
        {"key": "."},
        {"key": "a/b"},
        {"name": "s1"},
        {"name": "s1-j7.closed"},
        {"name": "../s1-j7"},
        {"app_local_data": "relative"},
    ],
)
def test_malformed_params_are_refused_and_nothing_moves(
    setup: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]
) -> None:
    main, ald, params = setup
    target = saved(main, ald, params)
    for op in ("r4_check", "r4_attempt", "r4_resume"):
        reply = call(ald, op, {**target, **bad})
        assert reply["error"]["key"] == "protocol.invalid_params", (op, reply)
    assert sorted(p.name for p in (ald / "local" / "k1" / "journal").iterdir()) == ["s1-j7.closed"]


def test_another_app_folder_is_refused(setup: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    main, ald, params = setup
    target = saved(main, ald, params)
    other = tmp_path / "other"
    (other / "locks").mkdir(parents=True)
    reply = call(other, "r4_attempt", target)
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert sorted(p.name for p in (ald / "local" / "k1" / "journal").iterdir()) == ["s1-j7.closed"]
