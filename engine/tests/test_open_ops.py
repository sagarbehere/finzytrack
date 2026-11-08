"""The open's op on the protocol (`ledger_engine.protocol.opening`; `design/specs/engine-jobs.md` §2's `open`, §5.1,
§7; S0088, BL-0152), on the real file system (macOS, fresh `tmp_path` folders): `open` loads the ledger at `root` with
the overlay load and publishes its mirror under the cache folder the engine was started with; it is answered only by
an engine that holds the engine lock."""

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

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-05"),
]

MAIN = b'option "operating_currency" "USD"\n2026-01-01 open Assets:Cash\n2026-01-01 open Expenses:Food\n'


@pytest.fixture
def ws(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    led = tmp_path / "L"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(MAIN)
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    params: dict[str, Any] = {
        "root": str(main),
        "app_local_data": str(ald),
        "workspace_key": "k1",
        "lineage": "lin1",
        "floor": 0,
        "today": "2026-11-08",
        "built_at": "2026-11-08T10:00:00+05:30",
    }
    return main, ald, params


def call(ald: Path | None, params: dict[str, Any]) -> dict[str, Any]:
    events = Channel(2, proceed=False)
    reply = json.loads(engine(ald).handle(request("open", params, 2), events))
    reply["events"] = [m.get("phase") for m in events.sent]
    return reply


def test_the_open_loads_and_publishes_through_the_protocol(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    before = sorted(str(p) for p in main.parent.rglob("*"))
    reply = call(ald, params)
    assert reply["ok"], reply
    result = reply["result"]
    assert (result["outcome"], result["errors"], result["publish"], result["number"]) == ("loaded", 0, "published", 1)
    assert result["entries"] > 0
    assert result["refused"] is None
    assert reply["events"] == ["reading"]
    assert (reply["decision"]["table"], reply["decision"]["version"]) == ("engine.open", 1)
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    name = os.readlink(mirror / "current.db")
    con = sqlite3.connect(f"{(mirror / name).as_uri()}?mode=ro", uri=True)
    try:
        accounts = {a for (a,) in con.execute("SELECT name FROM r_accounts")}
        facts = dict(con.execute("SELECT key, value FROM m_meta").fetchall())
    finally:
        con.close()
    assert "Expenses:Food" in accounts
    assert facts["ledger_version"] == result["ledger_version"]
    again = call(ald, params)["result"]
    assert (again["outcome"], again["publish"], again["number"]) == ("fresh", "fresh", 1)
    assert sorted(str(p) for p in main.parent.rglob("*")) == before  # nothing written beside the ledger


def test_an_open_journal_is_paused(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    _, ald, params = ws
    journal = ald / "local" / "k1" / "journal"
    journal.mkdir(parents=True)
    (journal / "s1-j1.open").write_bytes(b"{}")
    result = call(ald, params)["result"]
    assert (result["outcome"], result["publish"], result["number"]) == ("paused", None, None)


def test_an_encrypted_include_is_refused_with_its_file(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    (main.parent / "secret.bean.gpg").write_bytes(b"\x85\x01not really")
    main.write_bytes(MAIN + b'include "secret.bean.gpg"\n')
    result = call(ald, params)["result"]
    assert (result["outcome"], result["refused"], result["publish"]) == (
        "refused",
        str(main.parent / "secret.bean.gpg"),
        None,
    )


def test_no_open_runs_in_an_engine_without_the_engine_lock(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    _, _, params = ws
    assert call(None, params)["error"]["key"] == "engine.no_engine_lock"


@pytest.mark.parametrize(
    "bad",
    [
        {"root": "relative"},
        {"app_local_data": "/elsewhere"},  # not the folder of this engine's lock
        {"app_cache": "/elsewhere"},  # no request names the cache folder
        {"fingerprint": "{}"},  # the open finds the ledger set itself
        {"workspace_key": ".."},
        {"workspace_key": "a/b"},
        {"floor": -1},
        {"today": "08/11/2026"},
        {"built_at": "yesterday"},
    ],
)
def test_malformed_params_are_refused(ws: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]) -> None:
    _, ald, params = ws
    reply = call(ald, {**params, **bad})
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "protocol.invalid_params"


def test_an_engine_started_without_its_cache_folder_publishes_nothing(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    _, ald, params = ws
    e = Engine(engine_lock_held=str(ald / "locks" / "engine.lock"))
    assert json.loads(e.handle(b'{"v":2,"id":0,"op":"handshake","params":{}}'))["ok"]
    reply = json.loads(e.handle(request("open", params, 2), Channel(2, proceed=False)))
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert not (ald.parent / "cache" / "workspaces").exists()
