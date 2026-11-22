"""OneDrive's placeholders and the engine, on the runner's real cloud filter (S0149, BL-0283; ADR-0070): a sync root
under the test's temp folder served by a provider in its own process (`tests/fs/cloud_provider.py`), and the engine's
own jobs (the open's overlay load, the save, the rebuild) on a ledger in it, through the protocol, in the mode the
engine pins (`this_os.pin_placeholder_mode`: `disguise_placeholder`; owner, 2026-11-22: "A"). Every Cloud Files
provider is alike (owner, 2026-11-22: "Yes let's support every such provider and word the text accordingly").

What they hold: the pin takes (and a child does not inherit it); a placeholder, cloud-only or downloaded, is a plain
file to the adapter and is read and saved, the save leaving a plain file at the name; links and junctions stay links;
offline (the provider disconnected) a cloud-only file is `cloud_unavailable`, naming it, in the save ("not saved",
nothing written), the open and the rebuild (refused), while a downloaded one still saves; the download error codes are
the cloud filter's by Windows' own message table. The facts of the 2026-11-22 run 11, which chose the mode, are in
`design/journal/S0149-onedrive.md`.

**What the simulation cannot show:** OneDrive's own sync engine (its upload after our rename, a hold during it, its
conflict copies, its own errors offline or paused): the owner's check by hand in S0150.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
# (the adapter's own error mapping is checked against Windows' answers, as `probe_windows_cloud.py` reads it)
import ctypes
import json
import os
import subprocess
import sys
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.port import DirHandle
from ledger_engine.fs.this_os import for_this_os
from tests.fresh_engine import fresh_engine
from tests.test_save_ops import POST, PRE, Channel, engine, published_save, request

WINDOWS_ONLY = "the cloud filter is Windows' (OneDrive's placeholders; S0149, BL-0283)"

if sys.platform != "win32":
    raise pytest.skip.Exception(WINDOWS_ONLY, allow_module_level=True)

import _winapi  # noqa: E402 (Windows only)

from ledger_engine.fs import this_os  # noqa: E402
from ledger_engine.fs import windows as win  # noqa: E402 (the adapter's own error mapping)
from ledger_engine.fs.port import CLOUD_DOWNLOAD_ERRORS, CloudUnavailable  # noqa: E402
from tests.fs.cloud_provider import ENGINE, PHCM_DISGUISE, PHCM_EXPOSE, Cloud, own_mode, set_mode  # noqa: E402

pytestmark = pytest.mark.usefixtures("windows_port")

LEDGER = "L/main.bean"
RECALL_ON_DATA_ACCESS = 0x400000
ATTRS = (
    (0x10, "DIRECTORY"),
    (0x20, "ARCHIVE"),
    (0x200, "SPARSE_FILE"),
    (0x400, "REPARSE_POINT"),
    (0x1000, "OFFLINE"),
    (0x40000, "RECALL_ON_OPEN"),
    (0x80000, "PINNED"),
    (0x100000, "UNPINNED"),
    (RECALL_ON_DATA_ACCESS, "RECALL_ON_DATA_ACCESS"),
)


def fact(test: str, **fields: Any) -> None:
    """A record for the run's log, visible in the warnings summary (the job runs `-rs`)."""
    warnings.warn(json.dumps({"S0149": test, **fields}, default=str), stacklevel=2)


@pytest.fixture(autouse=True)
def pinned() -> Iterator[None]:
    """This process plays the engine: its mode pinned as the engine's start pins it; the previous one put back."""
    previous = own_mode()
    assert this_os.pin_placeholder_mode()
    try:
        yield
    finally:
        set_mode(previous)


@pytest.fixture
def cloud(tmp_path: Path) -> Iterator[Cloud]:
    c = Cloud(tmp_path / "OneDrive")
    try:
        yield c
    finally:
        stopped = c.stop()
        if stopped is not None and not stopped.get("ok"):
            fact("provider_stop", answer=stopped)


def started(c: Cloud, files: dict[str, bytes], folders: tuple[str, ...] = ()) -> None:
    out = c.start(files, folders)
    assert out["ok"], out  # the simulation itself must start (S0144 Facts 1)


def look(path: Path) -> dict[str, Any]:
    """The entry as an exposing process sees it (the mode set to expose for the look, then put back): its attributes,
    reparse tag and whether it is cloud-only."""
    previous = set_mode(PHCM_EXPOSE)
    try:
        st = os.lstat(path)
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}", "winerror": getattr(e, "winerror", None)}
    finally:
        set_mode(previous)
    attrs = st.st_file_attributes
    return {
        "attrs": [n for bit, n in ATTRS if attrs & bit],
        "tag": f"{st.st_reparse_tag:#010x}",
        "cloud_only": bool(attrs & RECALL_ON_DATA_ACCESS),
        "size": st.st_size,
    }


def attempt(steps: list[dict[str, Any]], name: str, fn: Callable[[], Any]) -> Any:
    """`fn()` recorded as a step: its answer, or the exception it raised (a fact, never a failure here)."""
    try:
        got = fn()
    except Exception as e:  # noqa: BLE001 (phase 1 records every answer)
        steps.append({"step": name, "raised": f"{type(e).__name__}: {e}", "winerror": getattr(e, "winerror", None)})
        return None
    steps.append({"step": name, "answer": got})
    return got


def kind(d: DirHandle, name: str) -> str | None:
    st = d.stat(name)
    return None if st is None else st.kind.value


def reply_of(raw: bytes, channel: Channel | None = None) -> dict[str, Any]:
    reply: dict[str, Any] = json.loads(raw)
    out: dict[str, Any] = {"ok": reply.get("ok"), "result": reply.get("result"), "error": reply.get("error")}
    decision = reply.get("decision")
    if isinstance(decision, dict):
        out["decision_inputs"] = decision.get("inputs")  # pyright: ignore[reportUnknownMemberType]
    if channel is not None:
        out["events"] = [m.get("event") for m in channel.sent]
    return out


class Workspace:
    """A ledger at `<sync root>/L/main.bean`, its app folder and cache beside the sync root (never in it)."""

    def __init__(self, tmp_path: Path, c: Cloud) -> None:
        self.cloud = c
        self.main = c.root / "L" / "main.bean"
        self.ald = tmp_path / "app"
        (self.ald / "locks").mkdir(parents=True)
        (tmp_path / "cache").mkdir()
        self.steps: list[dict[str, Any]] = []
        self.save_params: dict[str, Any] | None = None

    def fetches_by_me(self) -> int:
        return sum(1 for f in self.cloud.fetches() if f.get("reader_pid") == os.getpid())

    def op(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        before = self.fetches_by_me()
        got = attempt(self.steps, name, fn)
        self.steps.append({"step": f"{name}: after", "fetches": self.fetches_by_me() - before, "look": look(self.main)})
        return got

    def open(self) -> dict[str, Any]:
        params = {
            "root": str(self.main),
            "app_local_data": str(self.ald),
            "workspace_key": "k1",
            "lineage": "lin1",
            "floor": 0,
            "today": "2026-11-08",
            "built_at": "2026-11-08T10:00:00+05:30",
        }
        channel = Channel(2, proceed=False)
        with fresh_engine():
            return reply_of(engine(self.ald).handle(request("open", params, 2), channel), channel)

    def prepare_save(self) -> dict[str, Any]:
        with fresh_engine():
            self.save_params = published_save(self.main, self.ald, POST)
        return {"pre": self.save_params["changes"][0]["pre"]}

    def save(self) -> dict[str, Any]:
        assert self.save_params is not None
        channel = Channel(1, proceed=True)
        return reply_of(engine(self.ald).handle(request("save", self.save_params), channel), channel)

    def rebuild(self) -> dict[str, Any]:
        assert self.save_params is not None
        p = self.save_params
        params = {
            "root": p["root"],
            "fingerprint": p["fingerprint"],
            "app_local_data": p["app_local_data"],
            "workspace_key": p["workspace_key"],
            "lineage": p["lineage"],
            "floor": 0,
            "today": "2026-11-08",
            "built_at": "2026-11-08T10:00:00+05:30",
        }
        channel = Channel(2, proceed=False)
        with fresh_engine():
            return reply_of(engine(self.ald).handle(request("rebuild", params, 2), channel), channel)

    def provider(self, op: str) -> dict[str, Any] | None:
        return attempt(self.steps, f"provider {op}", lambda: self.cloud.call(op, rel=LEDGER))

    def leftovers(self) -> dict[str, Any]:
        """What is on disk after the jobs: the ledger folder's names, and the app folder's journal and backups."""
        local = self.ald / "local"
        return {
            "ledger_folder": sorted(p.name for p in self.main.parent.iterdir()),
            "app_local": sorted(str(p.relative_to(local)) for p in local.rglob("*")) if local.exists() else [],
        }

    def contents(self) -> str:
        data = self.main.read_bytes()
        return "PRE" if data == PRE else "POST" if data == POST.encode() else f"other ({len(data)} bytes)"


@pytest.fixture
def ws(tmp_path: Path, cloud: Cloud) -> Workspace:
    started(cloud, {LEDGER: PRE}, ("L",))
    return Workspace(tmp_path, cloud)


# --- the mode -------------------------------------------------------------------------------------------------------


PIN_CHILD = (
    "from ledger_engine.fs import this_os; before = this_os.placeholder_mode(); ok = this_os.pin_placeholder_mode(); "
    "print(before, ok, this_os.placeholder_mode(), this_os.supported())"
)


@pytest.mark.parametrize("parent_mode", [PHCM_EXPOSE, PHCM_DISGUISE])
def test_the_pin_takes_in_a_fresh_process_whatever_its_parent_set(parent_mode: int) -> None:
    """The engine's start pins `disguise_placeholder` and reads it back; a child does not inherit its parent's mode (run
    11), so each process pins its own: here a child of this process, its parent in another mode."""
    previous = set_mode(parent_mode)
    try:
        done = subprocess.run(  # noqa: S603 (this interpreter, a fixed one-liner)
            [sys.executable, "-s", "-B", "-c", PIN_CHILD], cwd=ENGINE, capture_output=True, text=True, check=True
        )
    finally:
        set_mode(previous)
    before, ok, after, _supported = done.stdout.split()
    assert (ok, after) == ("True", str(this_os.PHCM_DISGUISE_PLACEHOLDER)), done.stdout
    fact("pin_child", parent=parent_mode, before=int(before))


def test_a_refused_pin_refuses_every_file_operation(tmp_path: Path) -> None:
    """Must not pass: an engine whose pin Windows refused runs in another mode. Its port is refused
    (`UnsupportedPlatform`) and so is every op that needs it (`engine.unsupported_platform`, naming the cause)."""
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    e = engine(ald, tmp_path / "cache")  # started (its app folder's identity read) before the refusal is set
    previous = set_mode(PHCM_EXPOSE)  # another mode than the pinned one: what a refused pin leaves
    try:
        assert this_os.pin_refused() == "the placeholder mode is 2 (expose_placeholders)"
        assert not this_os.supported()
        with pytest.raises(this_os.UnsupportedPlatform):
            for_this_os()
        reply = json.loads(e.handle(request("recover_list", {"app_local_data": str(ald)})))
    finally:
        set_mode(previous)
    assert reply["error"]["key"] == "engine.unsupported_platform", reply
    assert "cloud files" in reply["error"]["message"], reply


DOWNLOAD_WORDS: dict[int, tuple[str, ...]] = {
    362: ("not running",),  # ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING (probed: runs 1 and 11)
    386: ("authenticat",),  # _AUTHENTICATION_FAILED
    387: ("resource",),  # _INSUFFICIENT_RESOURCES
    388: ("network",),  # _NETWORK_UNAVAILABLE
    389: ("unsuccessful", "not successful", "failed"),  # _UNSUCCESSFUL
    393: ("abort",),  # _REQUEST_ABORTED
    398: ("cancel",),  # _REQUEST_CANCELED
    404: ("terminat", "exited", "stopped"),  # _PROVIDER_TERMINATED
    426: ("time",),  # _REQUEST_TIMEOUT
}
"""Each download code's meaning, as words its own message must hold (S0149 round 1, V4)."""
NOT_DOWNLOAD_WORDS: dict[int, tuple[str, ...]] = {
    376: ("not a cloud file",),  # ERROR_NOT_A_CLOUD_FILE (run 11's dehydrate of a plain file)
    377: ("sync",),  # _NOT_IN_SYNC
    391: ("in use",),  # _IN_USE
    392: ("pinned",),  # _PINNED
    395: ("access",),  # _ACCESS_DENIED
}
"""Cloud filter codes that are not "could not download": each is what its message says, and none is in the set."""


def test_each_download_code_means_what_the_set_says_by_windows_own_message() -> None:
    """Round 1 (V4): every code of `CLOUD_DOWNLOAD_ERRORS` is in `DOWNLOAD_WORDS`, and the runner's (English) message
    for it holds its words and "cloud"; the near codes of `NOT_DOWNLOAD_WORDS` hold theirs and are not in the set, so
    adding the in-use, pinned, access-denied or not-in-sync code to the set fails here. What this proves: each number
    names the error its constant says, by Windows' own message table on this build; not every message's exact
    wording, and not which code real OneDrive gives (S0150's check by hand)."""
    texts = {c: ctypes.FormatError(c) for c in sorted({*DOWNLOAD_WORDS, *NOT_DOWNLOAD_WORDS})}
    fact("codes", messages=texts)
    assert set(CLOUD_DOWNLOAD_ERRORS) == set(DOWNLOAD_WORDS)
    for c, words in DOWNLOAD_WORDS.items():
        t = texts[c].lower()
        assert "cloud" in t, (c, texts[c])
        assert any(w in t for w in words), (c, texts[c])
    for c, words in NOT_DOWNLOAD_WORDS.items():
        assert c not in CLOUD_DOWNLOAD_ERRORS, c
        assert any(w in texts[c].lower() for w in words), (c, texts[c])


def test_the_adapter_tells_a_download_code_only_at_a_read() -> None:
    """Round 1 (V2): `_read_error` (the `ReadFile` of a file's data) gives `CloudUnavailable` exactly for the download
    codes; `_win_error` (every other Win32 call) and `_nt_error` (every NTSTATUS: opens, renames, set-information,
    the folder's queries) never do, the cloud filter's own statuses included."""
    for c in CLOUD_DOWNLOAD_ERRORS:
        assert isinstance(win._read_error("ReadFile", code=c), CloudUnavailable), c
        assert not isinstance(win._win_error("FlushFileBuffers", code=c), CloudUnavailable), c
    for c in (5, 87, 363, 376, 391, 392, 395):
        assert not isinstance(win._read_error("ReadFile", code=c), CloudUnavailable), c
    for s in (*range(0xC000CF00, 0xC000D000), 0xC0000022):
        assert not isinstance(win._nt_error(s, "NtCreateFile"), CloudUnavailable), hex(s)


# --- placeholders, pinned ------------------------------------------------------------------------------------------


def test_a_cloud_only_file_is_a_plain_file_and_its_first_read_downloads_it(cloud: Cloud) -> None:
    """S0144 verifier F-2: the adapter's `sha256` (the save's fingerprint read) as the first read of a cloud-only file
    downloads it, once, and answers its bytes."""
    started(cloud, {"a.bean": PRE})
    assert look(cloud.root / "a.bean")["cloud_only"]
    with for_this_os().open_dir(str(cloud.root)) as d:
        assert kind(d, "a.bean") == "file"
        got = d.sha256("a.bean", 1 << 20)
    assert got is not None
    assert got[1] == len(PRE)
    fetches = [f for f in cloud.fetches() if f.get("rel") == "a.bean"]
    assert [f["reader_pid"] for f in fetches] == [os.getpid()], fetches
    assert not look(cloud.root / "a.bean")["cloud_only"]


def test_offline_the_adapters_read_of_a_cloud_only_file_is_cloud_unavailable(cloud: Cloud) -> None:
    started(cloud, {"a.bean": PRE})
    cloud.call("disconnect")
    with for_this_os().open_dir(str(cloud.root)) as d, pytest.raises(CloudUnavailable):
        d.read("a.bean")
    with pytest.raises(CloudUnavailable) as got:
        list(fingerprint_chunks(str(cloud.root / "a.bean")))  # the C runtime's read, asked again with `ReadFile`
    assert got.value.filename == str(cloud.root / "a.bean")


def fingerprint_chunks(p: str) -> Iterator[bytes]:
    from ledger_engine.ledger import fingerprint

    return fingerprint.chunks(p)


def test_links_and_junctions_stay_links_in_the_pinned_mode(tmp_path: Path) -> None:
    """The disguise hides only the cloud filter's placeholders: a file link, a folder link and a junction are `LINK`
    (refused by the save as before)."""
    (tmp_path / "f").write_bytes(b"x")
    (tmp_path / "dir").mkdir()
    os.symlink(tmp_path / "f", tmp_path / "flink")
    os.symlink(tmp_path / "dir", tmp_path / "dlink", target_is_directory=True)
    _winapi.CreateJunction(str(tmp_path / "dir"), str(tmp_path / "junc"))
    with for_this_os().open_dir(str(tmp_path)) as d:
        assert [kind(d, n) for n in ("flink", "dlink", "junc")] == ["link", "link", "link"]


# --- the jobs online -----------------------------------------------------------------------------------------------


def saved_result(reply: dict[str, Any] | None) -> dict[str, Any]:
    assert reply is not None
    assert reply["ok"], reply
    return reply["result"]


@pytest.mark.parametrize("state", ["hydrated", "cloud_only"])
def test_the_open_the_save_and_the_rebuild_land_on_a_placeholder(state: str, ws: Workspace) -> None:
    """Online: the open loads, the save lands on a downloaded or a cloud-only placeholder (downloaded by its own
    read), leaving a plain file with the new bytes at the name (no reparse point: the provider sees a new file, as
    OneDrive uploads one), and the rebuild loads it again after it was made cloud-only (as OneDrive does after its
    upload)."""
    opened = saved_result(ws.op("open", ws.open))
    assert opened["outcome"] == "loaded", opened
    ws.prepare_save()
    if state == "cloud_only":
        assert ws.provider("dehydrate") is not None
        assert look(ws.main)["cloud_only"]
    out = saved_result(ws.op("save", ws.save))
    assert out["outcome"] == "saved", out
    assert ws.contents() == "POST"
    after = look(ws.main)
    assert "REPARSE_POINT" not in after["attrs"], after
    assert not after["cloud_only"], after
    assert (ws.provider("dehydrate") or {}).get("ok"), ws.steps
    rebuilt = saved_result(ws.op("rebuild", ws.rebuild))
    assert rebuilt["outcome"] == "loaded", rebuilt


# --- offline --------------------------------------------------------------------------------------------------------


def test_offline_the_open_of_a_cloud_only_ledger_is_refused_by_name(ws: Workspace) -> None:
    ws.provider("disconnect")
    r = saved_result(ws.op("open", ws.open))
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(ws.main)), r
    assert look(ws.main)["cloud_only"]


def test_offline_the_save_of_a_cloud_only_file_is_not_saved_by_name_and_writes_nothing(ws: Workspace) -> None:
    """Not saved, `cloud_unavailable` naming the file, before the gate: no journal, no backup, the file still cloud-only
    and (online again) at its old bytes. The re-hash cannot read it, so "unchanged" is not said."""
    saved_result(ws.op("open", ws.open))
    ws.prepare_save()
    ws.provider("dehydrate")
    ws.provider("disconnect")
    r = saved_result(ws.op("save", ws.save))
    assert (r["outcome"], r["reason"], r["path"], r["unchanged"]) == (
        "not_saved",
        "cloud_unavailable",
        str(ws.main),
        False,
    ), r
    assert look(ws.main)["cloud_only"]
    assert ws.leftovers()["app_local"] == []
    ws.provider("connect")
    assert ws.contents() == "PRE"


def test_offline_a_downloaded_file_still_saves(ws: Workspace) -> None:
    saved_result(ws.op("open", ws.open))
    ws.prepare_save()
    ws.provider("disconnect")
    r = saved_result(ws.op("save", ws.save))
    assert r["outcome"] == "saved", r
    assert ws.contents() == "POST"


def test_offline_the_rebuild_of_a_cloud_only_ledger_is_refused_by_name(ws: Workspace) -> None:
    saved_result(ws.op("open", ws.open))
    ws.prepare_save()
    assert saved_result(ws.op("save", ws.save))["outcome"] == "saved"
    ws.provider("dehydrate")
    ws.provider("disconnect")
    r = saved_result(ws.op("rebuild", ws.rebuild))
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(ws.main)), r
