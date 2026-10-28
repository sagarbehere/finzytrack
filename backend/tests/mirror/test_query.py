"""The one hardened runner for user SQL over the mirror (ADR-0016; ARCHITECTURE.md §2.2 `mirror/query.py`; L1-10,
L5-07; INV-09 "query engines are confined").

A read-only open alone is not enough (ADR-0016): on a `mode=ro` connection SQLite still runs `ATTACH`, `PRAGMA`,
`CREATE TEMP TABLE` and `VACUUM INTO`, which writes a new file anywhere the process may write (spike, S0052
journal). Each refusal below therefore checks that nothing was written, next to the near miss that must answer.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.mirror.files import MirrorUnavailable
from app.mirror.query import QueryLimits, QueryRefused, QueryRows, QueryRunner, UserSql, confine
from tests.mirror.support import make_mirror

pytestmark = pytest.mark.inv("INV-09")


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    folder = tmp_path / "gen"
    folder.mkdir()
    return make_mirror(folder / "mirror.db")


def run(path: Path, sql: str, limits: QueryLimits | None = None, **kwargs: object) -> object:
    return QueryRunner(str(path), limits).run_user_sql(UserSql(sql), **kwargs)  # type: ignore[arg-type]


def answered(result: object) -> QueryRows:
    assert isinstance(result, QueryRows), result
    return result


def refused(result: object, reason: str) -> QueryRefused:
    assert isinstance(result, QueryRefused), result
    assert result.reason == reason, result
    return result


def snapshot(folder: Path) -> dict[str, str]:
    """Every file under `folder` with its content hash: a refused query changes nothing and adds nothing."""
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*")}


# ---------------------------------------------------------------- what answers


def test_a_select_over_public_compatibility_and_base_names_answers(mirror: Path) -> None:
    for table in ("r_postings", "postings", "m_postings"):
        rows = answered(run(mirror, f"SELECT account, amount FROM {table} ORDER BY posting_id"))  # noqa: S608
        assert rows.columns == ("account", "amount")
        assert rows.rows[0] == ("Assets:Bank", "-10.50")  # money stays TEXT, as written (INV-06)
        assert rows.truncated is None


def test_every_table_and_view_of_the_surface_is_readable(mirror: Path) -> None:
    """The population: every object of the build's DDL snapshot (18 base tables, 17 `r_*` views, 18 compatibility
    views), read from the file, not from a list here."""
    con = sqlite3.connect(mirror)
    try:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_schema WHERE type IN ('table', 'view')")]
    finally:
        con.close()
    assert len(names) == 53
    for name in names:
        answered(run(mirror, f"SELECT * FROM {name.upper()} LIMIT 1"))  # noqa: S608 (names from the file)


def test_parameters_are_bound_by_name(mirror: Path) -> None:
    sql = "SELECT amount FROM postings WHERE account = :account ORDER BY posting_id"
    rows = answered(run(mirror, sql, params={"account": "Assets:Bank"}))
    assert rows.rows == (("-10.50",), ("1000",))
    # A value is never SQL: a quote in it is just a character.
    assert answered(run(mirror, sql, params={"account": "x' OR '1'='1"})).rows == ()


def test_ctes_windows_json_and_the_schema_table_answer(mirror: Path) -> None:
    sql = """
        WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3),
             flows AS (SELECT account, SUM(CAST(amount AS REAL)) AS total FROM postings GROUP BY account)
        SELECT (SELECT COUNT(*) FROM n), account, total, COUNT(*) OVER (),
               (SELECT COUNT(*) FROM json_each('[1, 2]')), (SELECT COUNT(*) FROM sqlite_schema)
        FROM flows ORDER BY account
    """
    rows = answered(run(mirror, sql)).rows
    assert len(rows) == 3
    assert rows[0][:4] == (3, "Assets:Bank", 989.5, 3)
    assert rows[0][4] == 2
    assert rows[0][5] == 53  # the 18 base tables, 17 `r_*` views and 18 compatibility views


def test_a_trailing_semicolon_or_comment_is_one_statement(mirror: Path) -> None:
    assert answered(run(mirror, "SELECT 1; ")).rows == ((1,),)
    assert answered(run(mirror, "SELECT 1; -- done")).rows == ((1,),)


# ---------------------------------------------------------------- what is refused, with nothing written

REFUSED = (
    "PRAGMA table_info(m_postings)",
    "PRAGMA query_only = 0",
    "SELECT * FROM pragma_table_info('m_postings')",
    "SELECT name FROM PRAGMA_TABLE_LIST",
    "ATTACH DATABASE ':memory:' AS other",
    "ATTACH DATABASE '{dir}/attached.db' AS other",
    "VACUUM INTO '{dir}/copy.db'",
    "VACUUM",
    "CREATE TEMP TABLE t (a)",
    "CREATE TEMP VIEW v AS SELECT 1",
    "CREATE TABLE t (a)",
    "INSERT INTO m_meta VALUES ('k', 'v')",
    "WITH x AS (SELECT 1) INSERT INTO m_meta SELECT 'k', 'v' FROM x",
    "UPDATE m_meta SET value = 'x'",
    "DELETE FROM m_meta",
    "DROP VIEW postings",
    "BEGIN",
    "SAVEPOINT s",
    "ANALYZE",
    "REINDEX",
    "-- only a comment",
    "SELECT load_extension('{dir}/x')",
    "SELECT LOAD_EXTENSION('{dir}/x')",
    "SELECT fts3_tokenizer('simple')",
    "SELECT * FROM dbstat",
    "SELECT * FROM DBSTAT",
    "SELECT * FROM temp.sqlite_master",
    "SELECT * FROM sqlite_temp_master",
)


@pytest.mark.parametrize("template", REFUSED)
def test_a_statement_outside_the_allowlist_is_refused_and_writes_nothing(mirror: Path, template: str) -> None:
    folder = mirror.parent
    before = snapshot(folder)
    result = refused(run(mirror, template.format(dir=folder)), "not_allowed")
    assert result.message
    assert snapshot(folder) == before


def test_a_second_statement_never_runs(mirror: Path) -> None:
    folder = mirror.parent
    result = refused(run(mirror, f"SELECT 1; VACUUM INTO '{folder}/copy.db'"), "invalid")
    assert "one statement" in result.message
    assert not (folder / "copy.db").exists()


def test_confine_sets_the_limits_on_the_connection(mirror: Path) -> None:
    limits = QueryLimits(max_value_bytes=4096)
    con = sqlite3.connect(mirror)
    try:
        confine(con, limits, frozenset({"m_meta"}))
        assert con.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
        assert con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == 4096
    finally:
        con.close()


# ---------------------------------------------------------------- the time limit, the size limits

ENDLESS = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 50000000) SELECT COUNT(*) FROM n"


def ticking() -> tuple[Callable[[], float], Iterator[int]]:
    """A monotonic clock that moves one second each time it is read, and a counter of the reads."""
    reads = 0

    def clock() -> float:
        nonlocal reads
        reads += 1
        return float(reads)

    def count() -> Iterator[int]:
        while True:
            yield reads

    return clock, count()


def test_a_query_past_its_time_limit_is_stopped(mirror: Path) -> None:
    clock, reads = ticking()
    runner = QueryRunner(str(mirror), QueryLimits(timeout_s=5, progress_steps=1000), monotonic=clock)
    refused(runner.run_user_sql(UserSql(ENDLESS)), "timed_out")
    # Stopped by SQLite's progress handler within a few checks, not after 50 million rows.
    assert next(reads) < 10


def test_a_query_within_its_time_limit_answers(mirror: Path) -> None:
    clock, _ = ticking()
    runner = QueryRunner(str(mirror), QueryLimits(timeout_s=5, progress_steps=1000), monotonic=clock)
    assert answered(runner.run_user_sql(UserSql("SELECT COUNT(*) FROM postings"))).rows == ((4,),)


def test_a_value_past_the_length_limit_is_refused(mirror: Path) -> None:
    limits = QueryLimits(max_value_bytes=1000)
    refused(run(mirror, "SELECT zeroblob(5000)", limits), "too_large")
    refused(run(mirror, "SELECT hex(zeroblob(600))", limits), "too_large")  # 1200 characters
    assert answered(run(mirror, "SELECT length(printf('%.*c', 900, 'x'))", limits)).rows == ((900,),)


def test_the_row_cap_truncates_and_says_so(mirror: Path) -> None:
    sql = "SELECT posting_id FROM postings ORDER BY posting_id"
    capped = answered(run(mirror, sql, QueryLimits(max_rows=2)))
    assert capped.rows == ((1,), (2,))
    assert capped.truncated == "rows"
    exact = answered(run(mirror, sql, QueryLimits(max_rows=4)))
    assert len(exact.rows) == 4
    assert exact.truncated is None


def test_a_caller_may_lower_the_row_cap_but_not_raise_it(mirror: Path) -> None:
    sql = "SELECT posting_id FROM postings"
    assert len(answered(run(mirror, sql, QueryLimits(max_rows=3), max_rows=1)).rows) == 1
    assert len(answered(run(mirror, sql, QueryLimits(max_rows=3), max_rows=100)).rows) == 3
    with pytest.raises(ValueError, match="max_rows"):
        run(mirror, sql, None, max_rows=0)


def test_the_result_size_cap_truncates_and_says_so(mirror: Path) -> None:
    sql = "SELECT printf('%.*c', 400, 'x') FROM postings"
    capped = answered(run(mirror, sql, QueryLimits(max_result_bytes=1000)))
    assert len(capped.rows) == 2  # 400 + 400 fit; the third would pass 1000
    assert capped.truncated == "bytes"
    assert answered(run(mirror, sql, QueryLimits(max_result_bytes=1600))).truncated is None


@pytest.mark.parametrize(
    "limits",
    [
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"max_rows": 0},
        {"max_value_bytes": 0},
        {"max_result_bytes": 0},
        {"progress_steps": 0},
    ],
)
def test_limits_must_be_positive_and_finite(limits: dict[str, float]) -> None:
    with pytest.raises(ValueError, match=next(iter(limits))):
        QueryLimits(**limits)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the user's mistakes and a missing mirror


@pytest.mark.parametrize(
    ("sql", "params", "says"),
    [
        ("SELEC 1", None, "syntax error"),
        ("SELECT * FROM nowhere", None, "no such table"),
        ("SELECT :missing", None, "missing"),
        ("SELECT :a", {"b": 1}, "a"),
        ("", None, "no statement"),
    ],
)
def test_a_mistake_in_the_query_is_invalid_with_a_message(
    mirror: Path, sql: str, params: dict[str, object] | None, says: str
) -> None:
    result = refused(run(mirror, sql, params=params), "invalid")
    assert says in result.message


def test_a_missing_file_is_unavailable_and_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "gone.db"
    result = run(path, "SELECT 1")
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "cannot_open"
    assert not path.exists()


def test_a_file_that_is_not_a_database_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "text.db"
    path.write_text("not a database " * 100)
    result = run(path, "SELECT 1")
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"


def test_a_path_with_uri_characters_opens_that_file(tmp_path: Path) -> None:
    folder = tmp_path / "a?mode=rwc#b %41"
    folder.mkdir()
    path = make_mirror(folder / "m.db")
    assert answered(run(path, "SELECT COUNT(*) FROM postings")).rows == ((4,),)
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder.name]


def test_a_relative_path_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="absolute"):
        QueryRunner("mirror.db")


def test_the_function_denylist_was_reviewed_for_this_sqlite() -> None:
    """Every built-in function but `load_extension` and `fts3_tokenizer` only computes (SQLite 3.53.1, reviewed in
    S0052). A new SQLite may add functions: review `DENIED_FUNCTIONS` against its release notes, then move this pin."""
    assert sqlite3.sqlite_version == "3.53.1"
