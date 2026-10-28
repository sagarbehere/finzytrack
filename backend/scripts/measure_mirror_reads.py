#!/usr/bin/env python3
"""Measure the mirror's reads and the hardened QueryRunner on a built mirror file (S0052; BL-0122; ADR-0016).

usage (cwd = backend/):
    .venv/bin/python scripts/measure_mirror_reads.py MIRROR [--runs 20] [--indexes]

MIRROR is a mirror file the engine built (`engine/scripts/measure_mirror_build.py` on the demo generator's 54k
ledger). It is only read. Prints a JSON report in milliseconds (median and max over the runs) for: a query that
does nothing (the cost of opening and confining a connection), `Reader.info`, `Reader.ledger_errors`, and typical
user queries over the compatibility views, each with the time limit's progress handler at its default spacing and
nearly off, to show what the working timeout costs. With `--indexes`, the same queries run again on a copy in a
fresh temp folder (honours TMPDIR, removed afterwards) that has the candidate indexes of BL-0122, so the gain of
each index can be read off before the build adds it. It reports and never fails.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from app.mirror.files import MirrorUnavailable
from app.mirror.query import QueryLimits, QueryRefused, QueryRows, QueryRunner, UserSql
from app.mirror.reader import Reader

QUERIES: dict[str, tuple[str, dict[str, str | int | None]]] = {
    "nothing": ("SELECT 1", {}),
    "monthly_expenses": (
        "SELECT year_month, account, SUM(CAST(amount AS REAL)) FROM postings"
        " WHERE account LIKE 'Expenses:%' GROUP BY year_month, account",
        {},
    ),
    "one_account_one_year": (
        "SELECT transaction_date, amount, currency FROM postings"
        " WHERE account = :account AND transaction_date BETWEEN :start AND :end ORDER BY transaction_date",
        {"start": "2025-01-01", "end": "2025-12-31"},
    ),
    "one_transaction": ("SELECT * FROM postings WHERE transaction_id = :id", {}),
    "account_balances": ("SELECT * FROM account_balances", {}),
    "every_posting_capped": ("SELECT * FROM postings", {}),
}
INDEXES = (
    "CREATE INDEX ix_postings_account_date ON m_postings (account, transaction_date)",
    "CREATE INDEX ix_postings_transaction ON m_postings (transaction_id)",
)


def timed(fn: Callable[[], object], runs: int) -> tuple[dict[str, float], object]:
    samples: list[float] = []
    result: object = None
    for _ in range(runs):
        began = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - began) * 1000)
    return {"median": round(statistics.median(samples), 2), "max": round(max(samples), 2)}, result


def shape(result: object) -> str:
    if isinstance(result, QueryRows):
        return f"{len(result.rows)} rows" + (f", truncated by {result.truncated}" if result.truncated else "")
    if isinstance(result, QueryRefused | MirrorUnavailable):
        return f"{type(result).__name__}: {result}"
    return type(result).__name__


def measure_queries(path: str, runs: int, params: dict[str, dict[str, str | int | None]]) -> dict[str, object]:
    report: dict[str, object] = {}
    for spacing_name, steps in (("default", QueryLimits().progress_steps), ("nearly_off", 1_000_000_000)):
        runner = QueryRunner(path, QueryLimits(progress_steps=steps))
        for name, (sql, _) in QUERIES.items():
            times, result = timed(lambda r=runner, s=sql, n=name: r.run_user_sql(UserSql(s), params[n]), runs)
            report[f"{name} ({spacing_name})"] = {**times, "result": shape(result)}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mirror")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--indexes", action="store_true")
    args = ap.parse_args(argv)
    path = str(Path(args.mirror).resolve())
    con = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)
    try:  # the measurement's own inputs: an account with many postings and one transaction id
        (account,) = con.execute("SELECT account FROM m_postings GROUP BY account ORDER BY COUNT(*) DESC").fetchone()
        (txn,) = con.execute("SELECT transaction_id FROM m_postings WHERE posting_id = 1000").fetchone()
    finally:
        con.close()
    params = {name: dict(p) for name, (_, p) in QUERIES.items()}
    params["one_account_one_year"]["account"] = account
    params["one_transaction"]["id"] = txn
    reader = Reader(path)
    report: dict[str, object] = {"mirror_mb": round(Path(path).stat().st_size / 1e6, 1), "runs": args.runs}
    report["reader.info"], _ = timed(reader.info, args.runs)
    errors_times, errors = timed(lambda: reader.ledger_errors(200), args.runs)
    report["reader.ledger_errors(200)"] = {**errors_times, "result": type(errors).__name__}
    report["queries"] = measure_queries(path, args.runs, params)
    if args.indexes:
        folder = Path(tempfile.mkdtemp(prefix="ft-measure-reads-"))
        try:
            copy = folder / "indexed.db"
            shutil.copyfile(path, copy)
            con = sqlite3.connect(copy)
            began = time.perf_counter()
            for statement in INDEXES:
                con.execute(statement)
            con.commit()
            con.close()
            report["indexes_build_ms"] = round((time.perf_counter() - began) * 1000, 1)
            report["indexed_mirror_mb"] = round(copy.stat().st_size / 1e6, 1)
            report["queries_with_indexes"] = measure_queries(str(copy), args.runs, params)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    report["python"] = sys.version.split()[0]
    report["sqlite"] = sqlite3.sqlite_version
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
