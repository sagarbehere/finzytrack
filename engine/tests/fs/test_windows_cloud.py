"""OneDrive's placeholders and the engine, on the runner's real cloud filter (S0149, BL-0283; S0144's facts): a sync
root under the test's temp folder served by a provider in its own process (`tests/fs/cloud_provider.py`), and the
engine's own jobs (the open's overlay load, the save, the rebuild) on a ledger in it, through the protocol, in each
placeholder compatibility mode of the engine's process: the one it inherits, `disguise_placeholder` and
`expose_placeholders`.

**Phase 1 (facts; the 2026-11-22 run 11):** each test records what happened as one JSON warning (`{"S0149": ...}`, in
the CI log's warnings summary: the job runs `-rs`, which prints no line for a passing test) and fails only when the
simulation itself does not start. Phase 2 turns the facts into the behaviour the owner chooses (the engine's mode, the
offline refusal) with must-not-pass tests.

**What the simulation cannot show:** OneDrive's own sync engine (its upload after our rename, a hold during it, its
conflict copies, its own errors offline or paused): the owner's check by hand in S0150.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.port import DirHandle
from ledger_engine.fs.this_os import PLACEHOLDER_MODES, for_this_os
from tests.fresh_engine import fresh_engine
from tests.test_save_ops import POST, PRE, Channel, engine, published_save, request

WINDOWS_ONLY = "the cloud filter is Windows' (OneDrive's placeholders; S0149, BL-0283)"

if sys.platform != "win32":
    raise pytest.skip.Exception(WINDOWS_ONLY, allow_module_level=True)

import _winapi  # noqa: E402 (Windows only)

from tests.fs.cloud_provider import (  # noqa: E402 (Windows only)
    ENGINE_FLAGS,
    PHCM_DISGUISE,
    PHCM_EXPOSE,
    Cloud,
    child_mode,
    own_mode,
    set_mode,
)

pytestmark = pytest.mark.usefixtures("windows_port")

INITIAL_MODE = own_mode()
"""The test process's mode before any test sets one."""
MODES = {"inherited": None, "disguise": PHCM_DISGUISE, "expose": PHCM_EXPOSE}
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
    """One fact for the run's record, visible in the warnings summary."""
    warnings.warn(json.dumps({"S0149": test, **fields}, default=str), stacklevel=2)


@pytest.fixture(params=list(MODES))
def mode(request: pytest.FixtureRequest) -> Iterator[str]:
    """This process's (the engine's, in these tests) placeholder mode for the test; the previous one is put back."""
    name: str = request.param
    value = MODES[name]
    previous = None if value is None else set_mode(value)
    try:
        yield name
    finally:
        if previous is not None:
            set_mode(previous)


@pytest.fixture
def cloud(tmp_path: Path) -> Iterator[Cloud]:
    c = Cloud(tmp_path / "OneDrive")
    try:
        yield c
    finally:
        fact("provider_stop", answer=c.stop())


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


# --- the modes ------------------------------------------------------------------------------------------------------


def test_S0149_phase1_the_placeholder_mode_of_each_process(cloud: Cloud) -> None:
    """Question 1, beside the backend's engine started by `EngineCommand.for_venv`
    (`backend/tests/platform/test_engine_client_spawn.py`): this test process's mode before any test set one, the mode
    of a child it starts (plain, and with the engine's flags), whether a child inherits a mode the parent set, and the
    provider's own and its children's (S0144 saw its readers, children of the provider, in `disguise_placeholder`)."""
    started(cloud, {"a.bean": PRE})
    seen: dict[str, Any] = {
        "pytest_initial": INITIAL_MODE,
        "pytest_now": own_mode(),
        "child_plain": child_mode(),
        "child_engine_flags": child_mode(*ENGINE_FLAGS),
    }
    for name, value in (("expose", PHCM_EXPOSE), ("disguise", PHCM_DISGUISE)):
        previous = set_mode(value)
        try:
            seen[f"after_parent_set_{name}"] = {"parent": own_mode(), "child": child_mode(*ENGINE_FLAGS)}
        finally:
            set_mode(previous)
    seen["provider"] = cloud.call("mode")
    seen["provider_children"] = cloud.call("child_mode")
    fact("modes", seen=seen, names=PLACEHOLDER_MODES)


# --- S0144's re-probes ----------------------------------------------------------------------------------------------


def test_S0149_phase1_sha256_as_the_first_read_of_a_cloud_only_file(mode: str, cloud: Cloud) -> None:
    """S0144 verifier F-2: the adapter's `sha256` (the save's fingerprint read) as the first read of a cloud-only
    file, through the port, in each mode."""
    started(cloud, {"a.bean": PRE})
    steps: list[dict[str, Any]] = [{"step": "before", "look": look(cloud.root / "a.bean"), "mode": own_mode()}]
    with for_this_os().open_dir(str(cloud.root)) as d:
        attempt(steps, "stat", lambda: kind(d, "a.bean"))
        got = attempt(steps, "sha256", lambda: d.sha256("a.bean", 1 << 20))
    steps.append({"step": "after", "look": look(cloud.root / "a.bean"), "fetches": cloud.fetches()})
    if got is not None:
        steps.append({"step": "is_content", "answer": got[1] == len(PRE)})
    fact("sha256_first", mode=mode, steps=steps)


def test_S0149_phase1_links_beside_placeholders(mode: str, tmp_path: Path) -> None:
    """Whether each mode still shows a symbolic link and a junction as links (`LINK`): a disguise must hide only the
    cloud filter's placeholders."""
    (tmp_path / "f").write_bytes(b"x")
    (tmp_path / "dir").mkdir()
    steps: list[dict[str, Any]] = []
    attempt(steps, "symlink file", lambda: os.symlink(tmp_path / "f", tmp_path / "flink"))
    attempt(steps, "symlink dir", lambda: os.symlink(tmp_path / "dir", tmp_path / "dlink", target_is_directory=True))
    attempt(steps, "junction", lambda: _winapi.CreateJunction(str(tmp_path / "dir"), str(tmp_path / "junc")))
    with for_this_os().open_dir(str(tmp_path)) as d:
        for name in ("flink", "dlink", "junc"):
            attempt(steps, f"stat {name}", lambda name=name: kind(d, name))
            attempt(steps, f"lstat {name}", lambda name=name: stat.S_ISLNK(os.lstat(tmp_path / name).st_mode))
    fact("links", mode=mode, steps=steps)


# --- the jobs online ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["hydrated", "cloud_only"])
def test_S0149_phase1_open_save_and_rebuild_online(mode: str, state: str, ws: Workspace) -> None:
    """Question 3: the open (the overlay load and the publish), the save (`ledger.save.run` through the protocol) and
    the rebuild (Beancount's own load) on a ledger file in the sync root, hydrated or cloud-only at the save and the
    rebuild, the provider serving; after the save, what is at the name (S0144's second re-probe: a plain file or still
    a placeholder)."""
    ws.op("open", ws.open)
    attempt(ws.steps, "prepare_save", ws.prepare_save)
    if state == "cloud_only":
        ws.provider("dehydrate")
        ws.steps.append({"step": "before save", "look": look(ws.main)})
    if ws.save_params is not None:
        ws.op("save", ws.save)
        attempt(ws.steps, "contents after save", ws.contents)
        if state == "cloud_only":
            ws.provider("dehydrate")
        ws.op("rebuild", ws.rebuild)
    attempt(ws.steps, "leftovers", ws.leftovers)
    fact("online", mode=mode, state=state, steps=ws.steps)


# --- offline --------------------------------------------------------------------------------------------------------


def test_S0149_phase1_the_open_of_a_cloud_only_ledger_offline(mode: str, ws: Workspace) -> None:
    """Question 4: the first open of a cloud-only ledger while the provider is not running."""
    ws.provider("disconnect")
    ws.op("open", ws.open)
    ws.provider("connect")
    attempt(ws.steps, "leftovers", ws.leftovers)
    fact("offline_open", mode=mode, steps=ws.steps)


@pytest.mark.parametrize("state", ["hydrated", "cloud_only"])
def test_S0149_phase1_the_save_offline(mode: str, state: str, ws: Workspace) -> None:
    """Question 4: a save while the provider is not running, the ledger file hydrated or cloud-only; then, online
    again, the file's bytes and what the save left."""
    ws.op("open", ws.open)
    attempt(ws.steps, "prepare_save", ws.prepare_save)
    if state == "cloud_only":
        ws.provider("dehydrate")
    ws.provider("disconnect")
    if ws.save_params is not None:
        ws.op("save", ws.save)
    ws.provider("connect")
    attempt(ws.steps, "contents after save", ws.contents)
    attempt(ws.steps, "leftovers", ws.leftovers)
    fact("offline_save", mode=mode, state=state, steps=ws.steps)


def test_S0149_phase1_the_rebuild_of_a_cloud_only_ledger_offline(mode: str, ws: Workspace) -> None:
    """Question 4: the rebuild after a landed save, the saved file made cloud-only (as OneDrive does after its
    upload) and the provider not running."""
    ws.op("open", ws.open)
    attempt(ws.steps, "prepare_save", ws.prepare_save)
    if ws.save_params is not None:
        ws.op("save", ws.save)
        ws.provider("dehydrate")
        ws.provider("disconnect")
        ws.op("rebuild", ws.rebuild)
        ws.provider("connect")
    attempt(ws.steps, "leftovers", ws.leftovers)
    fact("offline_rebuild", mode=mode, steps=ws.steps)
