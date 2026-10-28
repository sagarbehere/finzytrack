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

Limit: today the sidecar does no work of its own during a load (no mirror reads yet), so this measures the
probe against a busy machine, not against a busy sidecar; it is measured again once the read core lands (BL-0114).
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
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ENGINE_VENV = REPO / "engine" / ".venv"
ANCHOR = "2026-03-31"
# Probes are spaced, as a health probe is; unspaced, each new connection's TIME_WAIT uses up the loopback's
# ephemeral ports within seconds (EADDRNOTAVAIL). The spacing waits between probes, never inside one.
PACE_S = 0.01


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--loads", type=int, default=3, help="how many full loads to probe through")
    ap.add_argument("--size", default="54k", choices=["demo", "5k", "54k"])
    ap.add_argument("--idle", type=int, default=500, help="probes before the load")
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
        sidecar = subprocess.Popen(  # noqa: S603 (fixed argv: the sidecar under measurement)
            [sys.executable, "-m", "app.main", "--app-local-data", str(local), "--app-cache", str(cache),
             "--port", "0", "--engine-venv", str(ENGINE_VENV)],
            cwd=BACKEND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
        if sidecar.stdout is None:
            msg = "the sidecar has no stdout pipe"
            raise RuntimeError(msg)
        port = int(json.loads(sidecar.stdout.readline().removeprefix("FT_READY "))["port"])
        for _ in range(20):  # warm-up: imports, first connections
            probe_once(port)
        pause = threading.Event()  # never set: its wait is the pause between two probes
        idle: list[float] = []
        for _ in range(args.idle):
            idle.append(probe_once(port))
            pause.wait(PACE_S)

        loads: list[float] = []
        during: list[float] = []
        done = threading.Event()

        def load_repeatedly() -> None:
            for _ in range(args.loads):
                began = time.perf_counter()
                subprocess.run(  # noqa: S603 (fixed argv: Beancount's checker from the engine's environment)
                    [str(ENGINE_VENV / "bin" / "bean-check"), "--no-cache", str(ledger / "main.beancount")],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
                loads.append(round(time.perf_counter() - began, 2))
            done.set()

        loader = threading.Thread(target=load_repeatedly)
        loader.start()
        while not done.is_set():
            during.append(probe_once(port))
            done.wait(PACE_S)
        loader.join()
        report = {
            "size": args.size,
            "health_ms_idle": summary(idle),
            "health_ms_during_load": summary(during),
            "load_seconds": loads,
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0
    finally:
        if sidecar is not None and sidecar.poll() is None:
            sidecar.terminate()
            sidecar.wait(timeout=60)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
