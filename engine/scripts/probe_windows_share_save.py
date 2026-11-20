"""Probe the engine's whole save on a Windows network share (BL-0219's Windows part; owner, 2026-11-19: "For bl-0219,
I think we need to ensure that Finzytrack works for ledgers on windows shares, possibly with reduced guarantees").
Prints only, one JSON line per step; it changes no product code and decides nothing.

Commands (run from `engine/`, on Windows only):

- `probe <folder> [<folder> ...]`: for each folder (the CI share's spellings and, for comparison, a local NTFS
  folder), in a scratch folder `.ft-share-save-<pid>-<index>` it makes there:
  1. `save`: a small ledger, then the engine's real write path on it, as `tests/ledger/test_save.py` runs it on a
     real disk (the Windows adapter selected by the tests' switch, `windows_port_under_test`): the open's publish (the
     app's cache, local), the preview's load, the save (`ledger.save.run`: its gate, the journal and backups in the
     app's machine-local folder, local; each create, write, flush and rename in the ledger folder), the rebuild, a
     second save, a third save with a failure injected after its rename (the rollback), recover-all over a journal
     left `.open`, and the outcome as the protocol carries it. The adapter's `volume_id` answers None on every remote
     volume, so the save would refuse `no_drive_id` before writing: **in this process only** it is patched to give a
     share the drive ID an NTFS volume gets (its `FileIdInfo` serial), never in product code. Two variants:
     `as_built` (only that patch) and `share_sync_tolerated` (also a ledger folder's flush refused on a remote volume,
     `ERROR_INVALID_FUNCTION`, taken as done, to show what the rest of the save does there: a reduced guarantee).
  2. `exclusive`: the adapter's exclusive create (`FILE_CREATE` relative to the folder handle, as `WindowsDir.create`)
     and the by-path forms at a name that is taken, in every way found for a name to share a file (the same name, its
     other case, a file another handle holds, one whose deletion is pending, a folder, a second handle on the
     folder), with the existing file's bytes and identity before and after; the same for `mkdir`; and run 4's
     "ok (unexpected)" sequence again, printing whether its name existed before the create.
  3. `replace`: the adapter's own replace (`WindowsDir.rename`: `FileRenameInformation` with `ReplaceIfExists`,
     relative to the folder handle; ADR-0069) over an existing file, alone, under a reader sharing deletion, under a
     reader that does not, and 300 times while another thread looks for the target; and the exclusive rename over it.
- `identity <label> <root> [<root> ...]`: for the folder `.ft-identity-probe` under each root (made under the first
  root if missing; each root a spelling of the same place), `FileIdInfo`, the 32-bit serial, the creation time, the
  final paths, and what the engine's identity rule would compare (`volume_id`, the folder's mark, `drive_of`,
  `known_birth`) if `volume_id` used the share's serial. The CI step runs it before and after remapping the drive
  and after restarting the server service.
- `identity-remove <root>`: removes that folder (the last step).

It writes only the scratch folders it makes on the share and in the runner's temp folder, and removes them.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Generator
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

if sys.platform != "win32":
    raise SystemExit("a Windows probe")  # noqa: TRY003 (one raise site)

# pyright: reportPrivateUsage=false
# (a probe reads the adapter's own handles and calls, as `measure_save.py` reads the save's)

import uuid

from ledger_engine.fs import port, under_test, windows
from ledger_engine.fs.port import DirHandle, FileSystem, Resolved
from ledger_engine.fs.this_os import for_this_os
from ledger_engine.ledger import handles, opening, overlay, publishing, rebuild, recover_all, restore
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Fingerprint, as_before
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import Change, sha256
from ledger_engine.mirror import publish as pb
from ledger_engine.protocol import save as protocol_save

MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b"2026-01-01 open Expenses:Food\n"
KEY = "k1"
LINEAGE = "lin1"
SESSION = "p1"
TODAY = date(2026, 11, 20)
BUILT_AT = "2026-11-20T10:00:00+00:00"
IDENTITY_FOLDER = ".ft-identity-probe"
ERROR_INVALID_FUNCTION = 1
CREATE_NEW = 1
OPEN_EXISTING = 3
EFFECTS = frozenset(
    {
        "create",
        "rename",
        "rename_excl",
        "rename_shared",
        "remove",
        "mkdir",
        "rmdir",
        "sync",
        "sync_file",
        "touch",
        "swap",
        "clear_readonly",
    }
)
"""The calls that change or flush something: each printed with its outcome."""
ASKED = frozenset({"mark", "volume_id", "caps", "remote", "fs_type", "born", "identity", "rule", "acl", "path"})
"""The questions about a folder: printed the first time each handle label asks them, and on any error."""


def say(part: str, **fields: Any) -> None:
    sys.stdout.write(json.dumps({"part": part, **fields}, default=_plain) + "\n")
    sys.stdout.flush()


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {"type": type(value).__name__, **asdict(value)}
    if isinstance(value, bytes):
        return value.decode("utf-8", "backslashreplace")
    return str(value)


def failure(e: BaseException) -> dict[str, Any]:
    """An error as the probe prints it: its type, `errno`, Windows' code and text."""
    if isinstance(e, OSError):
        return {"error": type(e).__name__, "errno": e.errno, "winerror": e.winerror, "text": str(e)}
    return {"error": type(e).__name__, "text": str(e)}


def attempt(fn: Callable[[], object]) -> object:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 (a probe reports every failure and goes on)
        return failure(e)


# -- the drive ID a share would get --------------------------------------------------------------------------------

ORIGINAL_VOLUME_ID = windows.WindowsDir.volume_id


def share_volume_id(d: windows.WindowsDir) -> str | None:
    """`WindowsDir.volume_id` without its remote refusal: on a remote volume the name an NTFS or ReFS volume gets from
    its `FileIdInfo` serial (or a FAT volume from its 32-bit serial), as the adapter builds it for a local one."""
    if not d.remote():
        return ORIGINAL_VOLUME_ID(d)
    fs_type, serial32 = d._h.volume()
    if fs_type in windows.FS_SERIAL64:
        serial = d.identity()[0]
        name = f"{serial:016X}"
    elif fs_type in windows.FS_SERIAL32:
        serial = serial32
        name = f"{serial >> 16:04X}-{serial & 0xFFFF:04X}"
    else:
        return None
    return None if serial == 0 else str(uuid.uuid5(port.SERIAL_NAMESPACE, name))


@contextlib.contextmanager
def drive_id_on_shares() -> Generator[None]:
    with mock.patch.object(windows.WindowsDir, "volume_id", share_volume_id):
        yield


# -- the traced port ---------------------------------------------------------------------------------------------------


@dataclass
class Trace:
    """What the traced port prints and injects. `roots`: each label's folders, as given and as the adapter names them
    (a mapped drive's handle gives the share's UNC path, a short name its long form): a handle under the scratch
    ledger folder is `ledger`, under the app's scratch folder `app`, else `other`. `tolerate_share_sync`: a ledger
    folder's flush refused on a remote volume (`ERROR_INVALID_FUNCTION`) is printed and taken as done.
    `fail_sync_after_rename`: the first ledger folder flush after a ledger rename raises `EIO` (the rollback's injected
    failure)."""

    roots: list[tuple[str, str]]
    step: str = ""
    tolerate_share_sync: bool = False
    fail_sync_after_rename: bool = False
    renamed: bool = False
    asked: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    first_failure: dict[str, Any] | None = None

    def label(self, path: str) -> str:
        p = path.casefold()
        for name, root in self.roots:
            if p.startswith(root.casefold()):
                return name
        return "other"

    def call(
        self, label: str, remote: Callable[[], bool], name: str, args: tuple[Any, ...], fn: Callable[..., Any]
    ) -> Any:
        shown = [a if isinstance(a, str) else f"<{len(a)} bytes>" if isinstance(a, bytes) else "<data>" for a in args]
        if name == "sync" and label == "ledger" and self.fail_sync_after_rename and self.renamed:
            self.fail_sync_after_rename = False
            e = OSError(errno.EIO, "injected by the probe after the rename")
            say("effect", step=self.step, on=label, call=name, args=shown, outcome="injected", **failure(e))
            raise e
        try:
            out = fn(*args)
        except Exception as e:
            if (
                name == "sync"
                and label == "ledger"
                and self.tolerate_share_sync
                and isinstance(e, OSError)
                and e.winerror == ERROR_INVALID_FUNCTION
                and remote()
            ):
                say("effect", step=self.step, on=label, call=name, outcome="refused, tolerated", **failure(e))
                return None
            if self.first_failure is None and not isinstance(e, FileNotFoundError):  # a missing name is an answer
                self.first_failure = {"step": self.step, "on": label, "call": name, "args": shown, **failure(e)}
            if name in EFFECTS or name in ASKED or not isinstance(e, FileNotFoundError):
                say("effect", step=self.step, on=label, call=name, args=shown, outcome="raised", **failure(e))
            raise
        if name in ("rename", "rename_excl") and label == "ledger":
            self.renamed = True
        if name in EFFECTS:
            say("effect", step=self.step, on=label, call=name, args=shown, outcome="ok", result=out)
        elif name in ASKED and (label, name) not in self.asked:
            self.asked.add((label, name))
            say("asked", step=self.step, on=label, call=name, result=out)
        return out


class TracedDir:
    """A folder handle that prints each effect and the first answer to each question, and returns exactly what the
    real handle returns; a child folder opened through it is traced too."""

    def __init__(self, inner: DirHandle, label: str, trace: Trace) -> None:
        self._inner = inner
        self._label = label
        self._trace = trace

    def _remote(self) -> bool:
        return bool(self._inner.remote())

    def open_child(self, name: str) -> TracedDir:
        child = self._trace.call(self._label, self._remote, "open_child", (name,), self._inner.open_child)
        return TracedDir(child, self._label, self._trace)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or name in ("close", "__enter__", "__exit__"):
            return attr

        def call(*args: Any) -> Any:
            return self._trace.call(self._label, self._remote, name, args, attr)

        return call

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> TracedDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()


class TracedFS:
    def __init__(self, inner: FileSystem, trace: Trace) -> None:
        self._inner = inner
        self._trace = trace

    def resolve(self, path: str) -> Resolved:
        return self._inner.resolve(path)

    def same_path(self, now: str, recorded: str) -> bool:
        return self._inner.same_path(now, recorded)

    def open_dir(self, path: str) -> DirHandle:
        label = self._trace.label(path)
        try:
            inner = self._inner.open_dir(path)
        except OSError as e:
            say("effect", step=self._trace.step, on=label, call="open_dir", args=[path], outcome="raised", **failure(e))
            raise
        return TracedDir(inner, label, self._trace)  # type: ignore[return-value]


class Job:
    def __init__(self) -> None:
        self.events: list[str] = []

    def progress(self, phase: str) -> None:
        self.events.append(phase)

    def gate(self) -> None:
        self.events.append("gate")

    def committed(self) -> None:
        self.events.append("committed")


# -- part 1: the whole save ----------------------------------------------------------------------------------------


def fs_now(folder: str) -> int:
    """Now, as the file system stamps a file (the engine reads no clock: INV-07)."""
    p = os.path.join(folder, ".now")
    with open(p, "wb"):
        pass
    try:
        return os.stat(p).st_mtime_ns
    finally:
        os.remove(p)


def target(cache: str, ald: str) -> publishing.Target:
    return publishing.Target(
        app_cache=cache,
        workspace_key=KEY,
        app_local_data=ald,
        lineage=LINEAGE,
        floor=0,
        today=TODAY,
        built_at=BUILT_AT,
        now_ns=fs_now(cache),
        engine_build="probe",
    )


def listing(folder: str) -> dict[str, list[str]]:
    """The journal and backups folders of the workspace, as the app folder holds them."""
    out: dict[str, list[str]] = {}
    for sub in ("journal", "backups"):
        p = os.path.join(folder, "local", KEY, sub)
        found: list[str] = []
        if os.path.isdir(p):
            for d, _, files in os.walk(p):
                found.extend(os.path.relpath(os.path.join(d, f), p) for f in files)
        out[sub] = sorted(found)
    return out


@dataclass
class Run:
    variant: str
    folder: str
    root: str
    ald: str
    cache: str
    trace: Trace
    policy: DesktopPolicy = field(default_factory=DesktopPolicy)
    n: int = 0

    def files(self) -> dict[str, object]:
        return {
            name: attempt(lambda name=name: Path(self.folder, name).read_bytes().decode())
            for name in sorted(os.listdir(self.folder))
        }


def plan(r: Run, post: bytes) -> tuple[sv.SavePlan, Fingerprint, bytes]:
    pre = Path(r.root).read_bytes()
    planned = overlay.load(r.root, {r.root: post}, r.policy)  # the preview's load of the candidate
    if not isinstance(planned, overlay.Loaded):
        raise TypeError(f"the preview's load refused: {planned}")  # noqa: TRY003 (a probe's own stop)
    fp = as_before(planned.fingerprint, {r.root: sha256(pre)})
    with for_this_os().open_dir(r.ald) as d:
        app_folder = port.folder_id(d)
    r.n += 1
    return (
        sv.SavePlan(
            session=SESSION,
            job=f"j{r.n}",
            workspace_key=KEY,
            lineage=LINEAGE,
            engine_build="probe",
            root=r.root,
            changes=(Change(r.root, sha256(pre), post),),
            fingerprint=fp,
            app_cache=r.cache,
            app_local_data=r.ald,
            app_folder=app_folder,
        ),
        fp,
        pre,
    )


def one_save(r: Run, step: str, post: bytes, *, inject: bool = False) -> tuple[sv.SaveOutcome | None, sv.SavePlan]:
    r.trace.step = step
    p, _, pre = plan(r, post)
    say("plan", variant=r.variant, step=step, job=p.job, root=p.root, pre_sha256=sha256(pre))
    r.trace.fail_sync_after_rename = inject
    r.trace.renamed = False
    job = Job()

    def observe(fp: Fingerprint) -> Fingerprint:
        now = sv._observe(fp)
        say("gate_observe", variant=r.variant, step=step, equal_to_plan=now == fp)
        return now

    out: sv.SaveOutcome | None = None
    try:
        out = sv.run(p, sv.Ports(TracedFS(for_this_os(), r.trace), observe), job)
    except Exception as e:  # noqa: BLE001 (a probe reports every failure and goes on)
        say("save", variant=r.variant, step=step, outcome="raised", **failure(e))
    if out is not None:
        say(
            "save",
            variant=r.variant,
            step=step,
            outcome=out,
            protocol=protocol_save._result(out),
            events=job.events,
            first_failure=r.trace.first_failure,
        )
    r.trace.first_failure = None
    r.trace.fail_sync_after_rename = False
    say(
        "after_save",
        variant=r.variant,
        step=step,
        ledger_holds=r.files(),
        root_is_post=Path(r.root).read_bytes() == post,
        root_is_pre=Path(r.root).read_bytes() == pre,
        app_folder=listing(r.ald),
        outcome_read=attempt(
            lambda: sv.outcome(for_this_os(), r.ald, KEY, SESSION, p.job, p.changes)
        ),  # §6.6, as `save_outcome` reads it
    )
    return out, p


def opened(r: Run) -> bool:
    r.trace.step = "open"
    try:
        a = opening.run(opening.Ports(), r.root, Job(), target(r.cache, r.ald))
    except Exception as e:  # noqa: BLE001
        say("open", variant=r.variant, outcome="raised", **failure(e))
        return False
    say("open", variant=r.variant, outcome=a.outcome, published=type(a.published).__name__, refusal=a.refusal)
    return isinstance(a.published, pb.Published)


def rebuilt(r: Run, p: sv.SavePlan) -> None:
    r.trace.step = "rebuild"
    try:
        a = rebuild.run(rebuild.Ports(), p.fingerprint, Job(), target(r.cache, r.ald))
    except Exception as e:  # noqa: BLE001
        say("rebuild", variant=r.variant, outcome="raised", **failure(e))
        return
    say("rebuild", variant=r.variant, outcome=a.outcome, published=type(a.published).__name__)


def recover(r: Run, step: str, name: str | None) -> None:
    """Recover-all over a journal left `.open`: the listing, then the try of each listed record (a landed save's
    `.closed` record `name` renamed back to `.open` first, as `measure_save.py` undoes a save)."""
    r.trace.step = step
    fs = TracedFS(for_this_os(), r.trace)
    jd = os.path.join(r.ald, "local", KEY, "journal")
    if name is not None:
        closed = os.path.join(jd, f"{name}.closed")
        if not os.path.exists(closed):
            say("recover", variant=r.variant, step=step, skipped=f"no {name}.closed")
            return
        os.rename(closed, os.path.join(jd, f"{name}.open"))
    listed = attempt(lambda: recover_all.list_journals(fs, r.ald))
    say("recover_list", variant=r.variant, step=step, listing=listed)
    if not isinstance(listed, recover_all.Listing):
        return
    for item in listed.journals:
        res = attempt(lambda item=item: restore.recover_journal(fs, item.journal_dir, item.name))
        say("recover_try", variant=r.variant, step=step, journal=item.name, outcome=res, ledger_holds=r.files())
    say("after_recover", variant=r.variant, step=step, app_folder=listing(r.ald))


def save_part(folder: str, variant: str) -> None:
    os.mkdir(folder)
    ledger = os.path.join(folder, "L")
    os.mkdir(ledger)
    with open(os.path.join(ledger, "main.bean"), "wb") as f:
        f.write(MAIN)
    with open(os.path.join(ledger, "inc.bean"), "wb") as f:
        f.write(INC)
    app = tempfile.mkdtemp(prefix="ft-share-save-")
    try:
        ald = os.path.join(app, "app")
        os.makedirs(os.path.join(ald, "locks"))
        cache = os.path.join(app, "cache")
        os.mkdir(cache)
        with windows.WindowsFS().open_dir(app) as d:
            app_named = d.path()
        with windows.WindowsFS().open_dir(ledger) as d:
            roots = [("ledger", ledger), ("ledger", d.path()), ("app", app), ("app", app_named)]
            trace = Trace(roots=roots, tolerate_share_sync=variant == "share_sync_tolerated")
            r = Run(variant, ledger, os.path.join(ledger, "main.bean"), ald, cache, trace)
            say(
                "ledger_folder",
                variant=variant,
                roots=roots,
                remote=d.remote(),
                fs_type=d.fs_type(),
                caps=d.caps(),
                volume_id_as_built=attempt(lambda: ORIGINAL_VOLUME_ID(d)),
                volume_id_probed=d.volume_id(),
                mark=d.mark(),
            )
        if not opened(r):
            return
        posts = [MAIN + f'2026-01-0{i} * "Lunch {i}"\n  Expenses:Food  5 USD\n  Assets:Cash\n'.encode() for i in (2, 3)]
        out1, p1 = one_save(r, "save1", posts[0])
        if not isinstance(out1, sv.Saved):
            recover(r, "recover_after_save1", None)  # a journal the save left `.open`, if any
            return
        rebuilt(r, p1)
        out2, p2 = one_save(r, "save2", posts[0] + posts[1][len(MAIN) :])
        if not isinstance(out2, sv.Saved):
            recover(r, "recover_after_save2", None)
            return
        rebuilt(r, p2)
        out3, _ = one_save(r, "save3_injected", posts[0] + posts[1][len(MAIN) :] + b"; probe\n", inject=True)
        if out3 is not None and not isinstance(out3, sv.Refused):
            recover(r, "recover_after_save3", None)  # nothing to list when the rollback resolved it
        recover(r, "recover_save2", f"{SESSION}-{p2.job}")  # save 2's record reopened: the ledger back to save 1
    finally:
        shutil.rmtree(app, ignore_errors=True)


# -- parts 2 and 3: exclusive create, the adapter's replace ---------------------------------------------------------


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
get_attributes: Callable[[str], int] = _k32.GetFileAttributesW
_k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
_k32.GetFileAttributesW.restype = wintypes.DWORD
INVALID_ATTRIBUTES = 0xFFFFFFFF


def raw_open(api: windows._Api, path: str, access: int, share: int, disposition: int) -> int:
    h = api.CreateFileW(path, access, share, None, disposition, windows.FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise windows._win_error("CreateFileW", path)
    return int(h)


def seen(d: windows.WindowsDir, name: str) -> dict[str, object]:
    """An entry's bytes (a file) and identity, through the folder handle."""
    st = attempt(lambda: d.stat(name))
    data = attempt(lambda: d.read(name))
    return {"stat": st, "bytes": data}


def exclusive_part(folder: str) -> None:
    os.mkdir(folder)
    fs = windows.WindowsFS()
    api = fs.api
    with fs.open_dir(folder) as d:
        sub = os.path.join(folder, "sub")
        os.mkdir(sub)
        with open(os.path.join(sub, "inside.txt"), "wb") as f:
            f.write(b"inside")
        for name in ("x.txt", "held.txt", "pending.txt"):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"original " + name.encode())

        def case(label: str, name: str, fn: Callable[[], object]) -> None:
            before = seen(d, name)
            out = attempt(fn)
            after = seen(d, name)
            say("exclusive", case=label, name=name, outcome=out, before=before, after=after, same=before == after)

        case("adapter create, same name", "x.txt", lambda: d.create("x.txt", b"NEW", 0o644))
        case("adapter create, other case", "x.txt", lambda: d.create("X.TXT", b"NEW", 0o644))

        def nt_create(name: str) -> str:
            h = windows._nt_open(
                api,
                d.handle,
                name,
                windows.GENERIC_WRITE,
                windows.FILE_CREATE,
                windows.FILE_NON_DIRECTORY_FILE | windows.FILE_OPEN_REPARSE_POINT,
            )
            h.close()
            return "opened: not exclusive"

        case("NtCreateFile FILE_CREATE relative, nothing written", "x.txt", lambda: nt_create("x.txt"))

        def by_path() -> str:
            h = raw_open(api, os.path.join(folder, "x.txt"), windows.GENERIC_WRITE, windows.FILE_SHARE_ALL, CREATE_NEW)
            api.CloseHandle(h)
            return "opened: not exclusive"

        case("CreateFileW CREATE_NEW by path", "x.txt", by_path)

        def os_excl() -> str:
            os.close(os.open(os.path.join(folder, "x.txt"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return "opened: not exclusive"

        case("os.open O_CREAT|O_EXCL by path", "x.txt", os_excl)
        hold = raw_open(
            api, os.path.join(folder, "held.txt"), windows.GENERIC_READ, windows.FILE_SHARE_ALL, OPEN_EXISTING
        )
        try:
            case("adapter create, the file held by a reader", "held.txt", lambda: d.create("held.txt", b"NEW", 0o644))
        finally:
            api.CloseHandle(hold)
        hold = raw_open(
            api, os.path.join(folder, "pending.txt"), windows.GENERIC_READ, windows.FILE_SHARE_ALL, OPEN_EXISTING
        )
        try:
            removed = attempt(lambda: d.remove("pending.txt"))
            say("exclusive", case="pending: removed while a reader holds it", outcome=removed, names=d.names())
            case("adapter create, deletion pending", "pending.txt", lambda: d.create("pending.txt", b"NEW", 0o644))
        finally:
            api.CloseHandle(hold)
        say("exclusive", case="pending: after the reader closed", names=d.names(), entry=seen(d, "pending.txt"))
        case("adapter create, a folder at the name", "sub", lambda: d.create("sub", b"NEW", 0o644))
        with fs.open_dir(folder) as d2:
            say("exclusive", case="second handle: create", outcome=attempt(lambda: d2.create("y.txt", b"one", 0o644)))
            case(
                "adapter create, the name a second handle just made", "y.txt", lambda: d.create("y.txt", b"two", 0o644)
            )
        sub_before = {"stat": attempt(lambda: d.stat("sub")), "names": os.listdir(sub)}
        say("mkdir", case="adapter mkdir at an existing folder", outcome=attempt(lambda: d.mkdir("sub")))
        say(
            "mkdir",
            case="the folder after",
            before=sub_before,
            stat=attempt(lambda: d.stat("sub")),
            names=os.listdir(sub),
        )
        case("adapter mkdir at an existing file", "x.txt", lambda: d.mkdir("x.txt"))
        say("mkdir", case="os.mkdir by path at an existing folder", outcome=attempt(lambda: os.mkdir(sub)))
        # run 4's sequence (probe_windows_fs.probe_exclusive): was its name there before the create?
        h = windows._nt_open(
            api,
            d.handle,
            "made.txt",
            windows.GENERIC_WRITE | windows.DELETE,
            windows.FILE_CREATE,
            windows.FILE_NON_DIRECTORY_FILE,
        )
        with h:
            flags = windows.FILE_RENAME_POSIX_SEMANTICS
            status = h.set_info(
                windows.NT_FILE_RENAME_INFORMATION_EX, windows._rename_info(flags, d.handle, "made2.txt")
            )
        say(
            "exclusive",
            case="run 4's sequence: the POSIX rename to made2.txt",
            status=f"{status & 0xFFFFFFFF:#010x}",
            names=d.names(),
            made2_existed=d.stat("made2.txt") is not None,
        )
        case("run 4's create at made2.txt", "made2.txt", lambda: nt_create("made2.txt"))


def replace_part(folder: str) -> None:
    os.mkdir(folder)
    fs = windows.WindowsFS()
    api = fs.api
    with fs.open_dir(folder) as d:
        d.create("a.txt", b"old", 0o644)

        def replace(label: str, data: bytes, around: Callable[[], Any] | None = None) -> None:
            new_id = d.create("a.new", data, 0o644)
            before = seen(d, "a.txt")
            out = attempt(lambda: d.rename("a.new", "a.txt"))
            say(
                "replace",
                case=label,
                outcome=out,
                before=before,
                after=seen(d, "a.txt"),
                new_identity=new_id,
                names=d.names(),
                reader=None if around is None else attempt(around),
            )
            with contextlib.suppress(OSError):
                d.remove("a.new")

        replace("alone", b"new 1")
        for label, share in (
            ("a reader sharing deletion", windows.FILE_SHARE_ALL),
            ("a reader not sharing deletion", 3),
        ):
            h = raw_open(api, os.path.join(folder, "a.txt"), windows.GENERIC_READ, share, OPEN_EXISTING)
            reader = windows._Handle(api, h)
            try:
                replace(label, f"new under {label}".encode(), lambda reader=reader: reader.read_all(None))
            finally:
                reader.close()
        d.create("b.new", b"excl", 0o644)
        before = seen(d, "a.txt")
        out = attempt(lambda: d.rename_excl("b.new", "a.txt"))
        say("replace", case="exclusive rename over it", outcome=out, before=before, after=seen(d, "a.txt"))
        with contextlib.suppress(OSError):
            d.remove("b.new")
        path = os.path.join(folder, "a.txt")

        def by_attributes() -> str:
            return "missing" if get_attributes(path) == INVALID_ATTRIBUTES else "there"

        def by_open() -> str:
            try:
                api.CloseHandle(
                    raw_open(api, path, windows.FILE_READ_ATTRIBUTES, windows.FILE_SHARE_ALL, OPEN_EXISTING)
                )
            except FileNotFoundError:
                return "missing"
            except OSError as e:
                return f"error {e.winerror}"
            return "there"

        for how, look in (("its attributes asked (no handle)", by_attributes), ("opened, sharing all", by_open)):
            replaces_watched(d, how, look)


def replaces_watched(d: windows.WindowsDir, how: str, look: Callable[[], str]) -> None:
    """300 of the adapter's replaces of `a.txt` while another thread looks for it (`look`): how often it was missing
    (a state between two steps of one replace, which a power loss can keep) and what the replaces answered."""
    stop = threading.Event()
    looks: dict[str, int] = {}

    def watch() -> None:
        while not stop.is_set():
            seen_now = look()
            looks[seen_now] = looks.get(seen_now, 0) + 1

    failures: dict[str, int] = {}
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        for i in range(300):
            try:
                d.create("a.new", f"round {i}".encode(), 0o644)
                d.rename("a.new", "a.txt")
            except OSError as e:
                failures[str(e)] = failures.get(str(e), 0) + 1
                with contextlib.suppress(OSError):
                    d.remove("a.new")
    finally:
        stop.set()
        t.join()
    say("replace", case=f"300 replaces while another thread looks: {how}", looks=looks, failures=failures)


# -- part 4: identity --------------------------------------------------------------------------------------------------


def identity_of(path: str) -> dict[str, object]:
    fs = windows.WindowsFS()
    out: dict[str, object] = {"path": path}
    try:
        d = fs.open_dir(path)
    except OSError as e:
        return {**out, **failure(e)}
    try:
        with d:
            out.update(_identity_seen(d))
    except Exception as e:  # noqa: BLE001 (a probe reports every failure and goes on)
        out.update(failure(e))
    return out


def _identity_seen(d: windows.WindowsDir) -> dict[str, object]:
    out: dict[str, object] = {}
    h = d._h
    fs_type, serial32, flags = h._volume_info()
    raw = attempt(lambda: h.info(windows.FILE_ID_INFO, 24))
    if isinstance(raw, bytes):
        out["file_id_info"] = {
            "volume_serial64": f"{int.from_bytes(raw[:8], 'little'):016X}",
            "file_id128": raw[8:24].hex(),
        }
    else:
        out["file_id_info"] = raw
    out["fs_type"] = fs_type
    out["serial32"] = f"{serial32 >> 16:04X}-{serial32 & 0xFFFF:04X}"
    out["flags"] = f"{flags:#x}"
    out["remote"] = d.remote()
    out["created_filetime"] = h.basic()[0]
    paths: dict[str, object] = {"adapter": attempt(d.path)}
    for label, how in (("dos", 0x0), ("guid", 0x1), ("nt", 0x2)):
        buf = ctypes.create_unicode_buffer(windows.MAX_PATH_CHARS)
        n = h.api.GetFinalPathNameByHandleW(h.handle, buf, windows.MAX_PATH_CHARS, how)
        paths[label] = buf.value if n else windows._win_error("GetFinalPathNameByHandleW").strerror
    out["final_paths"] = paths
    out["volume_id_as_built"] = attempt(lambda: ORIGINAL_VOLUME_ID(d))
    with drive_id_on_shares():
        mark = d.mark()
        out["volume_id_from_share_serial"] = mark.volume
        out["mark"] = mark
        out["drive_of"] = handles.drive_of(mark)
        out["known_birth"] = handles.known_birth(mark)
        out["folder_id"] = port.folder_id(d)
    return out


def identity(label: str, roots: list[str]) -> None:
    first = os.path.join(roots[0], IDENTITY_FOLDER)
    if not os.path.isdir(first):
        say("identity_made", folder=first, outcome=attempt(lambda: os.mkdir(first)))
    for root in roots:
        say("identity", label=label, root=root, root_seen=identity_of(root))
        say("identity", label=label, folder=identity_of(os.path.join(root, IDENTITY_FOLDER)))


# -- main --------------------------------------------------------------------------------------------------------------


def probe(folders: list[str]) -> None:
    for index, folder in enumerate(folders):
        base = os.path.join(os.path.abspath(folder), f".ft-share-save-{os.getpid()}-{index}")
        say("folder", folder=folder, scratch=base)
        try:
            os.mkdir(base)
        except OSError as e:
            say("folder", folder=folder, **failure(e))
            continue
        try:
            for variant in ("as_built", "share_sync_tolerated"):
                with drive_id_on_shares():
                    say("part", name="save", variant=variant, folder=folder)
                    try:
                        save_part(os.path.join(base, variant), variant)
                    except Exception as e:  # noqa: BLE001
                        say("save_part", variant=variant, outcome="raised", **failure(e))
            for name, fn in (("exclusive", exclusive_part), ("replace", replace_part)):
                say("part", name=name, folder=folder)
                try:
                    fn(os.path.join(base, name))
                except Exception as e:  # noqa: BLE001
                    say(name, outcome="raised", **failure(e))
        finally:
            shutil.rmtree(base, ignore_errors=True)
            say("cleanup", folder=folder, scratch_left=os.path.exists(base))


USAGE = "usage: probe_windows_share_save.py probe <folder>... | identity <label> <root>... | identity-remove <root>"


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "probe":
        with under_test.windows_port_under_test():
            probe(argv[1:])
        return 0
    if len(argv) >= 3 and argv[0] == "identity":
        identity(argv[1], argv[2:])
        return 0
    if len(argv) == 2 and argv[0] == "identity-remove":
        folder = os.path.join(argv[1], IDENTITY_FOLDER)
        shutil.rmtree(folder, ignore_errors=True)
        say("identity_removed", folder=folder, left=os.path.exists(folder))
        return 0
    sys.stderr.write(USAGE + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
