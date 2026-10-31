"""Measure the save job on a ledger (S0063; BL-0112; INV-20's absolute: a write in at most 5 s at 54k entries).

usage (cwd = engine/): .venv/bin/python scripts/measure_save.py ROOT WORK_DIR [--runs N] [--app APP_DIR]
ROOT is the ledger's root file (the demo generator's 54k ledger: backend/scripts/gen_demo.py --size 54k), WORK_DIR an
empty scratch folder on the disk to measure (never a real ledger: every run saves into a fresh copy). Each run copies
the ledger's folder, plans one appended transaction (a load of the copy, as a preview would), then runs
`ledger.save.run` on the real file-system port: the records, the locks, the gate, the commit's load, the re-check, the
durable order. Then R4 on the closed record (S0066: its check, Beancount's own load, must pass; then its confirm, timed
on the same record). It prints per run the total and each part, and the durable order's syncs (`F_FULLFSYNC` of a
folder, of a file, the files created and synced) with their time: S0061's additions (the staging rename and its folder
sync, `sync_file` per replaced file) are counted there. Then it undoes the save the way recovery does (the closed record
renamed back to `.open`, `restore.recover_journal`) and prints the restore's syncs. `--app`: an empty scratch folder for
the app's machine-local folder (journal, backups, locks) when WORK_DIR is on another drive, as it is for a ledger on a
drive without the swap (S0065: the journal and backups stay on the Mac's disk, INV-27, INV-30); the default is inside
WORK_DIR. The engine reads no clock in product code (EP-10); this measurement script is not product code.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger_engine.fs.darwin import DarwinLegacyLocks, DarwinLocks, for_this_os
from ledger_engine.fs.port import DirHandle, FileSystem, Resolved
from ledger_engine.ledger import overlay, r4
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Fingerprint, digest
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.restore import recover_journal
from ledger_engine.ledger.writer import Change, sha256

TXN = b'\n2026-10-30 * "Measured save"\n  Expenses:Food:Groceries  12.34 USD\n  Assets:Bank:Checking\n'


@dataclass
class Tally:
    counts: Counter[str] = field(default_factory=Counter[str])
    seconds: Counter[str] = field(default_factory=Counter[str])

    def time(self, what: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            self.counts[what] += 1
            self.seconds[what] += time.perf_counter() - t0

    def line(self) -> str:
        return ", ".join(f"{k} {self.counts[k]}x {self.seconds[k] * 1000:.0f} ms" for k in sorted(self.counts))


class CountingDir:
    def __init__(self, inner: DirHandle, tally: Tally) -> None:
        self._inner = inner
        self._tally = tally

    def sync(self) -> None:
        self._tally.time("folder_sync", self._inner.sync)

    def sync_file(self, name: str) -> None:
        self._tally.time("file_sync", lambda: self._inner.sync_file(name))

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        self._tally.time("create_synced", lambda: self._inner.create(name, data, mode, like))

    def rename_excl(self, src: str, dst: str) -> None:
        self._tally.time("rename", lambda: self._inner.rename_excl(src, dst))

    def rename(self, src: str, dst: str) -> None:
        self._tally.time("plain_rename", lambda: self._inner.rename(src, dst))

    def swap(self, a: str, b: str) -> None:
        self._tally.time("swap", lambda: self._inner.swap(a, b))

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


def one_run(src: Path, work: Path, n: int, app: Path | None = None) -> None:
    folder = work / f"run{n}"
    shutil.copytree(src.parent, folder / "L")
    root = folder / "L" / src.name
    ald = (app if app is not None else folder) / f"app{n}"
    (ald / "locks").mkdir(parents=True)
    policy = DesktopPolicy()
    t0 = time.perf_counter()
    planned = overlay.load(str(root), {}, policy)
    check(isinstance(planned, overlay.Loaded), "the plan's load refused")
    assert isinstance(planned, overlay.Loaded)  # noqa: S101 (narrowing after the check)
    plan_s = time.perf_counter() - t0
    pre = root.read_bytes()
    plan = sv.SavePlan(
        session="m1",
        job=f"j{n}",
        workspace_key="k1",
        lineage="lin1",
        engine_build="measure",
        root=str(root),
        changes=(Change(str(root), sha256(pre), pre + TXN),),
        fingerprint=planned.fingerprint,
        mirror=digest(planned.fingerprint),
        legacy_locks=(),
        app_local_data=str(ald),
    )
    parts = Tally()
    durable = Tally()
    real_load = sv.desktop_load(policy, for_this_os())

    def load(r: str, c: Mapping[str, bytes]) -> overlay.LoadResult:
        return parts.time("load", lambda: real_load(r, c))

    def observe(fp: Fingerprint) -> Fingerprint:
        return parts.time("observe", lambda: sv._observe(fp))  # pyright: ignore[reportPrivateUsage]

    ports = sv.Ports(
        CountingFS(for_this_os(), durable), DarwinLocks(str(ald / "locks")), DarwinLegacyLocks(), load, observe
    )
    t0 = time.perf_counter()
    out = sv.run(plan, ports, Job())
    total = time.perf_counter() - t0
    check(isinstance(out, sv.Saved), f"the save did not land: {out}")
    say(f"run {n}: save {total:.2f} s (plan load {plan_s:.2f} s, not in the save); {parts.line()}")
    say(f"  durable order: {durable.line()}")
    jd = ald / "local" / "k1" / "journal"
    name = f"m1-j{n}"
    r4_ports = r4.desktop(for_this_os(), "measure", policy)
    t0 = time.perf_counter()
    checked = r4.check(r4_ports, str(ald), "k1", name, Job())
    say(f"  R4 check (steps 1-3, Beancount's own load): {time.perf_counter() - t0:.2f} s, {checked.outcome}")
    check(checked.outcome == "pass", f"R4 did not pass: {checked}")
    os.rename(jd / f"{name}.done", jd / f"{name}.closed")
    t0 = time.perf_counter()
    confirmed = r4.confirm(r4_ports, str(ald), "k1", name, Job())
    say(f"  R4 confirm (step 4, two parses): {time.perf_counter() - t0:.2f} s, {confirmed.outcome}")
    os.rename(jd / f"{name}.done", jd / f"{name}.open")
    undo = Tally()
    t0 = time.perf_counter()
    res = recover_journal(CountingFS(for_this_os(), undo), DarwinLocks(str(ald / "locks")), str(jd), name)
    say(f"  restore: {time.perf_counter() - t0:.2f} s, {type(res).__name__}; {undo.line()}")
    check(root.read_bytes() == pre, "the restore did not put the ledger back")
    shutil.rmtree(folder)
    shutil.rmtree(ald, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("root", type=Path)
    p.add_argument("work", type=Path)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--app", type=Path, default=None)
    a = p.parse_args(argv)
    if any(a.work.iterdir()) or (a.app is not None and any(a.app.iterdir())):
        sys.stderr.write("WORK_DIR and APP_DIR must be empty\n")
        return 2
    for n in range(a.runs):
        one_run(a.root.resolve(), a.work.resolve(), n, None if a.app is None else a.app.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
