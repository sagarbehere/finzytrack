"""A small mirror file for the read tests, made from the build's own DDL snapshot (`contracts/mirror-ddl.sql`, generated
from `ledger_engine.mirror.core.schema`, S0050). The main sidecar never imports the engine (ADR-0054), so the tests
read the contract the engine's build is checked against; the rows are synthetic (INV-23).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

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
