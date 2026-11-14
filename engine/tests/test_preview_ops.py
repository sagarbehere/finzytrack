"""The preview's op on the protocol (`ledger_engine.protocol.preview`; `design/specs/engine-jobs.md` §2's `preview`,
§5.1, §7; S0116), on the real file system (macOS, fresh `tmp_path` folders): `open` publishes a generation, then
`preview` checks a change against it with the write-safety contract and writes nothing; the plan it returns passes
the save's gate. Answered only by an engine that holds the engine lock. The edit producer is the test-only one
(`tests/ledger/producer.py`), registered for each test under `producer.TEST_MUTATOR`."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, override

import pytest

from ledger_engine.ledger import overlay, preview
from ledger_engine.ledger.core.ledger_set import decode
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.core import schema
from ledger_engine.mirror.store import SqliteStore
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
        2,
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
        "confine": None,
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


def published_stamp(ald: Path) -> str:
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    con = sqlite3.connect(f"{(mirror / os.readlink(mirror / 'current.db')).as_uri()}?mode=ro", uri=True)
    try:
        return str(dict(con.execute("SELECT key, value FROM m_meta").fetchall())["fingerprint"])
    finally:
        con.close()


def with_glob(main: Path) -> Path:
    """An `include` pattern, so a stamp must name it (S0118 round 1, red team RT-F1)."""
    sub = main.parent / "sub"
    sub.mkdir()
    (sub / "a.bean").write_text("2024-01-01 open Assets:A\n")
    main.write_bytes(MAIN + b'include "sub/*.bean"\n')
    return sub


def test_no_published_generation_is_stale_with_no_hint(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """S0118 round 1 (red team RT-F1; owner, 2026-11-14: "Narrow the scope (Recommended)"): with no stamp of this
    ledger there is no set to rebuild from that names its include patterns (the root alone would drop them), so the
    answer carries no hint and nothing is queued; a reopen builds the mirror (engine-jobs §15)."""
    main, ald, params = ws
    with_glob(main)
    result = call(ald, params, 1, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"], result["fingerprint"]) == ("stale", "unpublished", None)


def test_another_generation_than_the_plan_s_is_stale_with_no_hint(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """@regression-guard (no stale answer carried a fingerprint on the base): the mirror may be fresh, so the caller
    plans again on the current generation; nothing to rebuild."""
    main, ald, params = ws
    number = opened(ald, params)
    result = call(ald, params, number + 1, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"], result["fingerprint"]) == ("stale", "other_generation", None)


def test_a_disk_changed_since_the_publish_is_stale_with_the_published_stamp_as_its_hint(
    ws: tuple[Path, Path, dict[str, Any]],
) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    main.write_bytes(MAIN + b"2024-02-01 open Assets:Other\n")
    result = call(ald, params, number, replace_buy(main))["result"]
    assert (result["outcome"], result["stale"], result["files"]) == ("stale", "not_fresh", [])
    assert result["fingerprint"] == published_stamp(ald)  # the generation's own stamp, as published


def rebuilt(ald: Path, params: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    """The sidecar's rebuild job: tries again with the answer's fingerprint after `moved`, at most 3 tries (§7)."""
    result: dict[str, Any] = {}
    for _ in range(3):
        p = {
            **params,
            "fingerprint": fingerprint,
            "floor": 0,
            "today": "2026-11-14",
            "built_at": "2026-11-14T10:00:00Z",
        }
        reply = json.loads(engine(ald).handle(request("rebuild", p, 5), Channel(5, proceed=False)))
        assert reply["ok"], reply
        result = reply["result"]
        if result["outcome"] != "moved":
            return result
        fingerprint = result["fingerprint"]
    raise AssertionError(result)


def test_a_rebuild_from_the_hint_keeps_the_stamps_include_patterns(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """@regression-guard (the stamp's hint was right on the base; the root-only hint was not, RT-F1): a file a sync
    tool adds under a glob include makes the generation not fresh; the rebuild from the answer's hint publishes a
    stamp that still names the pattern, so the next file added under it is seen as a change too (ADR-0015)."""
    main, ald, params = ws
    sub = with_glob(main)
    number = opened(ald, params)
    (sub / "b.bean").write_text("2024-01-01 open Assets:B\n")
    first = call(ald, params, number, replace_buy(main))["result"]
    assert (first["outcome"], first["stale"]) == ("stale", "not_fresh")
    assert decode(first["fingerprint"].encode()).globs  # the stamp names the pattern
    done = rebuilt(ald, params, first["fingerprint"])
    assert (done["outcome"], done["publish"]) == ("loaded", "published")
    assert [g for g, _ in decode(published_stamp(ald).encode()).globs] == [str(sub / "*.bean")]
    assert call(ald, params, done["number"], replace_buy(main))["result"]["outcome"] == "prepared"
    (sub / "c.bean").write_text("2024-01-01 open Assets:C\n")
    again = call(ald, params, done["number"], replace_buy(main))["result"]
    assert (again["outcome"], again["stale"]) == ("stale", "not_fresh")


def test_another_ledger_s_generation_is_stale_with_no_hint(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    """Never a hint of another root: the engine's `rebuild` refuses one (`params.root: not the fingerprint's root`)."""
    main, ald, params = ws
    number = opened(ald, params)
    other = main.parent / "other.bean"
    other.write_bytes(MAIN)
    result = call(ald, {**params, "root": str(other)}, number, replace_buy(other))["result"]
    assert (result["outcome"], result["stale"], result["fingerprint"]) == ("stale", "not_fresh", None)


class BadStamp(SqliteStore):
    """The published generation's stamp cannot be decoded."""

    @override
    def meta(self, d: Any, name: str) -> pb.Meta | None:
        m = super().meta(d, name)
        return None if m is None else dataclasses.replace(m, fingerprint="not a fingerprint")


def test_a_stamp_that_cannot_be_decoded_is_not_fresh_with_no_hint(ws: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = ws
    number = opened(ald, params)
    a = preview.run(
        preview.Ports(store=BadStamp()),
        str(main),
        number,
        producer.TEST_MUTATOR,
        replace_buy(main),
        where(ald, engine_build()),
        NoJob(),
    )
    assert (a.outcome, a.stale, a.hint) == ("stale", "not_fresh", None)


def drop_errors(ald: Path) -> None:
    """The published generation's `ledger_errors` made unreadable (its table gone), the rest of it intact."""
    mirror = ald.parent / "cache" / "workspaces" / "k1" / "mirror"
    table = next(t for t in schema.TABLES if t.name == "ledger_errors")
    con = sqlite3.connect(mirror / os.readlink(mirror / "current.db"))
    try:
        con.execute(f"DROP TABLE {table.base}")
        con.commit()
    finally:
        con.close()


@pytest.mark.parametrize("job", ["open", "rebuild"])
def test_a_generation_whose_errors_cannot_be_read_is_not_fresh_and_is_built_again(
    ws: tuple[Path, Path, dict[str, Any]], job: str
) -> None:
    """S0118 round 1 (verifier V1; owner, 2026-11-14: "Align freshness (Recommended)"): the preview cannot use a
    generation whose error table it cannot read as its baseline, so the open and the rebuild do not take it as fresh
    (`publishing.fresh_current`): they build a new one, and the preview prepares a plan on it."""
    main, ald, params = ws
    number = opened(ald, params)
    stamp = published_stamp(ald)
    drop_errors(ald)
    assert call(ald, params, number, replace_buy(main))["result"]["stale"] == "unpublished"
    if job == "open":
        again = opened(ald, params)  # `loaded`, never `fresh` (asserted by `opened`)
    else:
        done = rebuilt(ald, params, stamp)
        assert (done["outcome"], done["publish"]) == ("loaded", "published")
        again = int(done["number"])
    assert again != number
    assert call(ald, params, again, replace_buy(main))["result"]["outcome"] == "prepared"


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
    assert a.hint == decode(published_stamp(ald).encode())  # readable and this root's: the stamp itself
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


def test_a_new_error_at_line_0_is_listed_without_a_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S0116 round 1 F1: `check_commodity` puts its errors at line 0 of `<check_commodity>`: listed (ADR-0067), the
    line and the file left out, never an `engine.internal`."""
    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, producer.produce_json)
    led = tmp_path / "L"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(
        b'plugin "beancount.plugins.check_commodity"\n2024-01-01 commodity USD\n'
        b"2024-01-01 open Assets:Cash\n2024-01-01 open Assets:Bank\n"
    )
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    params = {"root": str(main), "app_local_data": str(ald), "workspace_key": "k1", "lineage": "lin1"}
    number = opened(ald, params)
    text = '2024-02-01 * "x"\n  Assets:Cash  1 EUR\n  Assets:Bank  -1 EUR\n'
    reply = call(ald, params, number, {"edits": [{"kind": "insert", "text": text, "default_file": str(main)}]})
    assert reply["ok"], reply
    result = reply["result"]
    assert result["outcome"] == "prepared", result
    assert [(e["type"], e["path"], e["line"]) for e in result["new_errors"]] == [("CheckCommodityError", None, None)]


MINUS = """\
import collections

__plugins__ = ["minus"]
MinusError = collections.namedtuple("MinusError", "source message entry")


def minus(entries, options_map):
    errors = [
        MinusError({"filename": e.meta["filename"], "lineno": -1}, "an error with no line", None)
        for e in entries
        if getattr(e, "narration", None) == "minus"
    ]
    return entries, errors
"""


def test_a_new_error_at_line_minus_1_is_listed_without_a_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S0116 round 1 F1: a plugin's error at line -1 (as Beancount's `summarize` gives) is listed without a line."""
    monkeypatch.setitem(preview.PRODUCERS, producer.TEST_MUTATOR, producer.produce_json)
    led = tmp_path / "L"
    led.mkdir()
    (led / "s0116_minus.py").write_text(MINUS)
    main = led / "main.bean"
    main.write_bytes(
        b'option "insert_pythonpath" "TRUE"\nplugin "s0116_minus"\n'
        b"2024-01-01 open Assets:Cash\n2024-01-01 open Assets:Bank\n"
    )
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    params = {"root": str(main), "app_local_data": str(ald), "workspace_key": "k1", "lineage": "lin1"}
    number = opened(ald, params)
    for name, module in list(sys.modules.items()):  # a fresh engine for the preview, as the sidecar runs it
        file = getattr(module, "__file__", None)
        if isinstance(file, str) and os.path.realpath(file).startswith(os.path.realpath(led)):
            del sys.modules[name]
    text = '2024-02-01 * "minus"\n  Assets:Cash  1 USD\n  Assets:Bank\n'
    reply = call(ald, params, number, {"edits": [{"kind": "insert", "text": text, "default_file": str(main)}]})
    assert reply["ok"], reply
    result = reply["result"]
    assert result["outcome"] == "prepared", result
    assert [(e["type"], e["path"], e["line"]) for e in result["new_errors"]] == [("MinusError", str(main), None)]


def test_a_load_refusal_names_its_plugin_and_line_in_the_answer() -> None:
    """S0116 round 1 F5: a refusal's subject that is no path (a plugin's name) leads the detail; its line is kept."""
    from ledger_engine.ledger import contract
    from ledger_engine.protocol.preview import result_of

    refused = contract.Refused("load", "beancount-failed", "my_plugin", "the load of the candidate cannot run", 7)
    out = result_of(preview.Answer("refused", refused=refused))
    assert (out.path, out.line) == (None, 7)
    assert out.detail is not None
    assert out.detail.startswith("my_plugin: ")
    on_disk = contract.Refused("load", "number-failed", "/l/main.bean", "the load of the candidate cannot run", 0)
    out = result_of(preview.Answer("refused", refused=on_disk))
    assert (out.path, out.line, out.detail) == ("/l/main.bean", None, "the load of the candidate cannot run")
