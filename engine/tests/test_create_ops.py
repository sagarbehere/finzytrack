"""The create's ops on the protocol (`ledger_engine.protocol.create`; `design/specs/engine-jobs.md` §4, §5.1, §8.2;
S0080), on the real file system (macOS, fresh `tmp_path` folders) with Beancount's own load: `create` writes the
files, checks that the ledger loads with no error from the files in the target, sends `gate`, and only after `proceed`
writes `.finzytrack/` last; `create_outcome` reads the target after its sync. Both are answered only by an engine
that holds the engine lock."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, override

import pytest

from ledger_engine.protocol.server import InputEnded
from tests.test_save_ops import Channel as SaveChannel
from tests.test_save_ops import engine, request

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
]

MAIN = 'option "title" "Books"\n2026-01-01 open Assets:Cash EUR\ninclude "prices/p.beancount"\n'
PRICES = "2026-01-02 price EUR 1.10 USD\n"
WS_JSON = (
    '{"format": 1, "min_reader": 1, "lineage_id": "0d4c1f1e-0b8a-4c2e-9a39-5b1f0f6f2a10",'
    ' "ledger": {"root": "main.beancount"}}\n'
)


class Channel(SaveChannel):
    """The protocol channel, with `proceed` for a `create`'s gate."""

    @override
    def read_line(self) -> bytes:
        line = super().read_line()
        return line.replace(b'"op": "save"', b'"op": "create"')


@pytest.fixture
def ald(tmp_path: Path) -> Path:
    out = tmp_path / "app"
    (out / "locks").mkdir(parents=True)
    return out


def params(target: Path, main: str = MAIN) -> dict[str, Any]:
    return {
        "job": "j7",
        "target": str(target),
        "files": [{"path": "main.beancount", "text": main}, {"path": "prices/p.beancount", "text": PRICES}],
        "root": "main.beancount",
        "manifest": [{"path": "workspace.json", "text": WS_JSON}],
    }


def create(ald: Path | None, p: dict[str, Any], *, proceed: bool = True) -> tuple[dict[str, Any], list[Any]]:
    channel = Channel(1, proceed=proceed)
    reply = json.loads(engine(ald).handle(request("create", p), channel))
    return reply, [m.get("phase") or m.get("event") for m in channel.sent]


def test_a_create_loads_its_ledger_gates_and_writes_the_manifest_last(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "Books"
    reply, events = create(ald, params(target))
    assert reply["ok"], reply
    assert reply["result"] == {
        "outcome": "created",
        "reason": None,
        "folder": str(target),
        "path": None,
        "detail": None,
        "errno": None,
        "advice": "none",
    }
    assert events == ["saving", "checking_change", "gate"]
    assert (target / ".finzytrack" / "workspace.json").read_text() == WS_JSON
    assert (target / "prices" / "p.beancount").read_text() == PRICES
    assert sorted(p.name for p in target.iterdir()) == [".finzytrack", "main.beancount", "prices"]
    assert reply["decision"]["table"] == "engine.create"


def test_without_proceed_no_manifest_is_written(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "Books"
    with pytest.raises(InputEnded):
        create(ald, params(target), proceed=False)
    assert (target / "main.beancount").read_text() == MAIN
    assert not (target / ".finzytrack").exists()


def test_a_ledger_with_beancount_errors_is_not_created(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "Books"
    failing = "2026-01-01 open Assets:Cash EUR\n2026-01-02 balance Assets:Cash 5 EUR\n"
    reply, events = create(ald, params(target, failing))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["advice"]) == ("not_created", "load_errors", "delete")
    assert "gate" not in events
    assert not (target / ".finzytrack").exists()


def test_a_ledger_that_includes_a_file_outside_the_folder_is_not_created(ald: Path, tmp_path: Path) -> None:
    (tmp_path / "other.beancount").write_text("2026-01-01 open Assets:Bank EUR\n")
    target = tmp_path / "Books"
    reply, _ = create(ald, params(target, MAIN + 'include "../other.beancount"\n'))
    r = reply["result"]
    assert (r["reason"], r["path"]) == ("load_outside", str((tmp_path / "other.beancount").resolve()))
    assert not (target / ".finzytrack").exists()


def test_a_non_empty_folder_is_refused_and_left_as_it_is(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "Books"
    target.mkdir()
    (target / "mine.txt").write_text("mine\n")
    reply, events = create(ald, params(target))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["advice"]) == ("not_created", "not_empty", "none")
    assert events == []
    assert sorted(p.name for p in target.iterdir()) == ["mine.txt"]


def test_create_outcome_reads_what_the_create_left(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "Books"
    e = engine(ald)
    assert json.loads(e.handle(request("create", params(target)), Channel(1, proceed=True)))["ok"]
    reply = json.loads(e.handle(request("create_outcome", params(target), 2)))
    assert reply["result"] == {"manifest": "ours", "advice": "delete", "errno": None}
    gone = json.loads(e.handle(request("create_outcome", params(tmp_path / "Nothing"), 3)))
    assert gone["result"] == {"manifest": "absent", "advice": "none", "errno": None}


@pytest.mark.parametrize("op", ["create", "create_outcome"])
def test_no_create_op_runs_in_an_engine_without_the_engine_lock(tmp_path: Path, op: str) -> None:
    target = tmp_path / "Books"
    reply = json.loads(engine(None).handle(request(op, params(target)), Channel(1, proceed=True)))
    assert reply["error"]["key"] == "engine.no_engine_lock"
    assert not target.exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"target": "relative/Books"},
        {"job": "J7"},
        {"root": "nope.beancount"},
        {"files": [{"path": "../escape.beancount", "text": MAIN}]},
        {"manifest": []},
    ],
)
def test_malformed_params_are_refused_and_nothing_is_written(ald: Path, tmp_path: Path, bad: dict[str, Any]) -> None:
    target = tmp_path / "Books"
    reply, _ = create(ald, {**params(target), **bad})
    assert reply["error"]["key"] == "protocol.invalid_params", reply
    assert not target.exists()
