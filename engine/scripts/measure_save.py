"""Measure a save on a ledger as the product runs it (S0063, S0106; BL-0112; INV-20: a write in at most 5 s at 54k).

usage (cwd = engine/): .venv/bin/python scripts/measure_save.py ROOT WORK_DIR [--runs N] [--app APP_DIR]
ROOT is the ledger's root file (the demo generator's 54k ledger: backend/scripts/gen_demo.py --size 54k), WORK_DIR an
empty scratch folder on the disk to measure (never a real ledger: every run saves into a fresh copy). Each run:

1. copies the ledger's folder and makes the app's folders (machine-local data, cache);
2. the open (`ledger.opening.run`: the overlay load, the mirror's build and its publish), which the save's gate needs
   (a save checks the published generation against the disk, `design/specs/mirror/publish.md` §7; S0087);
3. the preview's load of the candidate (one appended transaction), which makes the plan; not part of the save;
4. the save (`ledger.save.run` on the real file-system port: the records, the gate, the durable order; no load:
   ADR-0068 D1, S0070), with the durable order's syncs and their time (`F_FULLFSYNC` of a folder, of a file, the
   files created and synced, the renames and the swap);
5. the rebuild after it (`ledger.rebuild.run`: Beancount's own load, the build and the publish), which nobody waits
   for (engine-jobs §6.7) but which ends when everything on screen shows the change;
6. the save undone the way recovery does it (the closed record renamed back to `.open`, `restore.recover_journal`),
   with the restore's syncs; the ledger must be back to its bytes.

`--app`: an empty scratch folder for the app's machine-local folder (journal, backups) when WORK_DIR is on another
drive, as it is for a ledger on a drive without the swap (S0065: the journal and backups stay on the Mac's disk,
INV-27, INV-30); the default is inside WORK_DIR. Like the engine, the script reads no clock but the timer (INV-07,
EP-10): the publish's `today` and `built_at` are fixed, and its `now_ns` is read from a file the disk just stamped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import DirHandle, FileSystem, Resolved
from ledger_engine.ledger import opening, overlay, publishing, rebuild, restore
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Fingerprint, as_before
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import Change, sha256
from ledger_engine.mirror import publish as pb

TXN = b'\n2026-10-30 * "Measured save"\n  Expenses:Food:Groceries  12.34 USD\n  Assets:Bank:Checking\n'
KEY = "k1"
TODAY = date(2026, 10, 30)  # the publish's `as_of`; any civil date measures the same
BUILT_AT = "2026-10-30T10:00:00+00:00"
LINEAGE = "lin1"


@dataclass
class Tally:
    counts: Counter[str] = field(default_factory=Counter[str])
    seconds: dict[str, float] = field(default_factory=dict[str, float])

    def time[T](self, what: str, fn: Callable[[], T]) -> T:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            self.counts[what] += 1
            self.seconds[what] = self.seconds.get(what, 0.0) + time.perf_counter() - t0

    def line(self) -> str:
        return ", ".join(f"{k} {self.counts[k]}x {self.seconds[k] * 1000:.0f} ms" for k in sorted(self.counts))


class CountingDir:
    """A folder handle that times the durable order's effects and returns exactly what the real handle returns (the
    identity of `create` and `sync_file`, S0101); a child folder opened through it is counted too."""

    def __init__(self, inner: DirHandle, tally: Tally) -> None:
        self._inner = inner
        self._tally = tally

    def sync(self) -> None:
        self._tally.time("folder_sync", self._inner.sync)

    def sync_file(self, name: str) -> tuple[int, int]:
        return self._tally.time("file_sync", lambda: self._inner.sync_file(name))

    def create(self, name: str, data: bytes | Iterable[bytes], mode: int, like: str | None = None) -> tuple[int, int]:
        return self._tally.time("create_synced", lambda: self._inner.create(name, data, mode, like))

    def rename_excl(self, src: str, dst: str) -> None:
        self._tally.time("rename", lambda: self._inner.rename_excl(src, dst))

    def rename(self, src: str, dst: str) -> None:
        self._tally.time("plain_rename", lambda: self._inner.rename(src, dst))

    def swap(self, a: str, b: str) -> None:
        self._tally.time("swap", lambda: self._inner.swap(a, b))

    def open_child(self, name: str) -> DirHandle:
        return CountingDir(self._inner.open_child(name), self._tally)  # type: ignore[return-value]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __enter__(self) -> CountingDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()


class CountingFS:
    def __init__(self, inner: FileSystem, tally: Tally) -> None:
        self._inner = inner
        self._tally = tally

    def resolve(self, path: str) -> Resolved:
        return self._inner.resolve(path)

    def open_dir(self, path: str) -> DirHandle:
        return CountingDir(self._inner.open_dir(path), self._tally)  # type: ignore[return-value]


def say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


class Measured(Exception):
    """A run that did not end as the measurement expects."""


def check(ok: bool, what: str) -> None:
    if not ok:
        raise Measured(what)


class Job:
    def progress(self, phase: str) -> None:
        pass

    def gate(self) -> None:
        pass

    def committed(self) -> None:
        pass


def fs_now(folder: Path) -> int:
    """Now, as the file system stamps a file: the cleanup's grace period compares it with modification times (the
    engine and its scripts read no clock: INV-07, EP-10)."""
    probe = folder / ".now"
    probe.write_bytes(b"")
    try:
        return probe.stat().st_mtime_ns
    finally:
        probe.unlink()


def target(cache: Path, ald: Path) -> publishing.Target:
    return publishing.Target(
        app_cache=str(cache),
        workspace_key=KEY,
        app_local_data=str(ald),
        lineage=LINEAGE,
        floor=0,
        today=TODAY,
        built_at=BUILT_AT,
        now_ns=fs_now(cache),
        engine_build="measure",
    )


def one_run(src: Path, work: Path, n: int, app: Path | None = None) -> None:
    folder = work / f"run{n}"
    shutil.copytree(src.parent, folder / "L")
    root = folder / "L" / src.name
    ald = (app if app is not None else folder) / f"app{n}"
    ald.mkdir(parents=True)
    cache = folder / "cache"
    cache.mkdir()
    policy = DesktopPolicy()
    pre = root.read_bytes()

    t0 = time.perf_counter()
    opened = opening.run(opening.Ports(), str(root), Job(), target(cache, ald))
    open_s = time.perf_counter() - t0
    check(opened.outcome == "loaded", f"the open did not load: {opened.outcome}")
    check(isinstance(opened.published, pb.Published), f"the open did not publish: {opened.published}")

    t0 = time.perf_counter()
    planned = overlay.load(str(root), {str(root): pre + TXN}, policy)  # the preview's load of the candidate
    plan_s = time.perf_counter() - t0
    check(isinstance(planned, overlay.Loaded), f"the plan's load refused: {planned}")
    assert isinstance(planned, overlay.Loaded)  # noqa: S101 (narrowing after the check)
    fingerprint = as_before(planned.fingerprint, {str(root): sha256(pre)})
    st = os.stat(ald)
    plan = sv.SavePlan(
        session="m1",
        job=f"j{n}",
        workspace_key=KEY,
        lineage=LINEAGE,
        engine_build="measure",
        root=str(root),
        changes=(Change(str(root), sha256(pre), pre + TXN),),
        fingerprint=fingerprint,
        app_cache=str(cache),
        app_local_data=str(ald),
        app_folder=(st.st_dev, st.st_ino),  # the identity at the start (S0102)
    )
    parts = Tally()
    durable = Tally()

    def observe(fp: Fingerprint) -> Fingerprint:
        return parts.time("observe", lambda: sv._observe(fp))  # pyright: ignore[reportPrivateUsage]

    ports = sv.Ports(CountingFS(for_this_os(), durable), observe)
    t0 = time.perf_counter()
    out = sv.run(plan, ports, Job())
    total = time.perf_counter() - t0
    check(isinstance(out, sv.Saved), f"the save did not land: {out}")
    say(
        f"run {n}: open (load, build, publish) {open_s:.2f} s; preview load {plan_s:.2f} s (before the confirm);"
        f" save {total:.3f} s; {parts.line()}"
    )
    say(f"  durable order: {durable.line()}")

    t0 = time.perf_counter()
    rebuilt = rebuild.run(rebuild.Ports(), fingerprint, Job(), target(cache, ald))
    rebuild_s = time.perf_counter() - t0
    say(f"  rebuild (Beancount's own load, build, publish; after the reply): {rebuild_s:.2f} s, {rebuilt.outcome}")
    check(rebuilt.outcome == "loaded", f"the rebuild did not load: {rebuilt.outcome}")
    check(isinstance(rebuilt.published, pb.Published), f"the rebuild did not publish: {rebuilt.published}")

    jd = ald / "local" / KEY / "journal"
    name = f"m1-j{n}"
    os.rename(jd / f"{name}.closed", jd / f"{name}.open")
    undo = Tally()
    t0 = time.perf_counter()
    res = restore.recover_journal(CountingFS(for_this_os(), undo), str(jd), name)
    say(f"  restore: {time.perf_counter() - t0:.2f} s, {type(res).__name__}; {undo.line()}")
    check(isinstance(res, restore.Resolved), f"the restore did not end Resolved: {res}")
    check(root.read_bytes() == pre, "the restore did not put the ledger back")
    shutil.rmtree(folder)
    shutil.rmtree(ald, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("root", type=Path)
    p.add_argument("work", type=Path)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--app", type=Path, default=None)
    a = p.parse_args(argv)
    if any(a.work.iterdir()) or (a.app is not None and any(a.app.iterdir())):
        sys.stderr.write("WORK_DIR and APP_DIR must be empty\n")
        return 2
    say(f"ledger: {os.path.getsize(a.root) / 1e6:.1f} MB root file")
    for n in range(a.runs):
        one_run(a.root.resolve(), a.work.resolve(), n, None if a.app is None else a.app.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
