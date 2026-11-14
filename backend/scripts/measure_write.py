#!/usr/bin/env python3
"""Measure one write on the product's path: the preview, its confirm's save, and the rebuild until the publish
(S0119; BL-0112; ADR-0008 and its amendments of 2026-10-25, 2026-10-27 and 2026-10-31; ADR-0064; INV-20, INV-10).

usage (cwd = backend/):
    .venv/bin/python scripts/measure_write.py ROOT [--layout one|files12|meta20 ...] [--runs 1]

ROOT is a generated ledger's root file (`scripts/gen_demo.py --size 54k` or `--size demo`); it is only read. Each
`--layout` (default: `one`) is derived from its text into a fresh test copy for every run, never written back:
- `one`: the root file as it is;
- `files12`: the same text split into 13 files: the root keeps every line that is not part of a dated entry (the
  comments, the options) and ends with one glob include, `include "parts/*.beancount"`; the dated entries, in file
  order, go into `parts/01.beancount` to `parts/12.beancount`, the k-th file holding entries k*N/12 to (k+1)*N/12;
- `meta20` (the 20 MB recipe): the root file with five metadata lines added after the first line of every transaction,
  in the shape the v0.3 demo generator gave an imported transaction (spike S4's `big` kept it):
  `id` (a UUID, version 5, of "finzytrack-measure/<i>"), `content_hash` (the SHA-256 of "<i>" NUL and the
  transaction's text), `source_account` (its last posting's account), `external_id` (its date's digits and <i> in
  8 digits), `external_id_type` ("OFX"); <i> is the transaction's index in the file, from 0. On the 54k ledger it
  gives about 21 MB with the same 54,000 transactions.

Everything happens in a fresh temp folder (honours TMPDIR): the app's folder and cache are made there, and the jobs run
through the sidecar's own scheduler (`EngineJobs`) and write use cases (`app.workspaces.writes`), as the product wires
them (`app.main.wire`), on the sidecar's own engine command (`app.main.engine_command`: the engine of `../engine/.venv`,
its bytecode folder, the engine lock and the app cache), so each job gets the engine the scheduler gives it: a fresh
one after any job that ran the ledger's plugins (engine-jobs §5.1), its start inside the job's time. The bytecode fill
is awaited before the first open (the state of every launch after the first).

**The producer.** No mutator exists yet and the shipped engine registers none (`preview.PRODUCERS` is empty,
`tests/workspaces/test_writes_end_to_end.py::test_the_shipped_engine_has_no_producer`). This script's engine command
is the product's with one change, its startup code: after the path setup it appends `engine/` to the path and
registers the engine's test producer (`engine/tests/ledger/producer.py`, the end-to-end test's) under "test.edits",
imported on its first call, so only the preview's engine imports it and the other jobs' engines start as the
product's do. The plan therefore writes only into this test copy (the confirm is confined to the copy's folder, as
for every unpromoted mutator: `writes.promotion_refusal`, `writes.confinement`).

**One write**: open the copy (its mirror published; timed for the record), then a preview of a one-transaction insert
into the file that holds the most recent entry (dated that entry's date), its confirm, and the background rebuild the
landed save queues, each waited for in turn. Per run it reports each job's wall time from its submit to its answer
(the rebuild's from the save's answer to its own), the time from the confirm's submit to the new generation
published, the user-visible total from the preview's submit to that publish, each job's engine's peak resident size
(sampled every 5 ms, macOS `proc_pid_rusage`) and its lifetime peak footprint, and the 1-minute load average before
the run. A job that does not end as expected ends that run with its outcome (the report says which, e.g. a preview
refused); the script exits 0 and prints its JSON report. With `--runs N` the layouts alternate within each run.
"""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import hashlib
import json
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, override

from app.main import engine_command, parse_args
from app.platform.adapters.app_folder import AppFolder, prepare
from app.platform.adapters.engine_client import (
    ENGINE_RUN,
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    engine_env,
    venv_site,
)
from app.platform.adapters.engine_lock import wait_engine_lock
from app.platform.clock import FrozenClock
from app.platform.engine import EngineEnd
from app.platform.engine_jobs.opening import Opened, submit_open
from app.platform.engine_jobs.opening import OpenTarget as OpenJobTarget
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.rebuild import Loaded
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket, Outcome, Work
from app.platform.engine_supervision import RestartPolicy
from app.platform.observability.events import ENGINE_BYTECODE, ENGINE_STOPPED, Event
from app.platform.plans import PlanStore
from app.workspaces import writes
from app.workspaces.writes import CopyMark, Previewed, WriteServices, WriteTarget, mirror_generation

BACKEND = Path(__file__).resolve().parents[1]
ENGINE = BACKEND.parent / "engine"
ENGINE_VENV = ENGINE / ".venv"
VERSION = "0.0.0-measure"
MUTATOR = "test.edits"  # `engine/tests/ledger/producer.TEST_MUTATOR`
LINEAGE = "lin1"
CLOCK = FrozenClock(datetime(2026, 11, 14, 10, 0, tzinfo=UTC))  # the plans' and the publish's instant; fixed
JOB_DEADLINE = 600.0  # seconds; a job that has not answered by then is reported as stuck (not a budget)
SAMPLE_EVERY = 0.005
LAYOUTS = ("one", "files12", "meta20")
JOBS = ("preview", "save", "rebuild")
PARTS = 12

WITH_LAZY_TEST_PRODUCER = (
    f"import sys; sys.path.append({str(ENGINE)!r}); "
    "from ledger_engine.ledger import preview; "
    f"preview.PRODUCERS[{MUTATOR!r}] = lambda s, r: "
    "__import__('tests.ledger.producer', fromlist=['produce_json']).produce_json(s, r); " + ENGINE_RUN
)

DATED = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s")
TXN = re.compile(r"\d{4}-\d{2}-\d{2}\s+(\*|!|txn)\s")


class Measured(Exception):
    """A run that did not end as the measurement expects."""


def fail(what: str) -> NoReturn:
    raise Measured(what)


def ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered), 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
    }


# -- the layouts ------------------------------------------------------------------------------------------------------


def blocks(text: str) -> list[str]:
    """The text in blocks: each starts at a line in column 0 that is not blank and keeps the indented and blank lines
    after it."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if out and line[:1] not in (" ", "\t", "\n", "\r"):
            out.append(line)
        elif out:
            out[-1] += line
        else:
            out.append(line)
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class Layout:
    files: dict[str, str]  # relative path -> text; the root keeps the given root's name
    latest: str  # the file that holds the most recent dated entry
    latest_date: str


def _latest(files: dict[str, str]) -> tuple[str, str]:
    best = ("", "")
    for rel, text in files.items():
        for b in blocks(text):
            m = DATED.match(b)
            if m and m.group(0).strip() >= best[1]:
                best = (rel, m.group(0).strip())
    if not best[0]:
        fail("the ledger has no dated entry")
    return best


def layout_of(name: str, root_name: str, text: str) -> Layout:
    if name == "one":
        files = {root_name: text}
    elif name == "files12":
        header = [b for b in blocks(text) if not DATED.match(b)]
        dated = [b for b in blocks(text) if DATED.match(b)]
        root = "".join(header)
        root += ("" if root.endswith("\n") or not root else "\n") + '\ninclude "parts/*.beancount"\n'
        files = {root_name: root}
        n = len(dated)
        for k in range(PARTS):
            files[f"parts/{k + 1:02d}.beancount"] = "".join(dated[k * n // PARTS : (k + 1) * n // PARTS])
    elif name == "meta20":
        out: list[str] = []
        i = 0
        for b in blocks(text):
            if not TXN.match(b):
                out.append(b)
                continue
            first, _, rest = b.partition("\n")
            postings = [ln.split()[0] for ln in rest.splitlines() if ln[:1] in (" ", "\t") and ln.strip()]
            postings = [p for p in postings if not p.endswith(":") and not p.startswith(";")]
            m = DATED.match(b)
            assert m is not None  # noqa: S101 (TXN matched)
            meta = (
                f'  id: "{uuid.uuid5(uuid.NAMESPACE_URL, f"finzytrack-measure/{i}")}"\n'
                f'  content_hash: "{hashlib.sha256(f"{i}\0{b}".encode()).hexdigest()}"\n'
                f'  source_account: "{postings[-1] if postings else ""}"\n'
                f'  external_id: "{m.group(1)}{m.group(2)}{m.group(3)}{i:08d}"\n'
                '  external_id_type: "OFX"\n'
            )
            out.append(f"{first}\n{meta}{rest}")
            i += 1
        files = {root_name: "".join(out)}
    else:
        fail(f"no layout {name}")
    latest, date = _latest(files)
    return Layout(files, latest, date)


def stats(layout: Layout) -> dict[str, int | float]:
    texts = list(layout.files.values())
    return {
        "files": len(texts),
        "mb": round(sum(len(t.encode()) for t in texts) / 1e6, 2),
        "lines": sum(t.count("\n") for t in texts),
        "transactions": sum(1 for t in texts for b in blocks(t) if TXN.match(b)),
        "largest_file_mb": round(max(len(t.encode()) for t in texts) / 1e6, 2),
    }


# -- the engine's memory ----------------------------------------------------------------------------------------------


class Sampler:
    """Polls the running engine's resident size (and its lifetime peak footprint) every `SAMPLE_EVERY` seconds, by
    the label the main thread sets: the job running. A sample of an engine first seen under one label stays with it."""

    def __init__(self, client: EngineClient) -> None:
        self._client = client
        self._lib: ctypes.CDLL | None = ctypes.CDLL("/usr/lib/libproc.dylib") if sys.platform == "darwin" else None
        self._buf = ctypes.create_string_buffer(512)
        self.label: str | None = None
        self._owner: dict[int, str] = {}
        self.rss: dict[str, int] = {}
        self.footprint: dict[str, int] = {}
        self.engines: dict[str, set[int]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="measure-rss", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def reset(self) -> None:
        self.rss.clear()
        self.footprint.clear()
        self.engines.clear()

    def _read(self, pid: int) -> tuple[int, int] | None:
        if self._lib is None or self._lib.proc_pid_rusage(pid, 4, self._buf) != 0:  # RUSAGE_INFO_V4
            return None
        raw = self._buf.raw
        return struct.unpack_from("<Q", raw, 64)[0], struct.unpack_from("<Q", raw, 240)[0]  # resident, lifetime max

    def _run(self) -> None:
        while not self._stop.wait(SAMPLE_EVERY):
            pid, label = self._client.pid, self.label
            if pid is None:
                continue
            if pid not in self._owner:
                if label is None:
                    continue
                self._owner[pid] = label
            owner = self._owner[pid]
            got = self._read(pid)
            if got is None:
                continue
            self.engines.setdefault(owner, set()).add(pid)
            self.rss[owner] = max(self.rss.get(owner, 0), got[0])
            self.footprint[owner] = max(self.footprint.get(owner, 0), got[1])


# -- the wiring -------------------------------------------------------------------------------------------------------


class Jobs(EngineJobs):
    """The product's scheduler; every ticket it hands out is kept by its kind, so the rebuild a landed save queues
    (`rebuild_submitter`) can be waited for."""

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
    """The test-copy mark as the copy job records it (the end-to-end test's `recorded_mark`): the folder's real path
    and its identity, read by the engine's own file-system port in the engine's interpreter."""
    path = os.path.realpath(folder)
    code = (
        "import json, sys; from ledger_engine.fs.this_os import for_this_os; "
        "m = for_this_os().open_dir(sys.argv[-1]).mark(); "
        "print(json.dumps([m.volume, m.fs_type, m.born]))"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        [*bootstrap_argv(ENGINE_VENV / "bin" / "python", venv_site(ENGINE_VENV), code), path],
        capture_output=True,
        text=True,
        check=True,
        env=engine_env(os.environ),
    )
    volume, fs_type, born = json.loads(out.stdout)
    return CopyMark(path, str(volume), str(fs_type), int(born))


def command_for(folder: AppFolder, cache: Path) -> EngineCommand:
    """The product's engine command with the lazy test producer as its only change (the module's docstring)."""
    sidecar = parse_args(
        ["--app-local-data", str(folder.path), "--app-cache", str(cache), "--engine-venv", str(ENGINE_VENV)]
    )
    product = engine_command(sidecar, folder, VERSION)
    python, site = ENGINE_VENV / "bin" / "python", venv_site(ENGINE_VENV)
    if product.argv != bootstrap_argv(python, site, ENGINE_RUN):
        fail(f"the product's engine command is not the one this script changes: {product.argv}")
    return dataclasses.replace(product, argv=bootstrap_argv(python, site, WITH_LAZY_TEST_PRODUCER))


def waited(ticket: JobTicket | Failed | NotDone, what: str) -> Outcome:
    if not isinstance(ticket, JobTicket):
        fail(f"{what}: not queued: {ticket}")
    out = ticket.wait(JOB_DEADLINE)
    if out is None:
        fail(f"{what}: no answer in {JOB_DEADLINE:.0f} s")
    return out


# -- one run ----------------------------------------------------------------------------------------------------------


def one_run(
    jobs: Jobs,
    sampler: Sampler,
    folder: AppFolder,
    cache: Path,
    work: Path,
    layout: Layout,
    key: str,
    root_name: str,
    stops: list[str],
) -> dict[str, object]:
    copy = work / key / "copy"
    for rel, text in layout.files.items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_text(text, encoding="utf-8", newline="")
    copy = copy.resolve()
    root, latest = copy / root_name, copy / layout.latest
    result: dict[str, object] = {"load_average": round(os.getloadavg()[0], 2)}

    jobs.exclusive_began()
    t = time.perf_counter()
    opened = waited(submit_open(jobs, key, OpenJobTarget(key, str(root), LINEAGE, folder.path), clock=CLOCK), "open")
    result["open_ms"] = ms(time.perf_counter() - t)
    jobs.exclusive_ended()
    if not (isinstance(opened, Done) and isinstance(opened.value, Opened) and opened.value.mirror_ready):
        fail(f"open: {opened}")
    jobs.workspace_opened(key)
    target = WriteTarget(key, str(root), LINEAGE, folder.path, cache, copy=recorded_mark(copy))
    services = WriteServices(jobs=jobs, plans=PlanStore(CLOCK), clock=CLOCK, ids=_Ids(), session="m1")
    before = mirror_generation(target)
    if before is None:
        fail("no generation published after the open")
    txn = f'{layout.latest_date} * "Measured write"\n  Expenses:Food:Groceries  12.34 USD\n  Assets:Bank:Checking\n'
    request = {"edits": [{"kind": "insert", "text": txn, "default_file": str(latest)}]}
    jobs.tickets = []
    stops.clear()
    sampler.reset()

    def stop(outcome: str) -> dict[str, object]:
        sampler.label = None
        result["outcome"] = outcome
        if stops:
            result["engine_stops"] = list(stops)  # the client's `engine.stopped`: how the engine's output ended
        return result

    sampler.label = "preview"
    t0 = time.perf_counter()
    previewed = waited(writes.submit_preview(services, target, before, MUTATOR, request), "preview")
    t1 = time.perf_counter()
    result["preview_ms"] = ms(t1 - t0)
    if not (isinstance(previewed, Done) and isinstance(previewed.value, Previewed)):
        return stop(f"preview: {previewed}")
    plan = previewed.value
    result["plan_mb"] = round(sum(len(f.text.encode()) for f in plan.files) / 1e6, 2)
    if plan.new_errors or [f.path for f in plan.files] != [str(latest)]:
        return stop(f"preview: an unexpected plan: {len(plan.new_errors)} new errors, {len(plan.files)} files")

    sampler.label = "save"
    t2 = time.perf_counter()
    confirmed = writes.submit_confirm(services, target, plan.plan_id, plan.preview_hash, acknowledged=frozenset())
    if isinstance(confirmed, NotDone):
        return stop(f"confirm: {confirmed}")
    saved = waited(confirmed, "save")
    t3 = time.perf_counter()
    sampler.label = "rebuild"
    result["save_ms"] = ms(t3 - t2)
    if not (isinstance(saved, Done) and isinstance(saved.value, Saved)):
        return stop(f"save: {saved}")
    rebuilds = [ticket for kind, ticket in jobs.tickets if kind == "rebuild"]
    if len(rebuilds) != 1:
        return stop(f"rebuild: {len(rebuilds)} queued")
    rebuilt = waited(rebuilds[0], "rebuild")
    t4 = time.perf_counter()
    sampler.label = None
    result["rebuild_ms"] = ms(t4 - t3)
    if not (isinstance(rebuilt, Done) and isinstance(rebuilt.value, Loaded) and rebuilt.value.publish == "published"):
        return stop(f"rebuild: {rebuilt}")
    result["rebuild_tries"] = rebuilt.value.tries
    after = mirror_generation(target)
    if after is None or after <= before:
        return stop(f"rebuild: the generation is {after}, before it {before}")
    if latest.read_text(encoding="utf-8") != plan.files[0].text:
        return stop("save: the file does not hold the plan's text")
    result["confirm_to_published_ms"] = ms(t4 - t2)
    result["total_ms"] = ms(t4 - t0)
    for job in JOBS:
        result[f"{job}_peak_rss_mb"] = round(sampler.rss.get(job, 0) / 1e6, 1)
        result[f"{job}_peak_footprint_mb"] = round(sampler.footprint.get(job, 0) / 1e6, 1)
        result[f"{job}_engines"] = len(sampler.engines.get(job, ()))
    return stop("published")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--layout", action="append", choices=LAYOUTS)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args(argv)
    names: list[str] = args.layout or ["one"]
    src: Path = args.root.resolve()
    text = src.read_text(encoding="utf-8")
    layouts = {name: layout_of(name, src.name, text) for name in dict.fromkeys(names)}
    top = Path(tempfile.mkdtemp(prefix="ft-measure-write-"))
    jobs: Jobs | None = None
    client: EngineClient | None = None
    sampler: Sampler | None = None
    try:
        cache, work = top / "cache", top / "work"
        cache.mkdir()
        work.mkdir()
        folder = prepare(top / "app")
        if not isinstance(folder, AppFolder):
            fail(f"the app folder: {folder}")
        command = command_for(folder, cache)
        fill_failed: list[str] = []
        stops: list[str] = []

        def log(event: Event, /, **fields: object) -> None:
            if event is ENGINE_BYTECODE:
                fill_failed.append(str(fields))
            elif event is ENGINE_STOPPED:
                stops.append(f"{fields.get('kind')}: {str(fields.get('detail'))[:200]}")

        restart = RestartPolicy()

        def spawned(serial: int) -> None:
            assert jobs is not None  # noqa: S101 (bound before the first spawn)
            jobs.engine_spawned(serial)

        def ended(end: EngineEnd) -> None:
            assert jobs is not None  # noqa: S101 (bound before the first spawn)
            jobs.engine_ended(end)

        client = EngineClient(command, restart, log=log, on_engine_spawned=spawned, on_engine_ended=ended)
        jobs = Jobs(client, wait_engine_lock=lambda: wait_engine_lock(folder), restart=restart, log=log)
        jobs.tickets = []
        boot = jobs.start()
        if not isinstance(boot, JobTicket) or boot.wait(JOB_DEADLINE) is None:
            fail(f"the scheduler did not start: {boot}")
        bytecode = command.bytecode
        waited_from = time.perf_counter()
        while bytecode is not None and not bytecode.ready():  # the fill the first engine began
            if fill_failed:
                fail(f"the bytecode fill: {fill_failed}")
            if time.perf_counter() - waited_from > JOB_DEADLINE:
                fail("the bytecode fill did not finish")
            threading.Event().wait(0.05)  # a poll of the fill's mark, no sleep (EP-28)
        sampler = Sampler(client)
        sampler.start()
        runs: list[dict[str, object]] = []
        for n in range(args.runs):
            for name, layout in layouts.items():
                out = one_run(jobs, sampler, folder, cache, work, layout, f"k{n}{name}", src.name, stops)
                runs.append({"run": n, "layout": name, **out})
                shutil.rmtree(work / f"k{n}{name}", ignore_errors=True)
        keys = (*(f"{j}_ms" for j in JOBS), "confirm_to_published_ms", "total_ms", "open_ms")
        keys += tuple(f"{j}_peak_rss_mb" for j in JOBS)
        report = {
            "ledger": src.name,
            "path": "the product's: the sidecar's scheduler and write use cases, a fresh engine per job as it gives",
            "producer": "the engine's test producer, registered lazily by this script's engine command only",
            "layouts": {name: stats(layout) for name, layout in layouts.items()},
            "runs": runs,
            "summary": {
                name: {
                    k: summary([float(str(r[k])) for r in runs if r["layout"] == name and k in r])
                    for k in keys
                    if any(r["layout"] == name and k in r for r in runs)
                }
                for name in layouts
            },
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0
    finally:
        if sampler is not None:
            sampler.stop()
        if jobs is not None:
            jobs.close()
        if client is not None:
            client.close()
        shutil.rmtree(top, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
