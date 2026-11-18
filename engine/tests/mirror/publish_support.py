"""Helpers for the publish's tests (S0087, BL-0113): a small ledger loaded with Beancount's own loader, and one whole
publish job (the folder and the cleanup, then steps 2 to 8 of `design/specs/mirror/publish.md` §4) into a cache folder
under `tmp_path` (INV-24: temporary folders only; INV-23: synthetic data only)."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from ledger_engine.fs.port import DirHandle, FileSystem
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.store import SqliteStore

KEY = "k1"
TODAY = date(2026, 11, 8)
BUILT_AT = "2026-11-08T10:00:00+05:30"
ENGINE_BUILD = "test engine build"
MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b'2026-01-01 open Expenses:Food\n2026-01-02 * "Lunch"\n  Expenses:Food  5 USD\n  Assets:Cash\n'


def write_ledger(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "main.bean").write_bytes(MAIN)
    (folder / "inc.bean").write_bytes(INC)
    return folder / "main.bean"


def ledger_set(root: Path) -> LedgerSet:
    return LedgerSet(str(root), (str(root), str(root.parent / "inc.bean")), (), (), ())


def loaded(root: Path, ledger: LedgerSet | None = None) -> tuple[port.ParseResult, Fingerprint]:
    """Beancount's own load of the ledger, and the fingerprint of the set as observed after it (the rebuild's stamp)."""
    result = port.real_load(str(root))
    return result, observe(ledger or ledger_set(root))


def fs_now(folder: Path) -> int:
    """Now, as the file system stamps a file (the engine reads no clock: INV-07, EP-10; the cleanup compares this
    with modification times)."""
    probe = folder / ".now"
    probe.write_bytes(b"")
    try:
        return probe.stat().st_mtime_ns
    finally:
        probe.unlink()


def same() -> pb.Recheck:
    return "same"


def publish_job(
    fs: FileSystem,
    cache: Path,
    root: Path,
    *,
    floor: int = 0,
    recheck: Callable[[], pb.Recheck] = same,
    store: pb.Store | None = None,
    now_ns: int | None = None,
    around: Callable[[DirHandle], object] | None = None,
    ledger: LedgerSet | None = None,
) -> pb.Published | pb.NotPublished:
    """One job: §4 step 1 (the folder, the cleanup), the load, then steps 2 to 8. `around` acts on the folder's handle
    between the cleanup and the build (an outside program's act during the load)."""
    store = store or SqliteStore()
    result, fp = loaded(root, ledger)
    with pb.open_folder(fs, str(cache), KEY) as d:
        cleaned = pb.clean(d, store, fs_now(cache) if now_ns is None else now_ns)
        if around is not None:
            around(d)
        return pb.publish(
            d,
            cleaned,
            store,
            result,
            fp,
            floor=floor,
            today=TODAY,
            built_at=BUILT_AT,
            engine_build=ENGINE_BUILD,
            recheck=recheck,
            by="rebuild",
        )


def mirror_of(cache: Path) -> Path:
    return cache / "workspaces" / KEY / "mirror"


def meta(path: Path) -> dict[str, str]:
    """A generation's `meta`, read as an outside tool would (read-only)."""
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT key, value FROM m_meta").fetchall())
    finally:
        con.close()


CUR = pb.POINTER if sys.platform == "win32" else pb.CURRENT
"""The entry that names the current generation on this OS: the link `current.db`, Windows' pointer `current.txt`."""


def current_in(m: Path) -> str | None:
    """What the mirror folder `m`'s current entry names, read as a reader does: the link's target (POSIX), or the
    pointer's whole content through the port, whose handles share deletion and never block a swap (Windows; a plain
    `open` would refuse the engine's rename while it reads). None when there is none."""
    if sys.platform != "win32":
        return os.readlink(m / CUR) if (m / CUR).is_symlink() else None
    from ledger_engine.fs.this_os import for_this_os

    with for_this_os().open_dir(str(m)) as d:
        data = d.read(CUR) if d.stat(CUR) is not None and not (m / CUR).is_dir() else None
    return None if data is None else data.decode("ascii", "replace")


def current(cache: Path) -> str | None:
    return current_in(mirror_of(cache))


def point_at(m: Path, target: str) -> None:
    """Another program makes the current entry name `target`: a link (POSIX), the pointer's content (Windows)."""
    (m / CUR).unlink(missing_ok=True)
    if sys.platform == "win32":
        (m / CUR).write_bytes(target.encode("ascii"))
    else:
        (m / CUR).symlink_to(target)
