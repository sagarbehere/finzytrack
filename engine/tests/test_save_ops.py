"""The save's ops on the protocol (`ledger_engine.protocol.save`; `design/specs/engine-jobs.md` §4, §5.1, §6.1, §6.6;
S0063), on the real file system (macOS, fresh `tmp_path` folders): `save` sends its phases, `gate`, waits for
`proceed`, then `committed`; without `proceed` nothing is written; `save_outcome` reads the record; both are answered
only by an engine that holds the engine lock, and only for the app's own folder; malformed params are refused before
anything is read."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import overlay
from ledger_engine.ledger.core.ledger_set import digest, encode
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.protocol.server import Engine, InputEnded

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
    params: dict[str, Any] = {
        "session": "s1",
        "job": "j7",
        "workspace_key": "k1",
        "lineage": "lin1",
        "root": str(main),
        "changes": [{"path": str(main), "pre": sha(PRE), "post": POST}],
        "fingerprint": encode(loaded.fingerprint).decode("ascii"),
        "mirror": digest(loaded.fingerprint),
        "legacy_locks": [],
        "app_local_data": str(ald),
    }
    return main, ald, params


def engine(ald: Path | None) -> Engine:
    e = Engine(engine_lock_held=None if ald is None else str(ald / "locks" / "engine.lock"))
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
    assert reply["decision"]["table"] == "engine.save"
    events = [str(m["event"]) + (":" + str(m["phase"]) if "phase" in m else "") for m in channel.sent]
    assert events == ["progress:checking_change", "gate", "progress:saving", "committed"]
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
