"""The save's ops on the protocol (`ledger_engine.protocol.save`; `design/specs/engine-jobs.md` §4, §5.1, §6.1, §6.6;
S0063), on the real file system (macOS, fresh `tmp_path` folders): `save` sends its phases, `gate`, waits for
`proceed`, then `committed`; without `proceed` nothing is written; `save_outcome` reads the record; both are answered
only by an engine that holds the engine lock, and only for the app's own folder; malformed params are refused before
anything is read."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core.ledger_set import encode
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.mirror import publish as pb
from ledger_engine.protocol.server import Engine, InputEnded
from tests.mirror.publish_support import publish_job

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
]

PRE = b"2026-01-01 open Assets:Cash\n"
POST = "2026-01-01 open Assets:Cash\n2026-01-01 open Expenses:Food\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Channel:
    """The protocol channel: what the engine sent, and `proceed` for the request's gate when `proceed` is set."""

    def __init__(self, rid: int, *, proceed: bool) -> None:
        self.sent: list[dict[str, Any]] = []
        self._rid = rid
        self._proceed = proceed

    def send(self, raw: bytes) -> None:
        self.sent.append(json.loads(raw))

    def read_line(self) -> bytes:
        if not self._proceed:
            return b""
        return json.dumps({"v": 2, "id": self._rid, "op": "save", "control": "proceed"}).encode() + b"\n"


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    ledger = tmp_path / "L"
    ledger.mkdir()
    main = ledger / "main.bean"
    main.write_bytes(PRE)
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    loaded = overlay.load(str(main), {}, DesktopPolicy())
    assert isinstance(loaded, overlay.Loaded)
    cache = tmp_path / "cache"
    cache.mkdir()
    published = publish_job(for_this_os(), cache, main, ledger=loaded.fingerprint.ledger_set)  # the gate reads it
    assert isinstance(published, pb.Published), published
    params: dict[str, Any] = {
        "session": "s1",
        "job": "j7",
        "workspace_key": "k1",
        "lineage": "lin1",
        "root": str(main),
        "changes": [{"path": str(main), "pre": sha(PRE), "post": POST}],
        "fingerprint": encode(loaded.fingerprint).decode("ascii"),
        "app_local_data": str(ald),
    }
    return main, ald, params


def engine(ald: Path | None, cache: Path | None = None) -> Engine:
    """An engine holding the lock under `ald`, started with the app cache `cache` (default: `ald`'s sibling `cache`,
    the setup's; S0087: the cache is anchored at the start, never named by a request)."""
    if cache is None and ald is not None:
        cache = ald.parent / "cache"
    e = Engine(
        engine_lock_held=None if ald is None else str(ald / "locks" / "engine.lock"),
        app_cache=None if cache is None else str(cache),
    )
    assert json.loads(e.handle(b'{"v":2,"id":0,"op":"handshake","params":{}}'))["ok"]
    return e


def request(op: str, params: dict[str, Any], rid: int = 1) -> bytes:
    return json.dumps({"v": 2, "id": rid, "op": op, "params": params}).encode()


def test_a_save_sends_its_gate_lands_after_proceed_and_says_committed(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    channel = Channel(1, proceed=True)
    reply = json.loads(engine(ald).handle(request("save", params), channel))
    assert reply["ok"], reply
    assert reply["result"]["outcome"] == "saved"
    assert (reply["decision"]["table"], reply["decision"]["version"]) == ("engine.save", 4)
    # S0074: the history trim's counts ride on the save's decision (content-free; nothing to trim here)
    assert reply["decision"]["inputs"] == {
        "reason": None,
        "unchanged": False,
        "trimmed": 0,
        "trim_left": 0,
        "trim_skipped": False,
    }
    events = [str(m["event"]) + (":" + str(m["phase"]) if "phase" in m else "") for m in channel.sent]
    assert events == ["gate", "progress:saving", "committed"]  # no load, no "checking_change" (S0070)
    assert main.read_bytes() == POST.encode()
    assert (ald / "local" / "k1" / "journal" / "s1-j7.closed").is_file()


def test_without_proceed_nothing_is_written(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    with pytest.raises(InputEnded):
        engine(ald).handle(request("save", params), Channel(1, proceed=False))
    assert main.read_bytes() == PRE
    assert not (ald / "local").exists()


def test_a_refusal_comes_back_as_a_result(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    main.write_bytes(PRE + b"; edited\n")
    reply = json.loads(engine(ald).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["unchanged"]) == ("refused", "ledger_changed", False)


@pytest.mark.parametrize("op", ["save", "save_outcome"])
def test_no_save_op_runs_in_an_engine_without_the_engine_lock(
    setup: tuple[Path, Path, dict[str, Any]], op: str
) -> None:
    main, ald, params = setup
    if op == "save_outcome":
        params = {"app_local_data": str(ald), "workspace_key": "k1", "session": "s1", "job": "j7",
                  "targets": [{"path": str(main), "pre": sha(PRE)}]}  # fmt: skip
    reply = json.loads(engine(None).handle(request(op, params), Channel(1, proceed=True)))
    assert reply["error"]["key"] == "engine.no_engine_lock"
    assert main.read_bytes() == PRE


@pytest.mark.parametrize(
    "bad",
    [
        {"session": "S1"},  # never two sessions that differ only in case
        {"job": "J7"},
        {"workspace_key": "../k1"},
        {"workspace_key": ".."},
        {"changes": []},
        {"fingerprint": "{}"},
        {"changes": [{"path": "/x", "pre": None, "post": "\ud800"}]},
        {"legacy_locks": []},  # ADR-0068 D4: a save tries no legacy lock, so its plan names none (S0071)
    ],
)
def test_malformed_params_are_refused_and_nothing_is_written(
    setup: tuple[Path, Path, dict[str, Any]], bad: dict[str, Any]
) -> None:
    main, ald, params = setup
    reply = json.loads(engine(ald).handle(request("save", {**params, **bad}), Channel(1, proceed=True)))
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert main.read_bytes() == PRE
    assert not (ald / "local").exists()


def test_another_app_folder_is_refused(setup: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    main, _, params = setup
    other = tmp_path / "other"
    (other / "locks").mkdir(parents=True)
    reply = json.loads(engine(other).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert main.read_bytes() == PRE


def test_save_outcome_reads_the_record_after_a_save(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    main, ald, params = setup
    e = engine(ald)
    assert json.loads(e.handle(request("save", params), Channel(1, proceed=True)))["result"]["outcome"] == "saved"
    outcome = {"app_local_data": str(ald), "workspace_key": "k1", "session": "s1", "job": "j7",
               "targets": [{"path": str(main), "pre": sha(PRE)}]}  # fmt: skip
    reply = json.loads(e.handle(request("save_outcome", outcome, 2)))
    assert reply["result"] == {"record": "closed", "unchanged": None, "path": None, "errno": None}
    reply = json.loads(e.handle(request("save_outcome", {**outcome, "job": "j8"}, 3)))
    assert reply["result"] == {"record": "none", "unchanged": False, "path": str(main), "errno": None}


def test_a_landed_save_trims_its_workspaces_history_on_disk(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """S0074 (spec §6.5): on APFS, through the op: 17 older `.closed` saves and the new one leave 16."""
    _, ald, params = setup
    journal, backups = ald / "local" / "k1" / "journal", ald / "local" / "k1" / "backups"
    journal.mkdir(parents=True)
    backups.mkdir()
    for i in range(17):
        (journal / f"s0-j{i:02d}.closed").write_bytes(b"{}")
        (backups / f"s0-j{i:02d}").mkdir()
        (backups / f"s0-j{i:02d}" / "0-main.bean").write_bytes(b"old")
        os.utime(journal / f"s0-j{i:02d}.closed", ns=(i * 10**9, i * 10**9))
    reply = json.loads(engine(ald).handle(request("save", params), Channel(1, proceed=True)))
    assert reply["result"]["outcome"] == "saved", reply
    assert reply["decision"]["inputs"]["trimmed"] == 2
    assert sorted(p.name for p in backups.iterdir()) == sorted([*(f"s0-j{i:02d}" for i in range(2, 17)), "s1-j7"])
    assert not (journal / "s0-j00.closed").exists()
    assert not (journal / "s0-j01.closed").exists()
