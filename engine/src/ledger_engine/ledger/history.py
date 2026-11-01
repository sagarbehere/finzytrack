"""The history trim on disk (`design/specs/engine-jobs.md` §6.5, §6.1 step 9; INV-10; BL-0169; S0074): after a
landed save, in the save's own engine (it holds the engine lock and ran no user code), the history of the save's
workspace key is cut to the newest `core.history.KEEP` of each kind (the choice is `ledger.core.history`).

`trim(fs, app_local_data, workspace_key, current)`:
1. **What protects** (`_protected`): every `local/<key>/journal/` folder of every key is listed; an `.open` or
   `.part` entry protects its tag, wherever its backups folder is (a record names a backups folder of its own name
   only: `journal._check`). Any folder there that cannot be listed (other than one that is absent) stops the whole
   trim: it may hold an `.open` record we cannot see (fails closed). Only real folders are walked, as recover-all
   walks them.
2. **What is there:** `local/` and this key's `journal/` and `backups/`, each a real folder (an entry that is not
   followed through a link) at its canonical path (the path guard, `handles.Handles`); a link or a moved folder in
   their place, or a listing that fails, stops the whole trim (it could lead into a ledger folder: INV-27, INV-30).
   Each entry is `stat`ed, not followed; a `stat` that fails leaves its save alone.
3. **The backups folders** of the saves chosen, oldest first: a save's journal entries are listed again and must be
   exactly those chosen (an entry that appeared since, an `.open` or `.part` above all, leaves the save); its backups
   folder is opened under the path guard, every entry in it must be a regular file (else the save is left), they are
   removed, then the folder. Then `backups/` is synced once, so no record goes before its backups folder's removal
   is durable (a power loss never turns a trimmed save into a backups folder without a record).
4. **Their journal entries**, for each save whose backups folder is gone. They are not synced: a power loss can
   bring any of them back, which is history again (a record without its backups folder), and the next trim deletes
   it again.
An error leaves the rest of that save's history and the trim goes on with the next save; a failed sync of `backups/`
leaves every record.

Only `local/<key>/journal/` and `local/<key>/backups/` are ever written, and in them only names of a save's
history (`core.history`); the ledger folders, kept copies, the current save's record and every `.open` journal and
its backups are never touched. No error of the trim fails the save it follows: the save has landed and the user was
told "saved"; `Trimmed` tells what happened (counts only). An engine death mid-trim (`BaseException`) passes through:
the sidecar then reads the outcome from disk (§6.6), and the current `.closed` record is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ledger_engine.fs.port import DirHandle, FileSystem, Kind
from ledger_engine.ledger.core import history as core
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.handles import Handles, PathMovedError, join

LOCAL: Final = "local"
JOURNAL: Final = "journal"
BACKUPS: Final = "backups"


@dataclass(frozen=True, slots=True)
class Trimmed:
    """What one trim did: `removed` saves whose history is gone, `left` saves chosen whose history (or part of it)
    stayed after an error, `skipped` when nothing was tried (a folder could not be listed or was not a real folder)."""

    removed: int
    left: int
    skipped: bool


SKIPPED: Final = Trimmed(0, 0, True)


class _Leave(Exception):
    """This save's history is not what was chosen, or holds something not ours: leave the rest of it."""


_ERRORS: Final = (_Leave, OSError, PathMovedError, ValueError)


def _is_dir(h: DirHandle, name: str) -> bool | None:
    """The entry `name` is a real folder (True), absent (None), or something else (False: a link, a file)."""
    st = h.stat(name)
    if st is None:
        return None
    return st.kind is Kind.DIR


def _protected(handles: Handles, local: str) -> frozenset[str]:
    """Step 1 (the module's docstring). `OSError` or `PathMovedError` when a folder cannot be read."""
    out: set[str] = set()
    root = handles.at(local)
    for key in root.names():
        if not _is_dir(root, key):
            continue
        kd = handles.at(join(local, key))
        if not _is_dir(kd, JOURNAL):
            continue
        for name in handles.at(join(join(local, key), JOURNAL)).names():
            tag = core.gating(name)
            if tag is not None:
                out.add(tag)
    return frozenset(out)


def _entries(h: DirHandle) -> list[core.Entry]:
    out: list[core.Entry] = []
    for name in h.names():
        try:
            st = h.stat(name)
        except OSError:
            out.append(core.Entry(name, None, 0))
            continue
        if st is not None:
            out.append(core.Entry(name, st.kind, st.mtime_ns))
    return out


def _remove_backups(handles: Handles, jdir: str, bdir: str, g: core.Group) -> bool:
    """Step 3 for one save; True when it removed a folder. `_Leave`, `OSError` or `PathMovedError` leave the save."""
    now = sorted(
        n for n in handles.at(jdir).names() if (p := core.journal_tag(n)) is not None and p[0].casefold() == g.tag
    )
    if tuple(now) != g.journal:
        raise _Leave
    if g.backups is None:
        return False
    bd = handles.at(bdir)
    here = _is_dir(bd, g.backups)
    if here is None:
        return False
    if not here:
        raise _Leave
    folder = handles.at(join(bdir, g.backups))
    names = folder.names()
    for n in names:
        st = folder.stat(n)
        if st is not None and st.kind is not Kind.FILE:
            raise _Leave
    for n in names:
        folder.remove(n)
    bd.rmdir(g.backups)
    return True


def _remove_journal(jd: DirHandle, g: core.Group) -> None:
    """Step 4 for one save."""
    for n in g.journal:
        st = jd.stat(n)
        if st is None:
            continue
        if st.kind is not Kind.FILE:
            raise _Leave
        jd.remove(n)


def trim(fs: FileSystem, app_local_data: str, workspace_key: str, current: str) -> Trimmed:
    """The history trim (the module's docstring). `current`: the landed save's `<session>-<job>`."""
    handles = Handles(fs)
    try:
        try:
            r = fs.resolve(app_local_data.rstrip("/") or "/")
            if r.rest:
                return SKIPPED
            ald = core_paths.canonical_path(r)
            if not _is_dir(handles.at(ald), LOCAL):
                return SKIPPED  # absent, or a link in its place: never followed
            local = join(ald, LOCAL)
            protected = _protected(handles, local)
            kd = handles.at(join(local, workspace_key))
            if not (_is_dir(kd, JOURNAL) and _is_dir(kd, BACKUPS)):
                return SKIPPED
            jdir, bdir = join(join(local, workspace_key), JOURNAL), join(join(local, workspace_key), BACKUPS)
            chosen = core.select(_entries(handles.at(jdir)), _entries(handles.at(bdir)), protected, current)
        except (OSError, PathMovedError, ValueError):
            return SKIPPED
        ready: list[core.Group] = []
        synced = True
        for g in chosen:
            try:
                if _remove_backups(handles, jdir, bdir, g):
                    synced = False
                ready.append(g)
            except _ERRORS:
                pass
        if not synced:
            try:
                handles.at(bdir).sync()
            except (OSError, PathMovedError):
                ready = []  # a removal a power loss may undo: every record stays, the next trim tries again
        removed = 0
        for g in ready:
            try:
                _remove_journal(handles.at(jdir), g)
                removed += 1
            except _ERRORS:
                pass
        return Trimmed(removed, len(chosen) - removed, False)
    finally:
        handles.close()
