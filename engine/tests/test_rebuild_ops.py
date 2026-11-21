"""The rebuild's op on the protocol (`ledger_engine.protocol.rebuild`; `design/specs/engine-jobs.md` §5.1, §6.7, §7;
ADR-0068 D1; S0070), on the real file system (macOS, fresh `tmp_path` folders) after a real `save` through the same
server: `rebuild` loads the saved ledger with Beancount's own loader and answers counts; it is answered only by an
engine that holds the engine lock, and only for a fingerprint of its root. The R4 op is gone."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.protocol.server import Engine
from tests.mirror.publish_support import current_in
from tests.test_save_ops import Channel, engine, request, save_setup
from tests.test_save_ops import setup as setup  # the fixture, shared

pytestmark = [
    pytest.mark.usefixtures("windows_port"),  # Windows: the adapter (S0130, BL-0206 done)
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


@pytest.fixture
def fresh_setup(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """The shared setup, then a fresh engine's module cache for the rebuild (S0142, BL-0299): the setup's load imports
    Beancount's lazily imported steps (`beancount.ops.documents`, `beancount.ops.pad`, when no earlier test did) and
    records them in the fingerprint, the rebuild's hint. A rebuild in the same process would find them in
    `sys.modules`, record none, and its stamp would not cover the hint, so its second rebuild would load again. Every
    job that loads runs in a fresh engine (engine-jobs §5.1), so the modules the setup imported are dropped here."""
    before = set(sys.modules)
    made = save_setup(tmp_path)
    for name in set(sys.modules) - before:
        del sys.modules[name]
    return made


def test_a_saved_ledger_is_rebuilt_through_the_protocol(fresh_setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = fresh_setup
    target = saved(ald, params)
    before = sorted(str(p) for p in main.parent.rglob("*"))
    reply = call(ald, "rebuild", target)
    assert reply["ok"], reply
    result = reply["result"]
    assert (result["outcome"], result["errors"]) == ("loaded", 0)
    assert result["entries"] > 0
    assert reply["events"] == ["updating_views"]
    assert (reply["decision"]["table"], reply["decision"]["version"]) == ("engine.rebuild", 5)
    # S0087 (BL-0113): published after the save's generation (number 1, the setup's), the save's change in it
    assert (result["publish"], result["number"]) == ("published", 2)
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    name = current_in(mirror)
    assert name is not None
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


def test_a_ledger_beancount_cannot_load_is_refused_through_the_protocol(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    main, ald, params = setup
    target = saved(ald, params)
    lines = main.read_bytes().count(b"\n")
    with main.open("ab") as f:  # an outside edit after the save
        f.write(b'2026-01-02 * "Coins"\n  Assets:Cash  12345678901.123456789012345678 ETH\n  Expenses:Food\n')
    reply = call(ald, "rebuild", target)
    assert reply["ok"], reply  # a refusal of the ledger, never `engine.internal`
    result = reply["result"]
    assert (result["outcome"], result["refusal"], result["refused"], result["line"]) == (
        "refused",
        "number_failed",
        str(main),
        lines + 1,
    )
    assert (result["publish"], result["number"]) == (None, None)
    assert reply["decision"]["inputs"]["refusal"] == "number-failed"


def test_a_cache_folder_on_a_network_share_refuses_the_rebuild_by_name(
    setup: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0134 (owner, 2026-11-19: "Ok. Option 1 for Bl-0298."): as the open's case (`tests/test_open_ops.py`), for the
    rebuild: refused `cache_on_share` before any effect, the folder named, never `engine.internal`."""
    import os

    from tests.test_open_ops import on_a_share, tree

    _, ald, params = setup
    target = saved(ald, params)
    cache = (ald.parent / "cache").resolve()
    cache.mkdir(exist_ok=True)
    on_a_share(monkeypatch, cache)
    before = tree(cache)
    reply = call(ald, "rebuild", target)
    assert reply["ok"], reply
    result = reply["result"]
    assert (result["outcome"], result["refusal"], result["publish"], result["line"]) == (
        "refused",
        "cache_on_share",
        None,
        None,
    )
    assert os.path.samefile(result["refused"], cache)
    assert result["fingerprint"] == target["fingerprint"]
    assert tree(cache) == before
    assert reply["decision"]["inputs"]["refusal"] == "cache-on-share"
    assert str(cache) not in json.dumps(reply["decision"])  # INV-23
