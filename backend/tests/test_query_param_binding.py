"""Recipe SQL parameters are BOUND by the database, not string-substituted (G9).

These pin the safe behavior of `_execute_sqlite_query`: values passed as
`parameters` are bound to :name placeholders, so a value can never be parsed as
SQL (no injection), and an omitted/None parameter set runs the query verbatim.
"""

import sqlite3
from pathlib import Path

import pytest

from app.api.routers.query import _execute_sqlite_query


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    p = tmp_path / "mirror.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE postings (account TEXT, amount TEXT, currency TEXT)")
    con.executemany(
        "INSERT INTO postings VALUES (?, ?, ?)",
        [
            ("Expenses:Food", "10.00", "USD"),
            ("Expenses:Rent", "900.00", "USD"),
            ("Expenses:Food", "500.00", "INR"),
        ],
    )
    con.commit()
    con.close()
    return str(p)


def test_bound_parameter_filters_correctly(db_path):
    res = _execute_sqlite_query(
        db_path,
        "SELECT account FROM postings WHERE currency = :currency ORDER BY account",
        {"currency": "USD"},
    )
    assert [r["account"] for r in res["rows"]] == ["Expenses:Food", "Expenses:Rent"]


def test_injection_value_is_treated_as_data_not_sql(db_path):
    # A classic injection payload as the parameter value. If it were substituted
    # into the SQL text it would return every row; bound, it matches nothing.
    res = _execute_sqlite_query(
        db_path,
        "SELECT account FROM postings WHERE currency = :currency",
        {"currency": "USD' OR '1'='1"},
    )
    assert res["rows"] == []
    assert res["row_count"] == 0


def test_value_with_apostrophe_binds_safely(db_path):
    # A legitimate value containing a quote must not break the query.
    res = _execute_sqlite_query(
        db_path,
        "SELECT account FROM postings WHERE account = :acct",
        {"acct": "O'Brien:Fees"},
    )
    assert res["rows"] == []  # no such account, but no SQL error either


def test_unreferenced_parameters_are_ignored(db_path):
    # Named binding only binds what the query references; extra keys are fine.
    res = _execute_sqlite_query(
        db_path,
        "SELECT account FROM postings WHERE currency = :currency",
        {"currency": "INR", "unused": "x", "year": 2025},
    )
    assert [r["account"] for r in res["rows"]] == ["Expenses:Food"]


def test_none_parameters_runs_query_verbatim(db_path):
    res = _execute_sqlite_query(db_path, "SELECT COUNT(*) AS n FROM postings", None)
    assert res["rows"][0]["n"] == 3
