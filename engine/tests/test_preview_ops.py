"""The preview's op on the protocol (`ledger_engine.protocol.preview`; `design/specs/engine-jobs.md` §2's `preview`,
§5.1, §7; S0116), on the real file system (macOS, fresh `tmp_path` folders): `open` publishes a generation, then
`preview` checks a change against it with the write-safety contract and writes nothing; the plan it returns passes
the save's gate. Answered only by an engine that holds the engine lock. The edit producer is the test-only one
(`tests/ledger/producer.py`), registered for each test under `producer.TEST_MUTATOR`."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import overlay, preview
from ledger_engine.ledger.core.ledger_set import decode
from ledger_engine.protocol.save import engine_build
from tests.ledger import producer
from tests.test_save_ops import Channel, engine, request

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-01"),
    pytest.mark.inv("INV-22"),
]

# A failing balance assertion (line 9) and a booking error (the sale, line 6): the baseline holds both.
MAIN = (
    b'option "operating_currency" "USD"\n'
    b"2024-01-01 open Assets:Broker\n"
    b"2024-01-01 open Assets:Pay USD\n"
    b'2024-01-02 * "buy"\n'
    b"  Assets:Broker  1 HOO {10 USD}\n"
    b"  Assets:Pay\n"
    b'2024-01-03 * "sell"\n'
    b"  Assets:Broker  -1 HOO {11 USD}\n"
    b"  Assets:Pay\n"
    b"2024-01-10 balance Assets:Pay  5 USD\n"
)
BUY = '2024-01-02 * "buy"\n  memo: "s0116"\n  Assets:Broker  1 HOO {10 USD}\n  Assets:Pay\n'


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, producer.produce_json)
    led = tmp_path / "L"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(MAIN)
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    return main, ald, {"root": str(main), "app_local_data": str(ald), "workspace_key": "k1", "lineage": "lin1"}


def opened(ald: Path, params: dict[str, Any]) -> int:
    o = {**params, "floor": 0, "today": "2026-11-14", "built_at": "2026-11-14T10:00:00+05:30"}
    reply = json.loads(engine(ald).handle(request("open", o, 2), Channel(2, proceed=False)))
    assert reply["ok"], reply
    assert reply["result"]["outcome"] == "loaded", reply
    return int(reply["result"]["number"])


def edits(main: Path, *items: dict[str, Any]) -> dict[str, Any]:
    return {"edits": [{"path": str(main), **i} for i in items]}


def call(
    ald: Path | None, params: dict[str, Any], generation: int, req: dict[str, Any], mutator: str = producer.TEST_MUTATOR
) -> dict[str, Any]:
    events = Channel(3, proceed=False)
    p = {**params, "generation": generation, "mutator": mutator, "request": req}
    reply = json.loads(engine(ald).handle(request("preview", p, 3), events))
    reply["events"] = [m.get("phase") for m in events.sent]
    return reply


def tree(folder: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file() or p.is_symlink()}


def replace_buy(main: Path) -> dict[str, Any]:
    return edits(main, {"kind": "replace", "line": 4, "date": "2024-01-02", "keyword": "txn", "text": BUY})


def test_a_preview_prepares_a_plan_that_the_save_then_lands(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """The replace adds a line above both old errors: neither is new (the baseline read from the published mirror is
    keyed as the candidate's errors are). The preview writes nothing; its plan passes the save's gate."""
    main, ald, params = ws
    number = opened(ald, params)
    before = tree(main.parent.parent)
    reply = call(ald, params, number, replace_buy(main))
    assert reply["ok"], reply
    result = reply["result"]
    assert (result["outcome"], result["new_errors"], result["errors"], result["step"]) == ("prepared", [], 2, None)
    [f] = result["files"]
    assert f["path"] == str(main)
    assert f["text"] == MAIN.decode().replace('2024-01-02 * "buy"\n', '2024-01-02 * "buy"\n  memo: "s0116"\n')
    assert reply["events"] == ["checking_change"]
    assert (reply["decision"]["table"], reply["decision"]["version"], reply["decision"]["output"]) == (
        "engine.preview",
        1,
        "prepared",
    )
    assert reply["decision"]["inputs"] == {
        "files": 1,
        "new_errors": 0,
        "errors": 2,
        "step": None,
        "reason": None,
        "stale": None,
    }
    assert tree(main.parent.parent) == before  # nothing written: not the ledger, the mirror or the journals
    assert decode(result["fingerprint"].encode()).root == str(main)

    save = {
        "session": "s1",
        "job": "j1",
        "workspace_key": "k1",
        "lineage": "lin1",
        "root": str(main),
        "changes": [{"path": f["path"], "pre": f["pre"], "post": f["text"]}],
        "fingerprint": result["fingerprint"],
        "app_local_data": str(ald),
    }
    saved = json.loads(engine(ald).handle(request("save", save, 4), Channel(4, proceed=True)))
    assert saved["ok"], saved
    assert saved["result"]["outcome"] == "saved", saved
    assert main.read_text() == f["text"]


def test_a_new_error_is_listed_with_its_key(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    req = edits(main, {"kind": "insert", "text": "2024-01-20 balance Assets:Pay  0 USD\n", "default_file": str(main)})
    del req["edits"][0]["path"]
    result = call(ald, params, number, req)["result"]
    assert result["outcome"] == "prepared"
    [e] = result["new_errors"]
    assert (e["type"], e["path"], e["line"], len(e["key"])) == ("BalanceError", str(main), 12, 64)


def test_an_unknown_mutator_is_refused_at_the_plan(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    result = call(ald, params, number, replace_buy(main), mutator="account.create")["result"]
    assert (result["outcome"], result["step"], result["reason"], result["files"]) == (
        "refused",
        "plan",
        "unknown_mutator",
        [],
    )


def test_a_target_the_producer_cannot_locate_is_refused_at_the_plan(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    req = edits(main, {"kind": "replace", "line": 5, "date": "2024-01-02", "keyword": "txn", "text": BUY})
    result = call(ald, params, number, req)["result"]
    assert (result["outcome"], result["step"], result["reason"]) == ("refused", "plan", "no_entry_at_position")


def test_no_published_generation_is_stale(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    result = call(ald, params, 1, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"]) == ("stale", "unpublished")


def test_another_generation_than_the_plan_s_is_stale(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    result = call(ald, params, number + 1, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"]) == ("stale", "other_generation")


def test_a_disk_changed_since_the_publish_is_stale(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    main.write_bytes(MAIN + b"2024-02-01 open Assets:Other\n")
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"], result["files"]) == ("stale", "not_fresh", [])


def test_another_ledger_s_generation_is_stale(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    other = main.parent / "other.bean"
    other.write_bytes(MAIN)
    result = call(ald, {**params, "root": str(other)}, number, replace_buy(other))["result"]
    assert (result["outcome"], result["stale"]) == ("stale", "not_fresh")


def test_an_open_journal_is_paused_and_nothing_is_loaded_over_it(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7: no result over an unfinished save, and its half-written files are not loaded (no plugin runs on them)."""
    main, ald, params = ws
    number = opened(ald, params)
    journal = ald / "local" / "k1" / "journal"
    journal.mkdir(parents=True)
    (journal / "s1-j1.open").write_bytes(b"{}")
    loads: list[str] = []
    real = overlay.load

    def spy(root: str, candidates: Any, policy: Any) -> Any:
        loads.append(root)
        return real(root, candidates, policy)

    monkeypatch.setattr(overlay, "load", spy)
    result = call(ald, params, number, replace_buy(main))["result"]
    assert result["outcome"] == "paused"
    assert loads == []


def test_a_journal_that_appears_during_the_job_is_paused(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, ald, params = ws
    number = opened(ald, params)

    def produce_then_journal(snapshot: Any, req: Any) -> Any:
        journal = ald / "local" / "k1" / "journal"
        journal.mkdir(parents=True)
        (journal / "s1-j1.open").write_bytes(b"{}")
        return producer.produce_json(snapshot, req)

    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, produce_then_journal)
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["files"]) == ("paused", [])


def test_a_file_the_plan_does_not_edit_changed_during_the_job_is_moved(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An included file changes after the snapshot was read and stays changed: the load read it, so the plan's own
    fingerprint observes equal, but the baseline is no longer the disk's (its errors and the routing's options were
    the old file's)."""
    main, ald, params = ws
    other = main.parent / "other.bean"
    other.write_bytes(b"2024-01-01 open Assets:Other\n")
    main.write_bytes(MAIN + b'include "other.bean"\n')
    number = opened(ald, params)

    def produce_then_edit(snapshot: Any, req: Any) -> Any:
        out = producer.produce_json(snapshot, req)
        other.write_bytes(b"2024-01-01 open Assets:Other\n2024-01-01 open Assets:More\n")
        return out

    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, produce_then_edit)
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["files"]) == ("moved", [])


def test_a_file_changed_during_the_job_is_moved(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generation is fresh when the job starts; the ledger changes while the producer plans (as an editor's save
    would): the re-check sees it and nothing is returned."""
    main, ald, params = ws
    number = opened(ald, params)

    def produce_then_edit(snapshot: Any, req: Any) -> Any:
        out = producer.produce_json(snapshot, req)
        main.write_bytes(MAIN + b"; edited meanwhile\n")
        return out

    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, produce_then_edit)
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["files"]) == ("moved", [])


def test_no_preview_runs_in_an_engine_without_the_engine_lock(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, _, params = ws
    assert call(None, params, 1, replace_buy(main))["error"]["key"] == "engine.no_engine_lock"


@pytest.mark.parametrize(
    "bad",
    [
        {"root": "relative"},
        {"app_local_data": "/elsewhere"},  # not the folder of this engine's lock
        {"workspace_key": ".."},
        {"generation": 0},
        {"mutator": "Not An Id"},
        {"request": []},
    ],
)
def test_malformed_params_are_refused(ws: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]) -> None:
    main, ald, params = ws
    p = {**params, "generation": 1, "mutator": producer.TEST_MUTATOR, "request": replace_buy(main), **bad}
    reply = json.loads(engine(ald).handle(request("preview", p, 3), Channel(3, proceed=False)))
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "protocol.invalid_params"


def test_the_preview_s_fingerprint_names_the_plugin_code_the_open_s_stamp_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-0106 (S0099 round 1): the plan's fingerprint and the generation's stamp record the same module lookups on the
    real job paths, else no rebuild after the save would find its generation fresh. The plugin is dropped between the
    two jobs, as the fresh engine each job runs in would start without it."""
    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, producer.produce_json)
    case = Path(__file__).resolve().parents[2] / "corpora" / "ledgers" / "write" / "pythonpath-plugin"
    led = tmp_path / "L"
    import shutil

    shutil.copytree(case, led, ignore=shutil.ignore_patterns("__pycache__"))
    main = led / "main.beancount"
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    params = {"root": str(main), "app_local_data": str(ald), "workspace_key": "k1", "lineage": "lin1"}
    number = opened(ald, params)
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if isinstance(file, str) and os.path.realpath(file).startswith(os.path.realpath(led)):
            del sys.modules[name]
    req = {"edits": [{"kind": "insert", "text": '2099-01-01 note Assets:Cash "s0116"\n', "default_file": str(main)}]}
    result = call(ald, params, number, req)["result"]
    assert result["outcome"] == "prepared", result
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    con = sqlite3.connect(f"{(mirror / os.readlink(mirror / 'current.db')).as_uri()}?mode=ro", uri=True)
    try:
        stamp = dict(con.execute("SELECT key, value FROM m_meta").fetchall())["fingerprint"]
    finally:
        con.close()
    modules = decode(result["fingerprint"].encode()).modules
    assert modules
    assert modules == decode(stamp.encode()).modules


def test_a_producer_that_fails_refuses_at_the_plan(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, ald, params = ws
    number = opened(ald, params)

    def broken(snapshot: Any, req: Any) -> Any:
        msg = "a producer's bug"
        raise RuntimeError(msg)

    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, broken)
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["step"], result["reason"], result["detail"]) == (
        "refused",
        "plan",
        "fault",
        "RuntimeError",
    )


def test_a_file_only_the_plan_reads_changed_after_the_load_is_moved(
    ws: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The insert names a statement that does not exist yet; the generation never read that path, the plan's load
    did. The file appears right after the load: only the plan's own fingerprint, observed again, sees it."""
    main, ald, params = ws
    number = opened(ald, params)
    receipt = main.parent / "receipt.pdf"
    real = overlay.load

    def then_create(root: str, candidates: Any, policy: Any) -> Any:
        out = real(root, candidates, policy)
        receipt.write_bytes(b"%PDF")
        return out

    monkeypatch.setattr(overlay, "load", then_create)
    text = '2024-01-20 document Assets:Pay "receipt.pdf"\n'
    req = {"edits": [{"kind": "insert", "text": text, "default_file": str(main)}]}
    result = call(ald, params, number, req)["result"]
    assert (result["outcome"], result["files"]) == ("moved", [])


def where(ald: Path, engine_build: str) -> preview.Where:
    return preview.Where(str(ald.parent / "cache"), "k1", str(ald), "lin1", engine_build)


class NoJob:
    def progress(self, phase: str) -> None:
        pass


def test_another_engine_build_s_generation_is_stale(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """Another build may list other errors (its warnings on numbers, say): its generation is no baseline."""
    main, ald, params = ws
    number = opened(ald, params)
    a = preview.run(
        preview.Ports(), str(main), number, producer.TEST_MUTATOR, replace_buy(main), where(ald, "other"), NoJob()
    )
    assert (a.outcome, a.stale) == ("stale", "not_fresh")
    b = preview.run(
        preview.Ports(),
        str(main),
        number,
        producer.TEST_MUTATOR,
        replace_buy(main),
        where(ald, engine_build()),
        NoJob(),
    )
    assert b.outcome == "prepared", b


def test_a_file_read_otherwise_than_the_generation_read_it_is_moved(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """The bytes read for the plan must be the generation's, also of a file the plan does not edit (the producer reads
    it, for one, for the routing options), else the plan is not made on the generation."""
    main, ald, params = ws
    other = main.parent / "other.bean"
    other.write_bytes(b"2024-01-01 open Assets:Other\n")
    main.write_bytes(MAIN + b'include "other.bean"\n')
    number = opened(ald, params)

    def read(path: str) -> bytes:
        data = Path(path).read_bytes()
        return data + b"; read later\n" if path == str(other) else data

    ports = preview.Ports(read=read)
    a = preview.run(
        ports, str(main), number, producer.TEST_MUTATOR, replace_buy(main), where(ald, engine_build()), NoJob()
    )
    assert (a.outcome, a.prepared) == ("moved", None)
