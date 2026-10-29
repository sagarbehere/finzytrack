"""A small mirror file for the read tests, made from the build's own DDL snapshot (`contracts/mirror-ddl.sql`, generated
from `ledger_engine.mirror.core.schema`, S0050). The main sidecar never imports the engine (ADR-0054), so the tests
read the contract the engine's build is checked against; the rows are synthetic (INV-23).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, override

import pytest

DDL = Path(__file__).resolve().parents[3] / "contracts" / "mirror-ddl.sql"

META: tuple[tuple[str, str], ...] = (
    ("surface_version", "1"),
    ("ledger_version", "7:abc"),
    ("fingerprint", "{}"),
    ("export_version", "e1"),
    ("built_at", "2026-10-28T09:00:00+00:00"),
    ("build_complete", "0"),
)

# (account, number, currency, narration): the money is TEXT, as the build writes it (ADR-0023).
POSTINGS: tuple[tuple[str, str, str, str], ...] = (
    ("Assets:Bank", "-10.50", "INR", "Groceries"),
    ("Expenses:Food", "10.50", "INR", "Groceries"),
    ("Assets:Bank", "1000", "INR", "Salary"),
    ("Income:Salary", "-1000", "INR", "Salary"),
)

# (source_file, line_number, message, entry_json, error_type)
ERRORS: tuple[tuple[str | None, int | None, str, str | None, str], ...] = (
    ("/ledger/main.beancount", 3, "Balance failed for 'Assets:Bank'", '["2026-01-01"]', "BalanceError"),
    ("/ledger/main.beancount", None, "Unbalanced <b>transaction</b>", None, "ValidationError"),
    (None, 0, "Plugin missing", None, "LoadError"),
)


def make_mirror(
    path: Path,
    *,
    meta: Sequence[tuple[str, str]] = META,
    errors: Sequence[tuple[str | None, int | None, str, str | None, str]] = ERRORS,
) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript(DDL.read_text(encoding="utf-8"))
        con.executemany(
            "INSERT INTO m_postings (posting_id, account, amount, currency, transaction_narration)"
            " VALUES (?, ?, ?, ?, ?)",
            [(i + 1, *row) for i, row in enumerate(POSTINGS)],
        )
        con.executemany(
            "INSERT INTO m_ledger_errors (id, source_file, line_number, message, entry_json, error_type)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(i + 1, *row) for i, row in enumerate(errors)],
        )
        con.executemany("INSERT INTO m_meta (key, value) VALUES (?, ?)", meta)
        con.commit()
    finally:
        con.close()
    return path


def starve(monkeypatch: pytest.MonkeyPatch, *, at: str) -> None:
    """SQLite out of its heap at one step (Python's sqlite3 raises `MemoryError` for SQLITE_NOMEM), as when the
    queries running at the same moment have used up the process-wide hard heap limit (verifier S0052 R2-F1).

    `at` is where: "connect" (opening the mirror file), "connect :memory:" (the resolver's own connection), or the
    start of a statement's text (that statement raises; the others run)."""
    real = sqlite3.connect

    class Starved(sqlite3.Connection):
        @override
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith(at):
                raise MemoryError
            return super().execute(sql, parameters)

    def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        memory = database == ":memory:"
        if (at == "connect" and not memory) or (at == "connect :memory:" and memory):
            raise MemoryError
        return real(database, *args, factory=Starved, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


SWEEP = """
import json, sqlite3, sys
from pathlib import Path
from app.mirror.query import QueryLimits, QueryRunner, UserSql
from app.mirror.reader import Reader
from tests.mirror.support import make_mirror
mirror = str(make_mirror(Path(sys.argv[1]) / "m.db"))
runner = QueryRunner(mirror, QueryLimits(max_sqlite_heap_bytes=16 * 1024 * 1024))
calls = {
    "query": lambda: runner.run_user_sql(UserSql("WITH c AS (SELECT 1) SELECT count(*) FROM c, postings")),
    "info": lambda: Reader(mirror).info(),
    "errors": lambda: Reader(mirror).ledger_errors(10),
}
seen = {name: set() for name in calls}
# The heap limit is process-wide and only ever lowered: from where every call answers down to 1 byte, so each
# step of each call meets the limit at some point.
control = sqlite3.connect(":memory:")  # opened while there is room, to set the limit at each step
for limit in range(600_000, 0, -1024):
    try:
        control.execute(f"PRAGMA hard_heap_limit = {limit}")
    except MemoryError:  # the limit is under what SQLite holds already: nothing more can be learnt
        break
    for name, call in calls.items():
        try:
            r = call()
            seen[name].add(type(r).__name__ + ":" + str(getattr(r, "reason", "")))
        except BaseException as e:
            seen[name].add("raised " + type(e).__name__)
print(json.dumps({name: sorted(s) for name, s in seen.items()}))
"""


def heap_sweep(folder: Path) -> dict[str, list[str]]:
    """Every outcome kind of a query and the two reads as SQLite's process-wide heap shrinks under them (as when
    other queries hold it), in a fresh interpreter: the limit cannot be raised again."""
    backend = Path(__file__).resolve().parents[2]
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{backend / 'src'}:{backend}"}
    out = subprocess.run(  # noqa: S603 (this interpreter, constant code)
        [sys.executable, "-c", SWEEP, str(folder)], capture_output=True, text=True, check=True, env=env, cwd=backend
    )
    return json.loads(out.stdout)
