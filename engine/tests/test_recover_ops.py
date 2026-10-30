"""Recover-all's ops on the protocol (`ledger_engine.protocol.recovery`; `design/specs/engine-jobs.md` §5.1, §8.1,
§11; S0062), on the real file system (macOS, fresh `tmp_path` folders).

From the spec and BL-0151's row: recover-all's listing and tries run "inside an engine (which holds the lock first)":
an engine that does not hold the engine lock answers none of these ops and touches nothing; an engine whose lock
another process holds (an engine of an earlier session) answers its handshake but not a recovery op until that
process lets the lock go; the ops list, try, keep and delete as `ledger.recover_all` does, only inside the app's own
journal folders.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger.writer import Change, Commit, Landed, durable_commit
from ledger_engine.protocol.server import Engine

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-32"),
]

DEADLINE = 15.0
PRE = b"2026-01-01 open Assets:A\n"
POST = b"2026-01-01 open Assets:A\n2026-01-02 *\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def journalled(ald: Path) -> tuple[Path, Path, str]:
    """A save landed on disk, its record renamed back to `.open`: a crash right before the close (every file at post,
    temps removed; §10's row "all replaced and read back, close not yet durable"). Returns the ledger file, the
    journal folder (canonical) and the record's name."""
    ledger = ald.parent / "L"
    ledger.mkdir()
    (ledger / "main.bean").write_bytes(PRE)
    jd = ald / "local" / "k1" / "journal"
    bd = ald / "local" / "k1" / "backups"
    jd.mkdir(parents=True)
    bd.mkdir(parents=True)
    from ledger_engine.fs.darwin import DarwinFS

    commit = Commit(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="b1",
        post_fingerprint="sha256:" + "f" * 64,
        adopted_digest="sha256:" + "a" * 64,
        parse_digest="sha256:" + "b" * 64,
        changes=(Change(str(ledger / "main.bean"), sha(PRE), POST), Change(str(ledger / "prices.bean"), None, b"p\n")),
        journal_dir=str(jd),
        backups_dir=str(bd),
    )
    assert isinstance(durable_commit(DarwinFS(), commit), Landed)
    os.rename(jd / "s1-j7.closed", jd / "s1-j7.open")
    return ledger, Path(os.path.realpath(jd)), "s1-j7"


class Rig:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.rid = 0
        assert self.send("handshake", {})["ok"]

    def send(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        self.rid += 1
        line = json.dumps({"v": 2, "id": self.rid, "op": op, "params": params}).encode()
        return json.loads(self.engine.handle(line))


@pytest.fixture
def ald(tmp_path: Path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    return path


def test_no_recovery_op_runs_in_an_engine_without_the_engine_lock(ald: Path) -> None:
    _, jd, name = journalled(ald)
    (jd / "s0-j1.part").write_bytes(b"torn")
    rig = Rig(Engine())
    for op, params in (
        ("recover_list", {"app_local_data": str(ald)}),
        ("recover_try", {"app_local_data": str(ald), "journal_dir": str(jd), "name": name}),
        ("recover_act", {"app_local_data": str(ald), "journal_dir": str(jd), "name": name, "action": "keep"}),
    ):
        reply = rig.send(op, params)
        assert not reply["ok"], reply
        assert reply["error"]["key"] == "engine.no_engine_lock"
    assert sorted(p.name for p in jd.iterdir()) == ["s0-j1.part", "s1-j7.open"], "an op without the lock touched files"


def test_the_listing_removes_a_part_and_names_each_open_journal(ald: Path) -> None:
    ledger, jd, name = journalled(ald)
    (jd / "s0-j1.part").write_bytes(b"torn")
    reply = Rig(Engine(engine_lock_held=True)).send("recover_list", {"app_local_data": str(ald)})
    assert reply["ok"], reply
    result = reply["result"]
    assert result["parts_removed"] == 1
    assert result["unlisted"] == []
    [j] = result["journals"]
    assert (j["journal_dir"], j["name"], j["readable"], j["workspace_key"], j["lineage"]) == (
        str(jd),
        name,
        True,
        "k1",
        "lin1",
    )
    assert {(f["path"].rpartition("/")[2], f["created"]) for f in j["files"]} == {
        ("main.bean", False),
        ("prices.bean", True),
    }
    assert not (jd / "s0-j1.part").exists()
    assert (ledger / "main.bean").read_bytes() == POST, "the listing wrote a ledger file"


def test_a_try_stops_at_the_created_path_with_what_stands_there_then_delete_it_and_retry_resolves(ald: Path) -> None:
    ledger, jd, name = journalled(ald)
    rig = Rig(Engine(engine_lock_held=True))
    base = {"app_local_data": str(ald), "journal_dir": str(jd), "name": name}
    first = rig.send("recover_try", base)
    assert first["ok"], first
    out = first["result"]
    assert (out["outcome"], out["cause"]) == ("stopped", "created_path_taken")
    assert first["decision"] == {
        "table": "engine.recovery",
        "version": 1,
        "inputs": {"cause": "created_path_taken"},
        "output": "stopped",
    }
    seen = {i["path"].rpartition("/")[2]: i for i in out["inspection"]}
    assert seen["prices.bean"]["now"] == {"kind": "file", "sha256": sha(b"p\n"), "size": 2}
    assert (ledger / "main.bean").read_bytes() == PRE, "the other file was not undone"
    wrong = rig.send("recover_act", {**base, "action": "delete_created", "file": out["path"], "sha256": sha(b"x")})
    assert wrong["result"]["outcome"] == "refused"
    assert wrong["result"]["reason"] == "changed"
    assert (ledger / "prices.bean").exists()
    done = rig.send("recover_act", {**base, "action": "delete_created", "file": out["path"], "sha256": sha(b"p\n")})
    assert done["result"] == {"outcome": "done", "reason": None, "path": None, "detail": None, "errno": None}
    assert not (ledger / "prices.bean").exists()
    again = rig.send("recover_try", base)
    assert again["result"]["outcome"] == "resolved", again
    assert sorted(p.name for p in jd.iterdir()) == ["s1-j7.resolved"]


def test_keep_and_delete_the_kept_copy_on_the_real_disk(ald: Path) -> None:
    ledger, jd, name = journalled(ald)
    kept = ledger / "main.bean.kept-s1-j7"
    kept.write_bytes(b"the editor's\n")
    rig = Rig(Engine(engine_lock_held=True))
    base = {"app_local_data": str(ald), "journal_dir": str(jd), "name": name}
    out = rig.send("recover_try", base)["result"]
    assert (out["outcome"], out["cause"]) == ("stopped", "kept_copy")
    main = next(i for i in out["inspection"] if i["path"].endswith("/main.bean"))
    assert [(k["name"], k["seen"]["sha256"]) for k in main["kept"]] == [(kept.name, sha(b"the editor's\n"))]
    act = {**base, "action": "delete_kept", "file": main["path"], "kept": kept.name, "sha256": sha(b"the editor's\n")}
    assert rig.send("recover_act", act)["result"]["outcome"] == "done"
    assert not kept.exists()
    keep = rig.send("recover_act", {**base, "action": "keep"})
    assert keep["result"]["outcome"] == "done", keep
    assert sorted(p.name for p in jd.iterdir()) == ["s1-j7.kept"]
    assert (ledger / "main.bean").read_bytes() == POST, "Keep wrote a ledger file"


@pytest.mark.parametrize(
    "params",
    [
        {"action": "delete_kept", "file": "/x/main.bean", "sha256": "0" * 64},  # no `kept`
        {"action": "delete_created", "file": "/x/prices.bean"},  # no `sha256`
        {"action": "delete_created", "sha256": "0" * 64},  # no `file`
    ],
)
def test_an_action_without_what_it_needs_is_refused(ald: Path, params: dict[str, Any]) -> None:
    _, jd, name = journalled(ald)
    reply = Rig(Engine(engine_lock_held=True)).send(
        "recover_act", {"app_local_data": str(ald), "journal_dir": str(jd), "name": name, **params}
    )
    assert not reply["ok"]
    assert reply["error"]["key"] == "protocol.invalid_params"


@pytest.mark.parametrize("where", ["ledger", "local", "key", "other_app"])
def test_no_op_works_in_a_folder_that_is_not_one_of_the_apps_journals(ald: Path, tmp_path: Path, where: str) -> None:
    ledger, jd, name = journalled(ald)
    other = tmp_path / "other" / "local" / "k1" / "journal"
    other.mkdir(parents=True)
    (other / f"{name}.open").write_bytes((jd / f"{name}.open").read_bytes())
    folder = {
        "ledger": str(ledger),
        "local": str(jd.parent.parent),
        "key": str(jd.parent),
        "other_app": str(other),
    }[where]
    rig = Rig(Engine(engine_lock_held=True))
    for op, extra in (("recover_try", {}), ("recover_act", {"action": "keep"})):
        reply = rig.send(op, {"app_local_data": str(ald), "journal_dir": folder, "name": name, **extra})
        assert not reply["ok"], reply
        assert reply["error"]["key"] == "protocol.invalid_params"
    assert (other / f"{name}.open").exists()
    assert (jd / f"{name}.open").exists()
    assert (ledger / "main.bean").read_bytes() == POST


# -- the gap: an engine whose lock another process holds ---------------------------------------------------------


class Replies:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._lines: list[dict[str, Any]] = []
        self._got = threading.Condition()
        assert proc.stdout is not None
        threading.Thread(target=self._pump, args=(proc.stdout,), daemon=True).start()

    def _pump(self, out: Any) -> None:
        for line in out:
            with self._got:
                self._lines.append(json.loads(line))
                self._got.notify_all()

    def wait(self, n: int, timeout: float = DEADLINE) -> bool:
        with self._got:
            return self._got.wait_for(lambda: len(self._lines) >= n, timeout)

    def __getitem__(self, i: int) -> dict[str, Any]:
        return self._lines[i]


def test_the_listing_waits_for_an_earlier_sessions_engine_to_let_the_engine_lock_go(ald: Path) -> None:
    """BL-0151's gap: the sidecar's own wait can miss an engine of an earlier session between its handshake reply and
    its `flock`. Recovery runs in an engine that takes the lock before it reads its next request, so while another
    process holds the lock the listing is not answered and the `.part` is still there; once it lets go, the listing
    runs."""
    _, jd, _ = journalled(ald)
    (jd / "s0-j1.part").write_bytes(b"torn")
    lock = ald / "locks" / "engine.lock"
    lock.parent.mkdir(exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)  # an engine of an earlier session holds it
    proc = subprocess.Popen(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", "--engine-lock", str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        replies = Replies(proc)
        assert proc.stdin is not None
        for rid, op, params in ((1, "handshake", {}), (2, "recover_list", {"app_local_data": str(ald)})):
            proc.stdin.write(json.dumps({"v": 2, "id": rid, "op": op, "params": params}).encode() + b"\n")
        proc.stdin.flush()
        assert replies.wait(1), "the handshake was not answered"
        assert replies[0]["ok"]
        assert not replies.wait(2, timeout=1.0), "the listing ran while another process held the engine lock"
        assert (jd / "s0-j1.part").exists(), "the `.part` was removed while another process held the engine lock"
        os.close(fd)  # the earlier engine ends
        fd = -1
        assert replies.wait(2), "the listing was not answered once the lock was free"
        assert replies[1]["ok"], replies[1]
        assert replies[1]["result"]["parts_removed"] == 1
        assert not (jd / "s0-j1.part").exists()
    finally:
        if fd >= 0:
            os.close(fd)
        proc.kill()
        proc.wait()
