"""The save's ops on the protocol (`ledger_engine.protocol.save`; `design/specs/engine-jobs.md` §4, §5.1, §6.1, §6.6;
S0063), on the real file system (macOS, fresh `tmp_path` folders): `save` sends its phases, `gate`, waits for
`proceed`, then `committed`; without `proceed` nothing is written; `save_outcome` reads the record; both are answered
only by an engine that holds the engine lock, and only for the app's own folder; malformed params are refused before
anything is read."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core.ledger_set import encode
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.mirror import publish as pb
from ledger_engine.protocol.messages import OPS
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
        app_folder_id=None if ald is None else (os.stat(ald).st_dev, os.stat(ald).st_ino),
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


def test_S0100_r1_a_removed_app_folder_is_its_own_outcome_with_nothing_made(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    """S0100 round 1 (red team F2): the app folder this engine's lock was taken in, removed while the app runs, is
    `not_saved {app_folder_missing}` naming it (the sidecar's `msg:save.app_folder_missing`: quit and reopen), not a
    refused request; nothing is made in its parent."""
    import shutil

    main, ald, params = setup
    e = engine(ald)
    shutil.rmtree(ald)
    reply = json.loads(e.handle(request("save", params), Channel(1, proceed=True)))
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["entry"], r["unchanged"]) == (
        "not_saved",
        "app_folder_missing",
        str(ald),
        True,
    )
    assert not ald.exists()
    assert main.read_bytes() == PRE


def test_S0100_r1_another_missing_folder_is_still_refused(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """Must not pass: a request naming a missing folder that is not this engine's app folder is refused as before."""
    main, ald, params = setup
    other = ald.parent / "elsewhere"
    reply = json.loads(
        engine(ald).handle(request("save", {**params, "app_local_data": str(other)}), Channel(1, proceed=True))
    )
    assert not reply["ok"], reply
    assert not other.exists()
    assert main.read_bytes() == PRE


# -- S0102 (BL-0244; owner, 2026-11-11: "BL-0244 slice in M1a (Recommended)"): the app folder's identity from start --


def identity(p: Path) -> tuple[int, int]:
    st = os.stat(p)
    return (st.st_dev, st.st_ino)


def replace_folder(ald: Path) -> None:
    """Another program removes the app folder and puts an empty one in its place (S0100 round 2 red team `test_g`)."""
    shutil.rmtree(ald)
    ald.mkdir()


def held(ald: Path, app_folder: tuple[int, int]) -> Engine:
    """An engine holding the lock under `ald`, started with the app folder identity `app_folder`."""
    e = Engine(
        engine_lock_held=str(ald / "locks" / "engine.lock"),
        app_folder_id=app_folder,
        app_cache=str(ald.parent / "cache"),
    )
    assert json.loads(e.handle(b'{"v":2,"id":0,"op":"handshake","params":{}}'))["ok"]
    return e


def _save(e: Engine, params: dict[str, Any]) -> dict[str, Any]:
    reply = json.loads(e.handle(request("save", params), Channel(1, proceed=True)))
    assert reply["ok"], reply
    return reply["result"]


def test_S0102_a_save_in_a_replaced_app_folder_is_app_folder_missing_and_writes_nothing_there(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    """S0100 round 2 red team `test_g`: the app folder replaced by an empty one while this engine holds its lock."""
    main, ald, params = setup
    e = held(ald, identity(ald))
    replace_folder(ald)
    r = _save(e, params)
    assert (r["outcome"], r["reason"], r["entry"], r["unchanged"]) == (
        "not_saved",
        "app_folder_missing",
        str(ald),
        True,
    )
    assert os.listdir(ald) == []
    assert main.read_bytes() == PRE


def test_S0102_a_save_spelled_with_a_dot_in_a_removed_app_folder_is_app_folder_missing(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    """S0100 round 2 red team Low 2: `<app folder>/./` with the folder gone was `invalid_params`."""
    main, ald, params = setup
    e = held(ald, identity(ald))
    shutil.rmtree(ald)
    r = _save(e, {**params, "app_local_data": str(ald) + "/./"})
    assert (r["outcome"], r["reason"]) == ("not_saved", "app_folder_missing")
    assert not ald.exists()
    assert main.read_bytes() == PRE


def test_S0102_a_save_in_the_app_folder_of_its_start_lands(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """Must pass: the identity check lets the folder of the start through."""
    _, ald, params = setup
    assert _save(held(ald, identity(ald)), params)["outcome"] == "saved"


def test_S0102_an_engine_started_with_another_identity_saves_nothing(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """The check compares with the identity the engine was started with, not with the folder found now."""
    main, ald, params = setup
    dev, ino = identity(ald)
    r = _save(held(ald, (dev, ino + 1)), params)
    assert (r["outcome"], r["reason"]) == ("not_saved", "app_folder_missing")
    assert not (ald / "local").exists()
    assert main.read_bytes() == PRE


def test_S0102_the_ops_that_write_in_the_app_folder_are_the_ones_checked() -> None:
    """The inventory, by behaviour, of reads, syncs and writes (S0102 Hand-off): the save writes `local/<key>/journal`
    and `backups`; its outcome syncs the journal folder and reads the record there (round 1, verifier F1); the listing
    syncs journal folders and removes `.part`s; a try restores and renames records; an act keeps or deletes. The open,
    rebuild and copy only read the journals (their `.open` gate: §6.1 step 8's Limit) and the create's outcome reads
    its target only."""
    assert {op for op, spec in OPS.items() if spec.writes_app_folder} == {
        "save",
        "save_outcome",
        "recover_list",
        "recover_try",
        "recover_act",
    }


def test_S0102_the_save_op_passes_the_identity_of_its_start_to_step_8(
    setup: tuple[Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Must not pass with the protocol's plan carrying no identity: the folder is replaced after the op's own check,
    during the gate's observation."""
    from ledger_engine.ledger import save as sv
    from ledger_engine.ledger.fingerprint import observe as observe_set

    main, ald, params = setup
    e = held(ald, identity(ald))
    real = sv.Ports

    def observe(fp: Any) -> Any:
        if not (ald / "local").exists() and os.listdir(ald) != []:
            replace_folder(ald)
        return observe_set(fp.ledger_set)

    def ports(fs: FileSystem) -> sv.Ports:
        return real(fs, observe=observe)

    monkeypatch.setattr(sv, "Ports", ports)
    r = _save(e, params)
    assert (r["outcome"], r["reason"]) == ("not_saved", "app_folder_missing")
    assert os.listdir(ald) == []
    assert main.read_bytes() == PRE


def test_S0102_an_engine_that_holds_the_lock_with_no_identity_saves_nothing(
    setup: tuple[Path, Path, dict[str, Any]],
) -> None:
    """Fail closed: an engine whose start named no app folder identity (never the product's: `--engine-lock` needs
    `--app-folder-id`) writes nothing in the app folder."""
    main, ald, params = setup
    e = Engine(engine_lock_held=str(ald / "locks" / "engine.lock"), app_cache=str(ald.parent / "cache"))
    assert json.loads(e.handle(b'{"v":2,"id":0,"op":"handshake","params":{}}'))["ok"]
    r = _save(e, params)
    assert (r["outcome"], r["reason"]) == ("not_saved", "app_folder_missing")
    assert not (ald / "local").exists()
    assert main.read_bytes() == PRE


# -- S0102 round 1 (red team F1): every folder of step 8 checked again up to the first swap ------------------------


def _replaced_by_a_copy(ald: Path) -> None:
    """Another program puts a copy of the app folder in its place (a restore from a backup, a sync tool)."""
    ald.rename(ald.parent / "moved")
    shutil.copytree(ald.parent / "moved", ald)


def _records(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob("s1-j7*"))


@pytest.mark.parametrize("when", ["after_step_8", "after_the_backups", "after_the_part"])
def test_S0102_r1_a_folder_replaced_after_step_8_and_before_the_first_swap_is_app_folder_missing(
    setup: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """`rt102/test_rt_engine.py::test_A2_swap_after_step8`: the journal and the backups would land in the replacement,
    and the save would answer `saved`. The writer checks the app folder, the journal and the backups folders by
    identity before the backups, before the `.part` and before the rename to `.open`: not saved, nothing swapped."""
    from ledger_engine.ledger import save as sv
    from ledger_engine.ledger import writer

    main, ald, params = setup
    if when == "after_step_8":
        real_ensure = sv._ensure_dir  # pyright: ignore[reportPrivateUsage]

        def ensure(*a: Any, **k: Any) -> Any:
            out = real_ensure(*a, **k)
            if a[2] == "backups":
                _replaced_by_a_copy(ald)
            return out

        monkeypatch.setattr(sv, "_ensure_dir", ensure)
    elif when == "after_the_backups":
        real_backups = writer._backups  # pyright: ignore[reportPrivateUsage]

        def backups(*a: Any, **k: Any) -> Any:
            out = real_backups(*a, **k)
            _replaced_by_a_copy(ald)
            return out

        monkeypatch.setattr(writer, "_backups", backups)
    else:
        real_check = writer._check_anchor  # pyright: ignore[reportPrivateUsage]
        calls: list[int] = []

        def check(*a: Any) -> None:
            calls.append(1)
            if len(calls) == 3:  # the last check, right after the `.part` was made
                _replaced_by_a_copy(ald)
            real_check(*a)

        monkeypatch.setattr(writer, "_check_anchor", check)
    r = _save(held(ald, identity(ald)), params)
    assert (r["outcome"], r["reason"], r["unchanged"]) == ("not_saved", "app_folder_missing", True), r
    assert main.read_bytes() == PRE
    # no record that says a swap may have begun, in either folder (the copy holds what was there when it was made)
    assert not [n for n in _records(ald.parent) if n.endswith((".open", ".closed"))], _records(ald.parent)


def test_S0102_r1_a_folder_replaced_after_the_swaps_is_saved_and_never_trimmed(
    setup: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After every file is swapped, synced and read back the save has landed: `saved` is true. Its record and backups
    stay in the folder it wrote them in; the history trim never runs in the replacement."""
    from ledger_engine.ledger import history
    from ledger_engine.ledger import save as sv

    main, ald, params = setup
    real_commit = sv.durable_commit
    trimmed: list[str] = []

    def commit(*a: Any) -> Any:
        out = real_commit(*a)
        _replaced_by_a_copy(ald)
        return out

    monkeypatch.setattr(sv, "durable_commit", commit)
    monkeypatch.setattr(history, "trim", lambda *a: trimmed.append("trim"))  # type: ignore[misc]
    r = _save(held(ald, identity(ald)), params)
    assert r["outcome"] == "saved", r
    assert main.read_bytes() == POST.encode()
    assert trimmed == []
