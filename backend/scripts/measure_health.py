#!/usr/bin/env python3
"""Measure how fast the main sidecar answers `GET /health`, idle and during a 54k load (the M1a exit; S0048).

usage (cwd = backend/):
    .venv/bin/python scripts/measure_health.py [--loads 3] [--size 54k]

Everything happens in a fresh temp folder (honours TMPDIR) on an OS-assigned loopback port (INV-24): the script
generates the demo ledger at the given size, starts the sidecar with its engine from `../engine/.venv`, and probes
`/health` with a new connection each time, 10 ms apart (as a health probe does): first idle, then while
`bean-check` from the engine's environment loads the ledger again and again in its own process, with Beancount's
pickle cache off (the engine never uses it, ADR-0040). That is the load the engine does for a save or a mirror
build, in the engine's process, not the sidecar's (ADR-0054; one load at a time, ADR-0064). It
prints a JSON report of the latencies in milliseconds (count, median, p95, p99, max) and the load times, and
removes the folder. It reports and never fails: the M1a exit's "under 100 ms" is read off the report.

With `--reads-mirror MIRROR` (a mirror file the engine built: `engine/scripts/measure_mirror_build.py`), the sidecar
does read work of its own (BL-0114, S0106): this script starts the sidecar as `app.main.main` in a process of its own
that also runs `--read-threads` worker threads (default 4), each running the mirror's reads and user queries
(`app.mirror.reader.Reader`, `app.mirror.query.QueryRunner`) back to back over a copy of MIRROR, as the reads will run
behind a route (ARCHITECTURE.md §2.5: on worker threads). The report gains a phase with the reads and no load
(`health_ms_reads_only`), the reads run through the load phase too, and `reads_done` counts the reads of each phase.
Limit: no route serves the mirror yet (BL-0126, M2), so a route's own work on the event loop (its request and the
JSON of its answer) is not in it; BL-0126 measures again with that route.
"""

from __future__ import annotations

import argparse
import http.client
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.main import main as sidecar_main
from app.mirror.query import QueryRunner, UserSql
from app.mirror.reader import Reader

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ENGINE_VENV = REPO / "engine" / ".venv"
ANCHOR = "2026-03-31"
# Probes are spaced, as a health probe is; unspaced, each new connection's TIME_WAIT uses up the loopback's
# ephemeral ports within seconds (EADDRNOTAVAIL). The spacing waits between probes, never inside one.
PACE_S = 0.01
AS_SIDECAR = "--as-sidecar-with-reads"
# The reads each worker thread runs in turn: the app's own reads and typical user queries over the compatibility
# views, the last one as large as the QueryRunner's caps let it be (the measure_mirror_reads.py mix).
USER_SQL = (
    "SELECT year_month, account, SUM(CAST(amount AS REAL)) FROM postings"
    " WHERE account LIKE 'Expenses:%' GROUP BY year_month, account",
    "SELECT * FROM account_balances",
    "SELECT * FROM postings",
)


def probe_once(port: int) -> float:
    """One `GET /health` on a new connection; milliseconds until the whole answer was read."""
    began = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            msg = f"/health answered {response.status}"
            raise RuntimeError(msg)
    finally:
        conn.close()
    return (time.perf_counter() - began) * 1000


def summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)

    def at(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)

    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered), 2),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1], 2),
    }


# -- the sidecar with reads of its own (a process of its own) ---------------------------------------------------------


def read_calls(mirror: str) -> list[Callable[[], object]]:
    runner = QueryRunner(mirror)
    reader = Reader(mirror)
    calls: list[Callable[[], object]] = [reader.info, lambda: reader.ledger_errors(200)]
    calls += [lambda q=q: runner.run_user_sql(UserSql(q), {}) for q in USER_SQL]
    return calls


def sidecar_with_reads(argv: list[str]) -> int:
    """`AS_SIDECAR MIRROR THREADS CONTROL -- <app.main's arguments>`: the read threads, then the sidecar's own main on
    this thread. The threads read while `CONTROL/on` exists; when it is gone they write the count of reads since it
    appeared to `CONTROL/count-<n>` (n = 1, 2, ...), which the measuring process waits for."""
    mirror, threads, control = argv[0], int(argv[1]), Path(argv[2])
    rest = argv[argv.index("--") + 1 :]
    done = [0] * threads
    stop = threading.Event()

    def read(i: int) -> None:
        calls = read_calls(mirror)
        while not stop.is_set():
            if not (control / "on").exists():
                stop.wait(0.005)
                continue
            for call in calls:
                call()
                done[i] += 1

    def report() -> None:
        phase = 0
        while not stop.is_set():
            while not (control / "on").exists():
                stop.wait(0.005)
            start = sum(done)
            while (control / "on").exists():
                stop.wait(0.005)
            phase += 1
            (control / f"count-{phase}").write_text(str(sum(done) - start))

    for i in range(threads):
        threading.Thread(target=read, args=(i,), name=f"measure-read-{i}", daemon=True).start()
    threading.Thread(target=report, name="measure-report", daemon=True).start()
    try:
        return sidecar_main(rest)
    finally:
        stop.set()


# -- the measuring process ---------------------------------------------------------------------------------------------


def reads_phase(control: Path | None, on: bool, phase: int) -> int | None:
    """Turn the sidecar's reads on, or off and return how many ran since they were turned on."""
    if control is None:
        return None
    if on:
        (control / "on").write_text("")
        return None
    (control / "on").unlink()
    count = control / f"count-{phase}"
    wait = threading.Event()
    while not count.exists():
        wait.wait(0.01)
    return int(count.read_text())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [AS_SIDECAR]:
        return sidecar_with_reads(argv[1:])
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--loads", type=int, default=3, help="how many full loads to probe through")
    ap.add_argument("--size", default="54k", choices=["demo", "5k", "54k"])
    ap.add_argument("--idle", type=int, default=500, help="probes before the load (and with the reads alone)")
    ap.add_argument("--reads-mirror", type=Path, help="a mirror file the sidecar reads during the measurement")
    ap.add_argument("--read-threads", type=int, default=4)
    args = ap.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="ft-measure-health-"))
    sidecar: subprocess.Popen[str] | None = None
    try:
        ledger, local, cache = root / "ledger", root / "local", root / "cache"
        local.mkdir()
        cache.mkdir()
        subprocess.run(  # noqa: S603 (fixed argv: this interpreter running the repo's generator)
            [sys.executable, str(BACKEND / "scripts/gen_demo.py"), "--size", args.size, "--out", str(ledger),
             "--anchor", ANCHOR],
            cwd=BACKEND, check=True, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        sidecar_args = ["--app-local-data", str(local), "--app-cache", str(cache), "--port", "0",
                        "--engine-venv", str(ENGINE_VENV)]  # fmt: skip
        control: Path | None = None
        if args.reads_mirror is None:
            argv_sidecar = [sys.executable, "-m", "app.main", *sidecar_args]
        else:
            control = root / "control"
            control.mkdir()
            mirror = root / "mirror.db"
            shutil.copyfile(args.reads_mirror, mirror)  # the reads open a copy, never the file given
            argv_sidecar = [sys.executable, str(Path(__file__).resolve()), AS_SIDECAR, str(mirror),
                            str(args.read_threads), str(control), "--", *sidecar_args]  # fmt: skip
        sidecar = subprocess.Popen(  # noqa: S603 (fixed argv: the sidecar under measurement)
            argv_sidecar,
            cwd=BACKEND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
        if sidecar.stdout is None:
            msg = "the sidecar has no stdout pipe"
            raise RuntimeError(msg)
        port = int(json.loads(sidecar.stdout.readline().removeprefix("FT_READY "))["port"])
        for _ in range(20):  # warm-up: imports, first connections
            probe_once(port)
        pause = threading.Event()  # never set: its wait is the pause between two probes
        report: dict[str, object] = {"size": args.size}

        def probe_for(count: int) -> list[float]:
            samples: list[float] = []
            for _ in range(count):
                samples.append(probe_once(port))
                pause.wait(PACE_S)
            return samples

        report["health_ms_idle"] = summary(probe_for(args.idle))
        reads_done: dict[str, int | None] = {}
        if control is not None:
            reads_phase(control, on=True, phase=1)
            report["health_ms_reads_only"] = summary(probe_for(args.idle))
            reads_done["reads_only"] = reads_phase(control, on=False, phase=1)

        loads: list[float] = []
        failed: list[int] = []  # each load's exit code: a load that failed measured nothing
        during: list[float] = []
        done = threading.Event()

        def load_repeatedly() -> None:
            for _ in range(args.loads):
                began = time.perf_counter()
                checked = subprocess.run(  # noqa: S603 (fixed argv: Beancount's checker from the engine's environment)
                    [str(ENGINE_VENV / "bin" / "bean-check"), "--no-cache", str(ledger / "main.beancount")],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
                loads.append(round(time.perf_counter() - began, 2))
                failed.append(checked.returncode)
            done.set()

        reads_phase(control, on=True, phase=2)
        loader = threading.Thread(target=load_repeatedly)
        loader.start()
        while not done.is_set():
            during.append(probe_once(port))
            done.wait(PACE_S)
        loader.join()
        if any(failed):
            msg = f"bean-check did not load the ledger (exit codes {failed}): nothing was measured"
            raise RuntimeError(msg)
        if control is not None:
            reads_done["during_load"] = reads_phase(control, on=False, phase=2)
            report["read_threads"] = args.read_threads
            report["reads_done"] = reads_done
        report["health_ms_during_load"] = summary(during)
        report["load_seconds"] = loads
        report["python"] = sys.version.split()[0]
        report["platform"] = sys.platform
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.terminate()
            sidecar.wait(timeout=60)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
