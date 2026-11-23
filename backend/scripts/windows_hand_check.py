#!/usr/bin/env python3
"""The owner's hand check of Finzytrack's saves in a OneDrive folder on Windows (S0150; BL-0283, BL-0087).

usage (cwd = backend/; `scripts/windows_hand_check/hand_check.ps1` runs it):
    .venv/Scripts/python.exe -I scripts/windows_hand_check.py [--folder DIR] [--auto] [--packaged INSTALL_DIR]

It makes one scratch folder in OneDrive (`<OneDrive>/Finzytrack hand check <date and time>`; with `--folder`, the
given folder, which must exist and be empty) and one local scratch folder for the app's own files (the temp folder),
and writes nowhere else. It never deletes anything outside them, and removes only the local one at its end. In the
OneDrive folder it puts three small test ledgers, and runs the product's own jobs on them, as the app runs them: the
sidecar's scheduler (`EngineJobs`), the open job and the write use cases (`app.workspaces.writes`: preview, then
confirm), on the engine command the sidecar builds (`app.main.engine_command`, the engine of `../engine/.venv`), with
one change: the engine registers its test producer (`engine/tests/ledger/producer.py`), as `scripts/measure_write.py`
does, so a save writes only into the test ledger's folder (a test copy, the only place an unpromoted change may be
saved: `writes.promotion_refusal`).

Steps (each is recorded in the report; a step that fails is recorded with its error, and the next steps go on):
1. `mode`: the engine's placeholder mode as the sidecar starts it (`test.placeholder_mode`; ADR-0070: 1,
   `disguise_placeholder`), and with `--packaged` that of the installed app's own engine.
2. `setup`: the three ledgers, then up to 2 minutes for OneDrive to upload them (`looks.after_setup`).
3. `open`, `save`: the first ledger opened and one change saved; then for 2 minutes what OneDrive does to the saved
   file (`looks.after_save`: a plain file after our rename, a placeholder again, in sync).
4. `burst`: 8 saves one after another, so that some meet OneDrive's upload of the one before (an "in use" refusal
   is a fact, not a failure).
5. `free_up_space`: the included files and the plugin set to "free up space" (`attrib +U -P`, as File Explorer's
   command), then up to 1 minute for OneDrive to make them cloud-only; `online_open` opens the ledger again: Windows
   should download them.
6. With the owner's hands (skipped with `--auto`): `q1_unlisted_folder` (prepared online: a folder made on
   onedrive.com that this PC has not listed), then, offline, `offline` (the open and a confirmed save over cloud-only
   files), `q2_cloud_only_plugin` (a ledger whose plugin module is cloud-only) and `q1_unlisted_folder`'s open; then
   `paused` (OneDrive paused, online).

The report (JSON on standard output, and `report.json` in the OneDrive folder) names files relative to the OneDrive
scratch folder; any other absolute path is replaced by a label (`<scratch>`, `<app>`, `<home>`, `<onedrive>`,
`<repo>`), so it can be pasted into a session (INV-23). Prompts and progress go to standard error.

**Stated limits.** The looks read each file's attributes (`os.lstat` in the expose mode, so the cloud filter shows
the placeholder) and OneDrive's in-sync state (`CfGetPlaceholderInfo`); they do not say when an upload starts or ends.
The burst cannot choose to meet an upload: it reports what it met. Q1 depends on OneDrive not listing the new folder
before the open; the report says whether it was listed (its `RECALL_ON_OPEN` attribute) just before.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn, override

from app.main import engine_command, parse_args
from app.platform.adapters.app_folder import AppFolder, prepare
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import (
    ENGINE_RUN,
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    engine_env,
    packaged_engine_site,
    venv_python,
    venv_site,
)
from app.platform.adapters.engine_lock import wait_engine_lock
from app.platform.clock import FrozenClock
from app.platform.engine import EngineEnd
from app.platform.engine_jobs import save_text
from app.platform.engine_jobs.opening import Opened, submit_open
from app.platform.engine_jobs.opening import OpenTarget as OpenJobTarget
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket, Outcome, Work
from app.platform.engine_supervision import RestartPolicy
from app.platform.plans import PlanStore
from app.workspaces import writes
from app.workspaces.writes import CopyMark, Previewed, WriteServices, WriteTarget, mirror_generation

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ENGINE = REPO / "engine"
ENGINE_VENV = ENGINE / ".venv"
VERSION = "0.0.0-hand-check"
MUTATOR = "test.edits"  # `engine/tests/ledger/producer.TEST_MUTATOR`
CLOCK = FrozenClock(datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
DEADLINE = 300.0  # seconds a job may take before the step records it as stuck (not a budget)
BURST = 8
PLUGIN = "ft_hand_check_plugin"

WITH_LAZY_TEST_PRODUCER = (
    f"import sys; sys.path.append({str(ENGINE)!r}); "
    "from ledger_engine.ledger import preview; "
    f"preview.PRODUCERS[{MUTATOR!r}] = lambda s, r: "
    "__import__('tests.ledger.producer', fromlist=['produce_json']).produce_json(s, r); " + ENGINE_RUN
)

MAIN = (
    'option "operating_currency" "USD"\n'
    'include "accounts.bean"\n'
    'include "sub/more.bean"\n'
    '2026-01-02 * "Hand check"\n  Expenses:Food  1.00 USD\n  Assets:Cash\n'
)
ACCOUNTS = "2026-01-01 open Assets:Cash USD\n2026-01-01 open Expenses:Food USD\n"
MORE = '2026-01-03 note Assets:Cash "an included file in a sub-folder"\n'
PLUGIN_MAIN = (
    'option "operating_currency" "USD"\noption "insert_pythonpath" "TRUE"\n'
    f'plugin "{PLUGIN}"\n2026-01-01 open Assets:Cash USD\n'
)
PLUGIN_CODE = "__plugins__ = ('run',)\n\n\ndef run(entries, options):\n    return entries, []\n"
Q1_MAIN = 'option "operating_currency" "USD"\ninclude "../web/web.txt"\n2026-01-01 open Assets:Cash USD\n'
Q1_TEXT = '2026-01-04 note Assets:Cash "made on onedrive.com"'

ATTRS: Final = (
    (0x1, "READONLY"),
    (0x10, "DIRECTORY"),
    (0x400, "REPARSE_POINT"),
    (0x1000, "OFFLINE"),
    (0x40000, "RECALL_ON_OPEN"),
    (0x80000, "PINNED"),
    (0x100000, "UNPINNED"),
    (0x400000, "RECALL_ON_DATA_ACCESS"),
)
IN_SYNC: Final = {0: "not_in_sync", 1: "in_sync"}


class Stop(Exception):
    """A step that cannot go on: recorded with its reason, then the next step runs."""


def fail(what: str) -> NoReturn:
    raise Stop(what)


def say(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


# -- privacy: no absolute path in the report (INV-23) ----------------------------------------------------------------


class Cleaner:
    """Replaces each known absolute path, in every spelling it can take in a text (as is, `repr`'s and JSON's doubled
    backslashes, forward slashes), by its label. Longest first, so `<scratch>` wins over `<onedrive>` and `<home>`."""

    def __init__(self) -> None:
        self.labels: list[tuple[str, str]] = []

    def add(self, path: Path | str | None, label: str) -> None:
        if not path:
            return
        for p in {str(path), os.path.realpath(path)}:
            if len(p) > 3:
                self.labels.append((p, label))
        self.labels.sort(key=lambda pl: -len(pl[0]))

    def __call__(self, text: str) -> str:
        for path, label in self.labels:
            for spelling in (path.replace("\\", "\\\\\\\\"), path.replace("\\", "\\\\"), path, path.replace("\\", "/")):
                text = text.replace(spelling, label)
        return text


# -- looks: what OneDrive did to a file -------------------------------------------------------------------------------


def _set_mode(mode: int) -> int | None:
    """Windows only: this process's placeholder mode set to `mode`; the previous one, or None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes  # Windows only

    nt = ctypes.WinDLL("ntdll")
    query = nt.RtlQueryProcessPlaceholderCompatibilityMode
    query.restype = ctypes.c_byte
    setter = nt.RtlSetProcessPlaceholderCompatibilityMode
    setter.argtypes = [ctypes.c_byte]
    setter.restype = ctypes.c_byte
    previous = int(query())
    setter(mode)
    return previous


def _in_sync(path: Path) -> str:
    """OneDrive's in-sync state of a placeholder (`CfGetPlaceholderInfo`, the basic class), never reading its data."""
    if sys.platform != "win32":
        return "not on Windows"
    import ctypes  # Windows only
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    read_attributes, share_all, open_existing = 0x80, 0x7, 3
    backup_semantics, open_reparse_point = 0x02000000, 0x00200000
    h = k32.CreateFileW(
        str(path), read_attributes, share_all, None, open_existing, backup_semantics | open_reparse_point, None
    )
    if h in (None, wintypes.HANDLE(-1).value):
        return f"open refused ({ctypes.get_last_error()})"
    try:
        cf = ctypes.WinDLL("cldapi")
        get = cf.CfGetPlaceholderInfo
        get.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get.restype = ctypes.c_long
        buf = ctypes.create_string_buffer(4096)
        got = wintypes.DWORD()
        hr = int(get(h, 0, buf, len(buf), ctypes.byref(got))) & 0xFFFFFFFF
        if hr == 0x80070178:  # HRESULT of ERROR_NOT_A_CLOUD_FILE
            return "not_a_placeholder"
        if hr not in (0, 0x800700EA):  # S_OK, or ERROR_MORE_DATA with the fixed fields filled
            return f"error {hr:#010x}"
        pin, sync = ctypes.c_int.from_buffer(buf, 0).value, ctypes.c_int.from_buffer(buf, 4).value
        return f"{IN_SYNC.get(sync, sync)}, pin {pin}"
    finally:
        k32.CloseHandle(h)


def look(path: Path, root: Path) -> dict[str, Any]:
    """One file as File Explorer sees it: its attributes in the expose mode (the cloud filter shows the placeholder)
    and its in-sync state; on macOS and Linux only whether it is there and its size."""
    out: dict[str, Any] = {"file": path.relative_to(root).as_posix()}
    previous = None
    try:
        previous = _set_mode(2)  # PHCM_EXPOSE_PLACEHOLDERS: for this look only
        st = os.lstat(path)
    except OSError as e:
        out["error"] = f"{type(e).__name__}: {e.strerror} ({getattr(e, 'winerror', None) or e.errno})"
        return out
    finally:
        if previous is not None:
            _set_mode(previous)
    out["size"] = st.st_size
    if sys.platform == "win32":
        attrs = st.st_file_attributes
        out["attrs"] = [n for bit, n in ATTRS if attrs & bit]
        out["kind"] = "cloud_only" if attrs & 0x400000 else "placeholder_on_this_pc" if attrs & 0x400 else "plain_file"
        try:
            out["sync"] = _in_sync(path)
        except OSError as e:
            out["sync"] = f"{type(e).__name__}: {e}"
    return out


def watch(files: list[Path], root: Path, seconds: float, until: Callable[[list[dict[str, Any]]], bool]) -> list[Any]:
    """Each file's look every 2 s for up to `seconds`, a sample kept each time a look changes; ends early when `until`
    holds for the latest looks."""
    samples: list[Any] = []
    last: list[dict[str, Any]] | None = None
    start = time.monotonic()
    while True:
        now = [look(f, root) for f in files]
        if now != last:
            samples.append({"t": round(time.monotonic() - start, 1), "looks": now})
            last = now
        if until(now) or time.monotonic() - start >= seconds:
            return samples
        threading.Event().wait(2.0)


def _all(kind: str) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda looks: os.name != "nt" or all(lk.get("kind") == kind for lk in looks)


def _synced(looks: list[dict[str, Any]]) -> bool:
    return os.name != "nt" or all(str(lk.get("sync", "")).startswith("in_sync") for lk in looks)


def free_up_space(files: list[Path], root: Path) -> list[dict[str, Any]]:
    """File Explorer's "Free up space" on each file (`attrib +U -P`): OneDrive then makes it cloud-only. Off Windows
    and outside a OneDrive folder it changes nothing that matters; its answer is recorded."""
    out: list[dict[str, Any]] = []
    for f in files:
        if os.name != "nt":
            out.append({"file": f.relative_to(root).as_posix(), "attrib": "not on Windows"})
            continue
        attrib = Path(os.environ.get("SYSTEMROOT", "C:\\Windows")) / "System32" / "attrib.exe"
        done = subprocess.run(  # noqa: S603 (Windows' own attrib on our own file)
            [str(attrib), "+U", "-P", str(f)], capture_output=True, text=True, check=False, timeout=60
        )
        out.append({"file": f.relative_to(root).as_posix(), "attrib": done.returncode, "said": done.stdout.strip()})
    return out


# -- the product's jobs -----------------------------------------------------------------------------------------------


class Jobs(EngineJobs):
    """The product's scheduler; every ticket kept by its kind, so the rebuild a landed save queues can be waited for."""

    tickets: list[tuple[str, JobTicket]]

    @override
    def submit(self, spec: JobSpec, work: Work) -> JobTicket | Failed:
        out = super().submit(spec, work)
        if isinstance(out, JobTicket):
            self.tickets.append((spec.kind, out))
        return out


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"plan-{self.n}"


def recorded_mark(folder: Path) -> CopyMark:
    """The test-copy mark as the copy job records it (`scripts/measure_write.py`'s): the folder's real path and its
    identity, read by the engine's own port in the engine's interpreter, its mode pinned as an engine's start does."""
    path = os.path.realpath(folder)
    code = (
        "import json, sys; from ledger_engine.fs.this_os import for_this_os, pin_placeholder_mode; "
        "assert pin_placeholder_mode(); "
        "m = for_this_os().open_dir(sys.argv[-1]).mark(); "
        "print(json.dumps([m.volume, m.fs_type, m.born]))"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        [*bootstrap_argv(venv_python(ENGINE_VENV), venv_site(ENGINE_VENV), code), path],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env=engine_env(os.environ),
    )
    volume, fs_type, born = json.loads(out.stdout)
    return CopyMark(path, str(volume), str(fs_type), int(born))


def waited(ticket: JobTicket | Failed | NotDone, what: str) -> Outcome:
    if not isinstance(ticket, JobTicket):
        fail(f"{what}: not queued: {ticket!r}")
    out = ticket.wait(DEADLINE)
    if out is None:
        fail(f"{what}: no answer in {DEADLINE:g} s")
    return out


def told(value: object) -> dict[str, Any]:
    """An outcome as the report records it: its kind, and for a refusal the user's text and its details."""
    if isinstance(value, Done):
        value = value.value
    if isinstance(value, Saved):
        return {"outcome": "saved"}
    if isinstance(value, NotDone | Failed):
        return {
            "outcome": "not saved",
            "code": str(value.error.code),
            "text": save_text.text(value.error),
            "details": repr(value.error.details),
        }
    if isinstance(value, Opened):
        return {"outcome": "opened" if value.mirror_ready else "opened, views not ready", "publish": value.publish}
    return {"outcome": type(value).__name__, "details": repr(value)}


class Product:
    """The scheduler over the product's engine command (with the lazy test producer), as `measure_write.py` wires it;
    one open workspace at a time."""

    def __init__(self, app: AppFolder, cache: Path) -> None:
        self.app = app
        self.cache = cache
        sidecar = parse_args(
            ["--app-local-data", str(app.path), "--app-cache", str(cache), "--engine-venv", str(ENGINE_VENV)]
        )
        product = engine_command(sidecar, app, VERSION)
        python, site = venv_python(ENGINE_VENV), venv_site(ENGINE_VENV)
        if product.argv != bootstrap_argv(python, site, ENGINE_RUN):
            fail(f"the product's engine command is not the one this script changes: {product.argv}")
        command = dataclasses.replace(product, argv=bootstrap_argv(python, site, WITH_LAZY_TEST_PRODUCER))
        restart = RestartPolicy()
        holder: list[Jobs] = []

        def spawned(serial: int) -> None:
            holder[0].engine_spawned(serial)

        def ended(end: EngineEnd) -> None:
            holder[0].engine_ended(end)

        self.client = EngineClient(command, restart, on_engine_spawned=spawned, on_engine_ended=ended)
        self.jobs = Jobs(self.client, wait_engine_lock=lambda: wait_engine_lock(app), restart=restart)
        holder.append(self.jobs)
        self.jobs.tickets = []
        boot = self.jobs.start()
        if not isinstance(boot, JobTicket) or boot.wait(DEADLINE) is None:
            fail(f"the scheduler did not start: {boot!r}")
        self.services = WriteServices(jobs=self.jobs, plans=PlanStore(CLOCK), clock=CLOCK, ids=_Ids(), session="h1")
        self.marks: dict[Path, CopyMark] = {}

    def close(self) -> None:
        self.jobs.close()
        self.client.close()

    def open(self, key: str, root: Path) -> tuple[dict[str, Any], WriteTarget | None]:
        self.jobs.exclusive_began()
        try:
            opened = waited(
                submit_open(self.jobs, key, OpenJobTarget(key, str(root), "lin-" + key, self.app.path), clock=CLOCK),
                "open",
            )
        finally:
            self.jobs.exclusive_ended()
        result = told(opened)
        if not (isinstance(opened, Done) and isinstance(opened.value, Opened)):
            return result, None
        self.jobs.workspace_opened(key)
        folder = root.parent
        if folder not in self.marks:
            self.marks[folder] = recorded_mark(folder)
        return result, WriteTarget(key, str(root), "lin-" + key, self.app.path, self.cache, copy=self.marks[folder])

    def preview(self, target: WriteTarget, line: str) -> Previewed | dict[str, Any]:
        generation = mirror_generation(target)
        if generation is None:
            return {"outcome": "no generation published"}
        request = {"edits": [{"kind": "insert", "text": line, "default_file": target.root}]}
        out = waited(writes.submit_preview(self.services, target, generation, MUTATOR, request), "preview")
        if isinstance(out, Done) and isinstance(out.value, Previewed):
            return out.value
        return {"outcome": "preview refused", "details": repr(out)}

    def confirm(self, target: WriteTarget, plan: Previewed) -> dict[str, Any]:
        self.jobs.tickets = []
        out = writes.submit_confirm(self.services, target, plan.plan_id, plan.preview_hash, acknowledged=frozenset())
        if isinstance(out, NotDone):
            return told(out)
        result = told(waited(out, "save"))
        if result["outcome"] == "saved":
            rebuilds = [t for kind, t in self.jobs.tickets if kind == "rebuild"]
            result["rebuild"] = told(waited(rebuilds[0], "rebuild")) if rebuilds else "none queued"
            on_disk = Path(target.root).read_text(encoding="utf-8")
            result["text_on_disk"] = "the plan's text" if on_disk == plan.files[0].text else "another text"
        return result

    def save(self, target: WriteTarget, line: str) -> dict[str, Any]:
        plan = self.preview(target, line)
        if not isinstance(plan, Previewed):
            return plan
        return self.confirm(target, plan)


def mode_of(command: EngineCommand) -> object:
    """The placeholder mode of an engine started by `command` (`test.placeholder_mode`; None off Windows)."""
    c = EngineClient(command)
    try:
        out = c.call("test.placeholder_mode", {})
    finally:
        c.close()
    return out.get("mode") if isinstance(out, dict) else repr(out)


# -- the run ----------------------------------------------------------------------------------------------------------


def ask(auto: bool, text: str) -> bool:
    """The owner's hands: `text` shown, then Enter to go on or `s` and Enter to skip. Never asked with `--auto`."""
    if auto:
        return False
    say("\n" + text + "\n  Press Enter when done, or type s and Enter to skip this step.")
    try:
        return input().strip().lower() != "s"
    except EOFError:
        return False


def step(report: dict[str, Any], name: str, fn: Callable[[], object]) -> None:
    say(f"-- {name}")
    try:
        report[name] = fn()
    except Exception as e:  # noqa: BLE001 (a step's failure is a fact of the report; the next step runs)
        report[name] = {"failed": f"{type(e).__name__}: {e}"}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def run(scratch: Path, auto: bool, packaged: Path | None, clean: Cleaner) -> dict[str, Any]:
    report: dict[str, Any] = {"platform": sys.platform, "python": sys.version.split()[0]}
    if sys.platform == "win32":
        report["windows_build"] = sys.getwindowsversion().build
    local = Path(tempfile.mkdtemp(prefix="ft-hand-check-")).resolve()
    clean.add(local, "<app>")
    looks: dict[str, Any] = {}
    report["looks"] = looks
    product: Product | None = None
    try:
        a = scratch / "ledger"
        main = write(a / "main.bean", MAIN)
        included = [write(a / "accounts.bean", ACCOUNTS), write(a / "sub" / "more.bean", MORE)]
        b = scratch / "plugin"
        plugin_main = write(b / "main.bean", PLUGIN_MAIN)
        plugin = write(b / f"{PLUGIN}.py", PLUGIN_CODE)
        c = scratch / "q1"
        q1_main = write(c / "main.bean", Q1_MAIN)
        files = [main, *included, plugin_main, plugin, q1_main]

        def modes() -> dict[str, Any]:
            got: dict[str, Any] = {"engine": mode_of(EngineCommand.for_venv(ENGINE_VENV, test_ops=True))}
            if packaged is None:
                got["packaged"] = "not asked (no --packaged)"
            else:
                python = packaged / "sidecar" / "python" / ("python.exe" if os.name == "nt" else "bin/python3.13")
                site = packaged_engine_site(python.parent if os.name == "nt" else python.parents[1])
                got["packaged"] = mode_of(EngineCommand.for_packaged(python, site, test_ops=True))
            return got

        step(report, "mode", modes)
        say("-- setup: waiting up to 2 minutes for OneDrive to upload the test ledgers")
        looks["after_setup"] = watch(files, scratch, 0 if auto else 120, _synced)
        report["setup"] = {"files": [f.relative_to(scratch).as_posix() for f in files]}
        app = prepare(local / "app")
        if not isinstance(app, AppFolder):
            fail(f"the app folder: {app!r}")
        cache = local / "cache"
        cache.mkdir()
        product = Product(app, cache)
        p = product
        targets: dict[str, WriteTarget] = {}

        def open_a() -> object:
            result, target = p.open("ka", main)
            if target is not None:
                targets["a"] = target
            return result

        step(report, "open", open_a)

        def save_once() -> object:
            if "a" not in targets:
                fail("the open did not succeed")
            return p.save(targets["a"], '2026-01-05 note Assets:Cash "saved by the hand check"\n')

        step(report, "save", save_once)
        say("-- watching what OneDrive does to the saved file (up to 2 minutes)")
        looks["after_save"] = watch([main], scratch, 0 if auto else 120, _synced)

        def burst() -> object:
            if "a" not in targets:
                fail("the open did not succeed")
            saves = [p.save(targets["a"], f'2026-01-06 note Assets:Cash "burst {n}"\n') for n in range(BURST)]
            return {"count": BURST, "saves": saves}

        step(report, "burst", burst)

        def online() -> object:
            freed = free_up_space([*included, plugin], scratch)
            looks["after_free_up_space"] = watch([*included, plugin], scratch, 0 if auto else 60, _all("cloud_only"))
            result, target = p.open("ka", main)
            if target is not None:
                targets["a"] = target
            return {"free_up_space": freed, "open": result, "looks_after": [look(f, scratch) for f in included]}

        step(report, "free_up_space", online)
        if auto:
            for name in ("offline", "paused", "q1_unlisted_folder", "q2_cloud_only_plugin"):
                report[name] = "skipped (--auto)"
            return report
        by_hand(report, p, targets, scratch, main, included, plugin, plugin_main, q1_main, looks)
        return report
    finally:
        if product is not None:
            product.close()
        shutil.rmtree(local, ignore_errors=True)


def by_hand(
    report: dict[str, Any],
    p: Product,
    targets: dict[str, WriteTarget],
    scratch: Path,
    main: Path,
    included: list[Path],
    plugin: Path,
    plugin_main: Path,
    q1_main: Path,
    looks: dict[str, Any],
) -> None:
    """The steps that need the owner's hands, in the order that needs the fewest switches of the network."""
    web = scratch / "web"
    q1 = ask(
        False,
        f'Q1. In a web browser, open onedrive.com and go to the folder "{scratch.name}". Make a new folder named '
        '"web" in it. In "web", choose New, then Plain text document, name it "web" (it is saved as web.txt), and '
        f"type this one line into it:\n  {Q1_TEXT}\n"
        f'  Then open "{scratch.name}" in File Explorer and wait until "web" appears there. Do not open "web".',
    )
    plan: Previewed | dict[str, Any] | None = None
    if "a" in targets:
        plan = p.preview(targets["a"], '2026-01-07 note Assets:Cash "confirmed offline"\n')  # planned online
    freed = free_up_space([*included, plugin], scratch)
    looks["before_offline"] = watch([*included, plugin], scratch, 60, _all("cloud_only"))
    looks["q1_web_before_offline"] = look(web, scratch) if q1 else "skipped"
    if not ask(False, "OFFLINE. Turn on airplane mode (or unplug the network cable)."):
        for name in ("offline", "q2_cloud_only_plugin"):
            report[name] = "skipped by the owner"
        report["q1_unlisted_folder"] = "skipped by the owner"
    else:

        def offline() -> object:
            out: dict[str, Any] = {"free_up_space": freed}
            out["confirm_planned_online"] = (
                p.confirm(targets["a"], plan) if isinstance(plan, Previewed) else {"no plan": repr(plan)}
            )
            out["open"] = p.open("ka", main)[0]
            out["looks_after"] = [look(f, scratch) for f in [main, *included]]
            return out

        step(report, "offline", offline)
        step(
            report,
            "q2_cloud_only_plugin",
            lambda: {"open": p.open("kb", plugin_main)[0], "plugin": look(plugin, scratch)},
        )
        if q1:
            step(report, "q1_unlisted_folder", lambda: {"open": p.open("kc", q1_main)[0], "web": look(web, scratch)})
        else:
            report["q1_unlisted_folder"] = "skipped by the owner"
        ask(False, "Turn airplane mode off again (or plug the cable back in), and wait until you are online.")
    if ask(False, "PAUSED. Pause OneDrive: its icon in the taskbar, then Help & Settings, Pause syncing, 2 hours."):
        free_up_space(included, scratch)
        looks["before_paused"] = watch(included, scratch, 60, _all("cloud_only"))
        step(
            report,
            "paused",
            lambda: {"open": p.open("ka", main)[0], "looks_after": [look(f, scratch) for f in included]},
        )
        ask(False, "Resume OneDrive: its icon, then Help & Settings, Resume syncing.")
    else:
        report["paused"] = "skipped by the owner"


def scratch_folder(given: Path | None) -> Path | None:
    """The given folder (checked empty by the caller), or a new folder in OneDrive; None when there is no OneDrive."""
    if given is not None:
        return given.resolve()
    onedrive = os.environ.get("ONEDRIVE")  # Windows: names are case-blind
    if os.name != "nt" or not onedrive or not Path(onedrive).is_dir():
        say("This check runs on Windows with OneDrive signed in (the OneDrive folder was not found).")
        return None
    folder = Path(onedrive) / f"Finzytrack hand check {SystemClock().now().strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir()  # exclusive: never an existing folder
    return folder.resolve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--folder", type=Path, help="an existing, empty folder to use instead of a new one in OneDrive")
    ap.add_argument("--auto", action="store_true", help="no prompts: the steps that need the owner are skipped")
    ap.add_argument("--packaged", type=Path, help="the installed Finzytrack folder, to ask its engine's mode")
    args = ap.parse_args(argv)
    if args.folder is not None and (not args.folder.is_dir() or any(args.folder.iterdir())):
        say("--folder must be an existing, empty folder.")
        return 2
    scratch = scratch_folder(args.folder)
    if scratch is None:
        return 2
    clean = Cleaner()
    clean.add(scratch, "<scratch>")
    clean.add(os.environ.get("ONEDRIVE"), "<onedrive>")
    clean.add(REPO, "<repo>")
    clean.add(args.packaged, "<install>")
    clean.add(Path.home(), "<home>")
    clean.add(tempfile.gettempdir(), "<temp>")
    say(f"Scratch folder: {scratch.name} (in OneDrive unless --folder was given).")
    report = run(scratch, args.auto, args.packaged, clean)
    text = clean(json.dumps(report, indent=2, ensure_ascii=True, default=str))
    (scratch / "report.json").write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    say(f"\nDone. The report above is also in {scratch.name}/report.json. Paste it back into the session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
