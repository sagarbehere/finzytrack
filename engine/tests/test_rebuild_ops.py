"""The rebuild's op on the protocol (`ledger_engine.protocol.rebuild`; `design/specs/engine-jobs.md` §5.1, §6.7, §7;
ADR-0068 D1; S0070), on the real file system (macOS, fresh `tmp_path` folders) after a real `save` through the same
server: `rebuild` loads the saved ledger with Beancount's own loader and answers counts; it is answered only by an
engine that holds the engine lock, and only for a fingerprint of its root. The R4 op is gone."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.protocol.server import Engine
from tests.test_save_ops import Channel, engine, request
from tests.test_save_ops import setup as setup  # the fixture, shared

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
]


def saved(ald: Path, params: dict[str, Any]) -> dict[str, Any]:
    reply = json.loads(engine(ald).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["result"]["outcome"] == "saved", reply
    return {
        "root": params["root"],
        "fingerprint": params["fingerprint"],
        "app_local_data": params["app_local_data"],
        "workspace_key": params["workspace_key"],
        "lineage": params["lineage"],
        "floor": 0,
        "today": "2026-11-08",
        "built_at": "2026-11-08T10:00:00+05:30",
    }


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
    assert (reply["decision"]["table"], reply["decision"]["version"]) == ("engine.rebuild", 2)
    # S0087 (BL-0113): published after the save's generation (number 1, the setup's), the save's change in it
    assert (result["publish"], result["number"]) == ("published", 2)
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    name = os.readlink(mirror / "current.db")
    assert result["ledger_version"].startswith("2/sha256:")
    con = sqlite3.connect(f"{(mirror / name).as_uri()}?mode=ro", uri=True)
    try:
        accounts = {a for (a,) in con.execute("SELECT name FROM r_accounts")}
        facts = dict(con.execute("SELECT key, value FROM m_meta").fetchall())
    finally:
        con.close()
    assert "Expenses:Food" in accounts  # the saved change
    assert facts["ledger_version"] == result["ledger_version"]
    assert facts["as_of"] == "2026-11-08"
    again = call(ald, "rebuild", target)["result"]
    assert (again["outcome"], again["publish"], again["number"]) == ("fresh", "fresh", 2)
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
    [
        {"fingerprint": "not a fingerprint"},
        {"root": "/elsewhere/main.bean"},
        {"root": "relative"},
        {"fingerprint": ""},
        {"app_local_data": "/elsewhere"},  # not the folder of this engine's lock
        {"app_cache": "/elsewhere"},  # no request names the cache folder: it is the engine's from its start
        {"workspace_key": ".."},
        {"workspace_key": "a/b"},
        {"floor": -1},
        {"today": "08/11/2026"},
    ],
)
def test_malformed_params_are_refused(setup: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]) -> None:
    _, ald, params = setup
    target = saved(ald, params)
    reply = call(ald, "rebuild", {**target, **bad})
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "protocol.invalid_params"


def test_a_rebuild_never_cleans_a_folder_a_request_names(
    setup: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    """S0087 round 1 (red team F3): the mirror folder is under the cache folder the engine was started with; a request
    that names another one is refused before anything is read, made or removed there."""
    _, ald, params = setup
    target = saved(ald, params)
    other = tmp_path / "Documents"
    (other / "workspaces" / "k1" / "mirror").mkdir(parents=True)
    (other / "workspaces" / "k1" / "mirror" / "keep.txt").write_bytes(b"the user's")
    reply = call(ald, "rebuild", {**target, "app_cache": str(other)})
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert (other / "workspaces" / "k1" / "mirror" / "keep.txt").read_bytes() == b"the user's"


def test_an_engine_started_without_its_cache_folder_publishes_nothing(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    _, ald, params = setup
    target = saved(ald, params)
    events = Channel(2, proceed=False)
    e = Engine(engine_lock_held=str(ald / "locks" / "engine.lock"))
    assert json.loads(e.handle(b'{"v":2,"id":0,"op":"handshake","params":{}}'))["ok"]
    reply = json.loads(e.handle(request("rebuild", target, 2), events))
    assert reply["error"]["key"] == "protocol.invalid_params"
