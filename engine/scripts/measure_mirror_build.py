"""Measure the mirror build on a ledger (S0050; roadmap M1a; ADR-0015): the overlay load, the rows, the write.

usage (cwd = engine/): .venv/bin/python scripts/measure_mirror_build.py ROOT OUT_DIR [--runs N]
ROOT is the ledger's root file (the demo generator's 54k ledger: backend/scripts/gen_demo.py --size 54k), OUT_DIR an
empty folder for the mirror files. Prints one line per run and the peak resident memory of the process. The engine
reads no clock in product code (EP-10); this measurement script is not product code.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from datetime import date
from pathlib import Path

from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror.build import Built, Stamp, build
from ledger_engine.mirror.core import rows

STAMP = Stamp(ledger_version="0:measure", fingerprint="{}", built_at="1970-01-01T00:00:00+00:00")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("out")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--today", default="2026-10-28")
    args = parser.parse_args()
    today = date.fromisoformat(args.today)
    for run in range(args.runs):
        t0 = time.perf_counter()
        loaded = load(os.path.abspath(args.root), {}, DESKTOP)
        if not isinstance(loaded, Loaded):
            sys.exit(f"the load was refused: {loaded}")
        t1 = time.perf_counter()
        postings = len(rows.table_rows(loaded.entries, loaded.errors, loaded.options, today, STAMP)["postings"])
        t2 = time.perf_counter()
        out = Path(args.out) / f"mirror-{run}.db"
        result = build(loaded, str(out), today, STAMP)
        t3 = time.perf_counter()
        if not isinstance(result, Built):
            sys.exit(f"the build failed: {result}")
        sys.stdout.write(
            f"run {run}: entries {len(loaded.entries)} postings {postings}"
            f" load {t1 - t0:.2f}s rows {t2 - t1:.2f}s build (rows + write) {t3 - t2:.2f}s"
            f" file {out.stat().st_size / 1e6:.1f} MB\n"
        )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sys.stdout.write(f"peak RSS {peak / (1e6 if sys.platform == 'darwin' else 1e3):.0f} MB\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
