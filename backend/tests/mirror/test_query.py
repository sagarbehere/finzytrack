"""The one hardened runner for user SQL over the mirror (ADR-0016; ARCHITECTURE.md §2.2 `mirror/query.py`; L1-10,
L5-07; INV-09 "query engines are confined").

A read-only open alone is not enough (ADR-0016): on a `mode=ro` connection SQLite still runs `ATTACH`, `PRAGMA`,
`CREATE TEMP TABLE` and `VACUUM INTO`, which writes a new file anywhere the process may write (spike, S0052
journal). Each refusal below therefore checks that nothing was written, next to the near miss that must answer.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.mirror.files import MirrorUnavailable
from app.mirror.query import (
    QueryLimits,
    QueryRefused,
    QueryRows,
    QueryRunner,
    UserSql,
    _Resolver,  # pyright: ignore[reportPrivateUsage]
    confine,
    row_bound,
)
from tests.mirror.support import make_mirror

BACKEND = Path(__file__).resolve().parents[2]

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
    # A read that names no column comes with no schema (verifier S0052 F2): the name is checked all the same.
    "SELECT count(*) FROM dbstat",
    "SELECT count(*) FROM dbstat('main', 1)",
    "SELECT (SELECT count(*) FROM dbstat) > 0",
    "SELECT EXISTS (SELECT 1 FROM dbstat)",
    "SELECT 1 FROM DBSTAT",
    "SELECT count(*) FROM pragma_table_info('m_meta')",
    "SELECT count(*) FROM pragma_module_list",
    "SELECT count(*) FROM temp.sqlite_master",
    "SELECT count(*) FROM sqlite_temp_master",
    "SELECT count(*) FROM fts3tokenize",
    "WITH dbstat AS (SELECT 1) SELECT count(*) FROM dbstat",  # a CTE named like one is refused too: harmless
)


@pytest.mark.parametrize("template", REFUSED)
def test_a_statement_outside_the_allowlist_is_refused_and_writes_nothing(mirror: Path, template: str) -> None:
    folder = mirror.parent
    before = snapshot(folder)
    result = refused(run(mirror, template.format(dir=folder)), "not_allowed")
    assert result.message
    assert snapshot(folder) == before


def test_no_eponymous_virtual_table_answers_a_read_without_columns(mirror: Path) -> None:
    """The population: every virtual-table module this SQLite has (`pragma_module_list`, read here, not listed) and
    the ones it registers only when named (`pragma_*`; `json_*` are allowed on purpose) never answer a count."""
    con = sqlite3.connect(":memory:")
    try:
        modules = [n for (n,) in con.execute("SELECT name FROM pragma_module_list")]
    finally:
        con.close()
    assert "dbstat" in modules
    for name in [*modules, "pragma_table_info", "pragma_function_list", "pragma_database_list"]:
        result = run(mirror, f'SELECT count(*) FROM "{name}"')  # noqa: S608 (names from SQLite itself)
        assert isinstance(result, QueryRefused), (name, result)
        assert result.reason in ("not_allowed", "invalid"), (name, result)  # some need arguments to exist at all


def test_a_read_without_columns_of_the_querys_own_cte_or_a_mirror_table_answers(mirror: Path) -> None:
    assert answered(run(mirror, "WITH n(x) AS (SELECT 1 UNION ALL SELECT 2) SELECT count(*) FROM n")).rows == ((2,),)
    cte = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT 'a' FROM n"
    assert answered(run(mirror, cte)).rows == (("a",), ("a",), ("a",))
    assert answered(run(mirror, "WITH c AS (SELECT 1) SELECT EXISTS (SELECT 1 FROM c)")).rows == ((1,),)
    assert answered(run(mirror, "SELECT count(*) FROM postings")).rows == ((4,),)
    assert answered(run(mirror, "SELECT count(*) FROM m_postings p JOIN m_meta m")).rows == ((24,),)
    assert answered(run(mirror, "SELECT count(*) FROM json_each('[1, 2]')")).rows == ((2,),)


def test_the_resolver_asks_sqlite_whether_a_bare_name_resolves() -> None:
    resolves = _Resolver()
    try:
        for name in ("dbstat", "DBSTAT", "pragma_table_info", "json_each", "sqlite_temp_master", "sqlite_schema"):
            assert resolves(name), name
        for name in ("my_cte", 'we"ird', "pragma_no_such_pragma"):
            assert not resolves(name), name
    finally:
        resolves.close()


def test_a_second_statement_never_runs(mirror: Path) -> None:
    folder = mirror.parent
    result = refused(run(mirror, f"SELECT 1; VACUUM INTO '{folder}/copy.db'"), "invalid")
    assert "one statement" in result.message
    assert not (folder / "copy.db").exists()


def test_confine_sets_the_limits_on_the_connection(mirror: Path) -> None:
    limits = QueryLimits(max_value_bytes=4096, max_columns=40)
    con = sqlite3.connect(mirror)
    try:
        confine(con, limits, frozenset({"m_meta"}), lambda _: True)
        assert con.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
        assert con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == 4096
        assert con.getlimit(sqlite3.SQLITE_LIMIT_COLUMN) == 40
        con.set_authorizer(None)
        assert con.execute("PRAGMA temp_store").fetchone() == (2,)  # MEMORY: no temp file, under the heap limit
        (heap,) = con.execute("PRAGMA hard_heap_limit").fetchone()
        assert 0 < heap <= limits.max_sqlite_heap_bytes
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
    """The cap counts the rows' memory in Python (`sys.getsizeof` of the column names, each row and each value, and
    a list slot a row), as each row arrives."""
    sql = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 4)"
        " SELECT printf('%.*c', 250, 'x') AS v FROM n"
    )
    names = sys.getsizeof(("v",)) + sys.getsizeof("v")
    one = 8 + sys.getsizeof(("x" * 250,)) + sys.getsizeof("x" * 250)
    small = {"max_columns": 1, "max_value_bytes": 250}  # one row: at most row_bound(1, 250) bytes
    capped = answered(run(mirror, sql, QueryLimits(**small, max_result_bytes=names + 3 * one - 1)))
    assert len(capped.rows) == 2  # the third would pass the cap by one byte
    assert capped.truncated == "bytes"
    exact = answered(run(mirror, sql, QueryLimits(**small, max_result_bytes=names + 4 * one)))
    assert (len(exact.rows), exact.truncated) == (4, None)


def test_a_row_wider_than_the_column_limit_is_invalid(mirror: Path) -> None:
    limits = QueryLimits(max_columns=30)
    result = refused(run(mirror, "SELECT " + ", ".join(["1"] * 31), limits), "invalid")
    assert "too many columns" in result.message
    assert len(answered(run(mirror, "SELECT " + ", ".join(["1"] * 30), limits)).columns) == 30
    answered(run(mirror, "SELECT * FROM r_postings", limits))  # the widest view (29 columns) still reads


def test_a_query_text_past_its_limit_is_too_large(mirror: Path) -> None:
    limits = QueryLimits(max_sql_bytes=100)
    refused(run(mirror, "SELECT '" + "é" * 46 + "'", limits), "too_large")  # 101 bytes in UTF-8
    answered(run(mirror, "SELECT '" + "x" * 91 + "'", limits))  # exactly 100


def test_row_bound_holds_for_the_worst_row() -> None:
    """`row_bound` is what `QueryLimits` relies on: a row of `max_value_bytes` values can take no more memory in
    Python, also for text that Python stores at 4 bytes a character (one character past U+FFFF in it)."""
    for size in (4, 100, 250_000):
        worst_text = "a" * (size - 4) + "\U0001f600"  # `size` bytes in UTF-8, 4 bytes a character in Python
        assert len(worst_text.encode()) == size
        values = (worst_text, b"x" * size, 2**63 - 1, -(2**63), 1.5, None)
        for value in values:
            row = (value,)
            assert 8 + sys.getsizeof(row) + sys.getsizeof(value) <= row_bound(1, size), (size, type(value))
    with pytest.raises(ValueError, match="max_result_bytes"):
        QueryLimits(max_columns=64, max_value_bytes=1_000_000, max_result_bytes=64 * 1024 * 1024)


def _probe(tmp_path: Path, sql: str) -> dict[str, object]:
    """Run one query in a fresh interpreter (SQLite's heap limit is process-wide and only ever lowered, and peak RSS
    is per process): its outcome and how much its peak RSS grew, in bytes."""
    code = f"""
import json, resource, sys
from pathlib import Path
from app.mirror.query import QueryLimits, QueryRunner, UserSql
from tests.mirror.support import make_mirror
mirror = make_mirror(Path({str(tmp_path)!r}) / "probe.db")
limits = QueryLimits(max_columns=8, max_value_bytes=100_000, max_result_bytes=4_000_000,
                     max_sqlite_heap_bytes=16 * 1024 * 1024)
runner = QueryRunner(str(mirror), limits)
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
result = runner.run_user_sql(UserSql({sql!r}))
grew = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
scale = 1 if sys.platform == "darwin" else 1024  # bytes on macOS, KiB on Linux
print(json.dumps({{"type": type(result).__name__, "reason": getattr(result, "reason", None),
                  "truncated": getattr(result, "truncated", None), "rows": len(getattr(result, "rows", ())),
                  "grew": grew * scale}}))
"""
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{BACKEND / 'src'}:{BACKEND}", "TMPDIR": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env, cwd=BACKEND)  # noqa: S603
    return json.loads(out.stdout)


GROWTH = 30_000_000  # the probe's limits allow 16 MiB in SQLite and 4 MB of rows plus one; unbounded is 100 MB or more
SERIES: str = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 1000) "


def test_many_large_rows_stay_within_the_result_cap_while_fetching(tmp_path: Path) -> None:
    """Verifier S0052 F1: 1000 rows of 100 kB (100 MB) under a 4 MB cap. The rows are counted as they arrive."""
    out = _probe(tmp_path, f"{SERIES} SELECT randomblob(100000) FROM n")  # noqa: S608 (constant text)
    assert (out["type"], out["truncated"]) == ("QueryRows", "bytes"), out
    assert 0 < int(out["rows"]) < 40, out  # type: ignore[call-overload]
    assert int(out["grew"]) < GROWTH, out  # type: ignore[call-overload]


def test_a_sort_that_needs_more_than_the_heap_limit_is_too_large_and_never_spills(tmp_path: Path) -> None:
    """Verifier S0052 F1: a sort of 90 MB, each record under the 100 kB value limit. With temp files it would spill
    to disk and answer; with `temp_store = MEMORY` under the 16 MiB heap limit it is refused, and the process grew by
    about the heap limit only."""
    out = _probe(tmp_path, f"{SERIES} SELECT x FROM n ORDER BY randomblob(90000)")  # noqa: S608 (constant text)
    assert (out["type"], out["reason"]) == ("QueryRefused", "too_large"), out
    assert int(out["grew"]) < GROWTH, out  # type: ignore[call-overload]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["probe.db"]


def test_one_row_wider_than_the_column_limit_is_never_fetched(tmp_path: Path) -> None:
    """Verifier S0052 F1: one row of 400 values of 100 kB is refused before a row exists."""
    out = _probe(tmp_path, "SELECT " + ", ".join(["randomblob(100000)"] * 400))
    assert (out["type"], out["reason"]) == ("QueryRefused", "invalid"), out
    assert int(out["grew"]) < GROWTH, out  # type: ignore[call-overload]


@pytest.mark.parametrize(
    "limits",
    [
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"max_rows": 0},
        {"max_value_bytes": 0},
        {"max_result_bytes": 0},
        {"max_columns": 0},
        {"max_sql_bytes": 0},
        {"max_sqlite_heap_bytes": 0},
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


@pytest.mark.parametrize(
    ("sql", "params", "says"),
    [
        ("SELECT '\ud800'", None, "not valid text"),
        ("SELECT :x", {"x": "\ud800"}, "x has a character"),
        ("SELECT :x", {"x": 2**63}, "too large"),
        ("SELECT :x", {"x": -(2**63) - 1}, "too large"),
    ],
)
def test_input_sqlite_cannot_take_is_invalid_not_an_exception(
    mirror: Path, sql: str, params: dict[str, object] | None, says: str
) -> None:
    """Verifier S0052 F5: Python's sqlite3 would raise UnicodeEncodeError or OverflowError for these."""
    assert says in refused(run(mirror, sql, params=params), "invalid").message
    assert answered(run(mirror, "SELECT :x", params={"x": 2**63 - 1})).rows == ((2**63 - 1,),)
    assert answered(run(mirror, "SELECT :x", params={"x": -(2**63)})).rows == ((-(2**63),),)


@pytest.mark.parametrize("value", [10.5, b"bytes", [1]])
def test_a_parameter_that_is_not_an_int_a_str_or_none_is_a_caller_error(mirror: Path, value: object) -> None:
    """Verifier S0052 F6: money and other decimals go as text (INV-06), never as a float."""
    with pytest.raises(TypeError, match="must be an int, a str or None"):
        run(mirror, "SELECT :x", params={"x": value})
    assert answered(run(mirror, "SELECT :x, :y", params={"x": "10.50", "y": None})).rows == (("10.50", None),)


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


def hot_journal_copy(tmp_path: Path) -> Path:
    """A mirror file with the hot journal a build stopped half-way leaves (verifier S0052 F3)."""
    src = make_mirror(tmp_path / "src.db")
    hot = tmp_path / "hot"
    hot.mkdir()
    con = sqlite3.connect(src, isolation_level=None)
    try:
        con.execute("PRAGMA cache_size = 1")
        con.execute("BEGIN")
        con.execute(
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 20000)"
            " INSERT INTO m_postings (posting_id, account, amount) SELECT x + 10, 'A', '1' FROM n"
        )
        shutil.copy(src, hot / "m.db")
        shutil.copy(f"{src}-journal", hot / "m.db-journal")
        con.execute("ROLLBACK")
    finally:
        con.close()
    return hot / "m.db"


def test_a_file_with_a_hot_journal_is_unavailable_and_unchanged(tmp_path: Path) -> None:
    path = hot_journal_copy(tmp_path)
    before = snapshot(path.parent)
    result = run(path, "SELECT 1")
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"
    assert snapshot(path.parent) == before


def test_a_database_that_is_not_a_mirror_is_unavailable(tmp_path: Path) -> None:
    """Verifier S0052 F4: an empty file and any other SQLite database are not answered."""
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE secrets (x)")
    con.commit()
    con.close()
    for path in (empty, other):
        result = run(path, "SELECT 1")
        assert isinstance(result, MirrorUnavailable), (path.name, result)
        assert result.reason == "not_a_mirror"


def test_a_wal_mode_file_is_unavailable_and_nothing_is_created(tmp_path: Path) -> None:
    """Verifier S0052 F9: SQLite would create `-wal` and `-shm` files beside a WAL-mode file, even read-only. The
    build writes rollback mode; a read creates nothing beside the file."""
    path = make_mirror(tmp_path / "m.db")
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = WAL")
    con.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.db"]
    result = run(path, "SELECT count(*) FROM m_postings")
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.db"]


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
