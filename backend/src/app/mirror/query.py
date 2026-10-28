"""The one hardened runner for user SQL over the mirror (ADR-0016; ARCHITECTURE.md §2.2; L1-10, L5-07; INV-09, INV-10).

Every user query (a dashboard's `query` step, Analyze, the assistant's tools) runs here, never on a connection of
its own. A read-only open alone is not enough: on a `mode=ro` connection SQLite still runs `ATTACH`, `PRAGMA`,
`CREATE TEMP TABLE` and `VACUUM INTO` (which writes a new file wherever the process may write). So, on top of the
checked read-only open with `query_only` (`files.open_mirror`), `confine` sets on each connection:

- an authorizer **allowlist**: only `SELECT`, reads, function calls and recursive CTEs pass. Every read is checked
  by its table's name, whatever schema SQLite reports: it must name an object of the mirror file itself (its tables
  and views, `sqlite_schema`) or a JSON table function. That keeps out every `pragma_*()` table, `dbstat` (and any
  other eponymous virtual table) and the `temp` schema. A read that names no column (`COUNT(*)`, `SELECT 1 FROM x`)
  comes with no schema, and SQLite reports it the same way for a stored table, an eponymous virtual table and a
  CTE of the query. So for such a read of any other name, the guard asks SQLite itself whether the bare name
  resolves outside the query (`_Resolver`: a statement prepared, never run, on an empty in-memory connection): if
  it does not, the name can only be a CTE of the query and the read passes. A function call passes unless it is
  `load_extension` or `fts3_tokenizer`, the two core functions that reach outside the query (SQLite's other
  built-ins only compute). Anything else (`PRAGMA`, `ATTACH`, writes, `CREATE`, transactions, `ANALYZE`, `VACUUM`)
  is refused before it runs;
- `SQLITE_LIMIT_ATTACHED` = 0 (no other file, even if the authorizer were bypassed);
- the memory and disk bounds of `QueryLimits` (its docstring says what each bounds): `SQLITE_LIMIT_LENGTH` and
  `SQLITE_LIMIT_COLUMN`, `temp_store = MEMORY` (a sort, a `DISTINCT` or a CTE's table never spills to a temp file)
  under SQLite's hard heap limit;
- a progress handler that stops the query in SQLite itself once its time is up, so a slow query frees its thread
  (L1-10 "working timeout"; v0.3's `wait_for` left the query running).

Python's `sqlite3` refuses a second statement before running anything, and binds parameters by name only; the text
and the parameters are checked first, so input SQLite cannot take is `invalid`, not an exception. Rows are fetched
one at a time and counted as they arrive (`QueryRows.truncated` says which cap ended them). Expected outcomes are
values (EP-13): `QueryRows`, `QueryRefused` (the query, not the app, is at fault), `MirrorUnavailable`. Limits are
configuration values (INV-10 style), not development targets.

TODO(BL-0113): the runner reads the generation file it is given; with the publish it reads the generation the
request pinned, and the freshness check against the disk is BL-0125's.
"""

from __future__ import annotations

import contextlib
import math
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, NewType

from app.mirror.files import MirrorUnavailable, absolute, open_mirror, unavailable

__all__ = [
    "QueryLimits",
    "QueryRefused",
    "QueryRows",
    "QueryRunner",
    "SqlParam",
    "SqlValue",
    "UserSql",
    "confine",
    "row_bound",
]

UserSql = NewType("UserSql", str)
"""SQL text a user, a recipe or the assistant wrote (ARCHITECTURE.md §2.6). Only `QueryRunner` runs it."""

type SqlValue = int | float | str | bytes | None
# No float parameter: money is text (INV-06); a recipe passes a decimal as its text. Checked at run time too.
type SqlParam = int | str | None

type RefusalReason = Literal[
    "not_allowed",  # outside the allowlist (PRAGMA, ATTACH, a write, a denied function or table)
    "timed_out",  # stopped at its time limit
    "too_large",  # past a size limit: its text, a value, or the memory SQLite may use
    "invalid",  # a mistake in the query: syntax, an unknown name, a bad or missing parameter, two statements
]

ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
DENIED_FUNCTIONS = frozenset({"load_extension", "fts3_tokenizer"})
TABLE_FUNCTIONS = frozenset({"json_each", "json_tree"})
SCHEMA_TABLES = frozenset({"sqlite_schema", "sqlite_master"})
ONLY_SELECT = "Only a SELECT that reads the mirror can run here."
# The mirror's own objects are read from the file itself, not from a list here, so a new table is readable at once.
OBJECTS_SQL = "SELECT name FROM sqlite_schema WHERE type IN ('table', 'view')"
INT64: Final = (-(2**63), 2**63 - 1)  # what SQLite can bind

# The most memory Python's objects take for one value and one row, from `sys.getsizeof` on CPython 3.13: a str is at
# most 4 bytes a character (one character past U+FFFF makes every character 4 bytes) plus its header, under 64
# bytes; an int or a float under 64 bytes; a tuple 40 bytes plus 8 a column (`test_row_bound_holds_for_the_worst_row`).
VALUE_OVERHEAD: Final = 64
TUPLE_OVERHEAD: Final = 40
SLOT: Final = 8


def row_bound(max_columns: int, max_value_bytes: int) -> int:
    """The most memory one fetched row can take in Python: `max_columns` values of `max_value_bytes` UTF-8 bytes."""
    return TUPLE_OVERHEAD + max_columns * (SLOT + VALUE_OVERHEAD + 4 * max_value_bytes)


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Bounds of one user query. Desktop defaults; a hosted deployment sets its own (INV-10).

    What is bounded, and how (INV-10: every bound is real):
    - time: `timeout_s`, checked inside SQLite every `progress_steps` virtual-machine steps;
    - SQLite's memory: `max_sqlite_heap_bytes`, SQLite's hard heap limit. SQLite has no per-connection limit, so it
      is process-wide: it bounds all SQLite memory of the main sidecar (only mirror reads use SQLite there) and the
      queries running at once share it; a query past it is `too_large`, and the lowest value set in the process
      wins. With `temp_store = MEMORY` the sorter and every temporary table live under this limit, so a query
      writes no temp file at all and cannot fill the disk;
    - one value: `max_value_bytes` (`SQLITE_LIMIT_LENGTH`), also for a value read from the file and inside the query;
    - one row: `max_columns` (`SQLITE_LIMIT_COLUMN`, also the widest view or join a query may use; the mirror's
      widest view has 29) times `max_value_bytes`; `row_bound` of the two must fit in `max_result_bytes`;
    - the rows handed back: `max_rows`, and `max_result_bytes`, their memory in Python (`sys.getsizeof` of each row,
      value and column name), counted as each row arrives. The row that passes the cap is dropped, so the rows
      kept stay under it and Python holds at most `max_result_bytes` plus one row (both under `max_result_bytes`);
    - the query's text: `max_sql_bytes` (UTF-8), which also bounds the column names SQLite makes from it.
    A query therefore holds at most `max_sqlite_heap_bytes` in SQLite plus twice `max_result_bytes` in Python.
    """

    timeout_s: float = 30.0  # v0.3's limit, now enforced inside SQLite
    max_rows: int = 10_000
    max_columns: int = 64
    max_value_bytes: int = 250_000
    max_result_bytes: int = 64 * 1024 * 1024
    max_sql_bytes: int = 100_000
    max_sqlite_heap_bytes: int = 256 * 1024 * 1024  # the 54k mirror's self-join of postings needs 64 to 128 MiB
    progress_steps: int = 10_000  # SQLite virtual-machine steps between two looks at the clock

    def __post_init__(self) -> None:
        for name in (
            "timeout_s",
            "max_rows",
            "max_columns",
            "max_value_bytes",
            "max_result_bytes",
            "max_sql_bytes",
            "max_sqlite_heap_bytes",
            "progress_steps",
        ):
            value = getattr(self, name)
            if not (value > 0 and math.isfinite(value)):
                msg = f"{name} must be a positive, finite number, not {value!r}"
                raise ValueError(msg)
        if (row := row_bound(self.max_columns, self.max_value_bytes)) > self.max_result_bytes:
            msg = (
                f"max_result_bytes ({self.max_result_bytes}) must hold one row of max_columns values of"
                f" max_value_bytes ({row} bytes in Python)"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class QueryRows:
    """The answer: column names, rows in SQLite's order, and which cap ended it early (None: every row is here)."""

    columns: tuple[str, ...]
    rows: tuple[tuple[SqlValue, ...], ...]
    truncated: Literal["rows", "bytes"] | None


@dataclass(frozen=True, slots=True)
class QueryRefused:
    """The query did not run to its end. `message` is plain text for the person who wrote the query."""

    reason: RefusalReason
    message: str


class _Resolver:
    """Whether SQLite resolves a bare table name outside any query: a table of an empty database's schemas or an
    eponymous virtual table (`dbstat`, `pragma_*`, `json_each`, and any a later SQLite adds, also the ones it
    registers only when first named, which `PRAGMA module_list` does not show). It prepares `SELECT 1 FROM "<name>"`
    on its own in-memory connection under an authorizer that records the read and refuses everything, so nothing
    runs. Every connection of the process gets the same built-in modules, so the answer holds for the mirror's
    connection too. Opened on first use, once per query at most; `close` ends it."""

    __slots__ = ("_answers", "_con")

    def __init__(self) -> None:
        self._con: sqlite3.Connection | None = None
        self._answers: dict[str, bool] = {}

    def __call__(self, name: str) -> bool:
        if (known := self._answers.get(name)) is not None:
            return known
        if self._con is None:
            self._con = sqlite3.connect(":memory:")
        reads: list[str | None] = []

        def record(action: int, arg1: str | None, *_: str | None) -> int:
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                reads.append(arg1)
            return sqlite3.SQLITE_DENY

        self._con.set_authorizer(record)
        quoted = name.replace('"', '""')
        # Always refused, so it never runs: "no such table" (a CTE's name) or "not authorized" (it resolves).
        with contextlib.suppress(sqlite3.Error):
            self._con.execute(f'SELECT 1 FROM "{quoted}"')  # noqa: S608 (a quoted name, on an empty in-memory db)
        self._answers[name] = bool(reads)
        return self._answers[name]

    def close(self) -> None:
        if self._con is not None:
            self._con.close()


class _Guard:
    """The authorizer of one query. It records the first thing it refused, for the message."""

    __slots__ = ("_objects", "_resolves", "refused")

    def __init__(self, objects: frozenset[str], resolves: Callable[[str], bool]) -> None:
        self._objects = objects
        self._resolves = resolves
        self.refused: str | None = None

    def __call__(self, action: int, arg1: str | None, arg2: str | None, db: str | None, _source: str | None) -> int:
        if self._allowed(action, arg1, arg2, db):
            return sqlite3.SQLITE_OK
        if self.refused is None:
            self.refused = _describe(action, arg1, arg2)
        return sqlite3.SQLITE_DENY

    def _allowed(self, action: int, arg1: str | None, arg2: str | None, db: str | None) -> bool:
        if action not in ALLOWED_ACTIONS:
            return False
        if action == sqlite3.SQLITE_READ:
            # The name is checked whatever the schema: a read that names no column comes with none, also for an
            # eponymous virtual table such as `dbstat`. Such a read of a name SQLite cannot resolve outside the
            # query is of the query's own CTE.
            table = (arg1 or "").lower()
            if db not in (None, "main"):
                return False
            if table in self._objects or table in SCHEMA_TABLES or table in TABLE_FUNCTIONS:
                return True
            return db is None and not arg2 and arg1 is not None and not self._resolves(arg1)
        if action == sqlite3.SQLITE_FUNCTION:
            return (arg2 or "").lower() not in DENIED_FUNCTIONS
        return True


def _describe(action: int, arg1: str | None, arg2: str | None) -> str:
    if action == sqlite3.SQLITE_READ:
        return f"reading {arg1}"
    if action == sqlite3.SQLITE_FUNCTION:
        return f"the function {arg2}"
    if action == sqlite3.SQLITE_PRAGMA:
        return "PRAGMA"
    if action == sqlite3.SQLITE_ATTACH:
        return "ATTACH"
    return "changing the database"


def confine(
    con: sqlite3.Connection, limits: QueryLimits, objects: frozenset[str], resolves: Callable[[str], bool]
) -> _Guard:
    """Lock a read-only connection down for user SQL: the limits and the authorizer (module docstring). `objects` are
    the lower-case names of the tables and views a query may read; `resolves` says whether SQLite resolves a bare
    table name outside a query (`_Resolver`). Anything the app runs on the connection itself (the `steps` schema of
    ADR-0029, M7) runs before this."""
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
    con.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, limits.max_columns)
    con.execute("PRAGMA temp_store = MEMORY")
    # The pragma only ever lowers SQLite's process-wide limit; read back what holds now.
    con.execute(f"PRAGMA hard_heap_limit = {int(limits.max_sqlite_heap_bytes)}")
    (heap,) = con.execute("PRAGMA hard_heap_limit").fetchone()
    if not 0 < heap <= limits.max_sqlite_heap_bytes:
        msg = f"SQLite's hard heap limit is {heap}, not at most {limits.max_sqlite_heap_bytes}"
        raise RuntimeError(msg)
    guard = _Guard(objects, resolves)
    con.set_authorizer(guard)
    return guard


class QueryRunner:
    """Runs user SQL against one mirror file. Holds no connection: each call opens, confines, runs and closes its
    own, so calls on worker threads never share one (ARCHITECTURE.md §9.1) and nothing stays resident (INV-10)."""

    __slots__ = ("_limits", "_monotonic", "_path")

    def __init__(
        self, path: str, limits: QueryLimits | None = None, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        absolute(path)
        self._path = path
        self._limits = limits if limits is not None else QueryLimits()
        self._monotonic = monotonic

    def run_user_sql(
        self, sql: UserSql, params: Mapping[str, SqlParam] | None = None, *, max_rows: int | None = None
    ) -> QueryRows | QueryRefused | MirrorUnavailable:
        """Run one SELECT with named parameters (`:name`). `max_rows` may lower the configured cap, never raise it.
        A parameter that is not an int, a str or None (a float above all, INV-06) is a caller error (`TypeError`)."""
        if max_rows is not None and max_rows <= 0:
            msg = f"max_rows must be positive, not {max_rows}"
            raise ValueError(msg)
        limits = self._limits
        bound = dict(params or {})
        if (bad := _check_input(sql, bound, limits)) is not None:
            return bad
        cap = limits.max_rows if max_rows is None else min(max_rows, limits.max_rows)
        mirror = open_mirror(self._path)
        if isinstance(mirror, MirrorUnavailable):
            return mirror
        con = mirror.con
        resolver = _Resolver()
        try:
            objects = frozenset(name.lower() for (name,) in con.execute(OBJECTS_SQL))
            guard = confine(con, limits, objects, resolver)
            deadline = self._monotonic() + limits.timeout_s
            con.set_progress_handler(lambda: self._monotonic() >= deadline, limits.progress_steps)
            try:
                cursor = con.execute(sql, bound)
                if cursor.description is None:  # it ran and returns no rows: no statement, or a no-op such as REINDEX
                    return QueryRefused("not_allowed", ONLY_SELECT)
                columns = tuple(str(d[0]) for d in cursor.description)
                return _fetch(cursor, columns, cap, limits.max_result_bytes)
            except sqlite3.Error as e:
                return _refusal(e, guard)
            except MemoryError:  # SQLite reached its hard heap limit (SQLITE_NOMEM; Python raises MemoryError)
                return QueryRefused("too_large", "The query needs more memory than a query may use.")
        finally:
            con.close()
            resolver.close()


def _check_input(sql: str, params: dict[str, SqlParam], limits: QueryLimits) -> QueryRefused | None:
    """Input SQLite cannot take is the query's mistake, found before running: text that is not valid Unicode (a lone
    surrogate), a number past SQLite's 64-bit integers, a text past `max_sql_bytes`."""
    if not sql.strip():
        return QueryRefused("invalid", "There is no statement to run.")
    try:
        size = len(sql.encode("utf-8"))
    except UnicodeEncodeError:
        return QueryRefused("invalid", "The query has a character that is not valid text.")
    if size > limits.max_sql_bytes:
        return QueryRefused("too_large", f"The query is longer than {limits.max_sql_bytes} bytes.")
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, int):
            if not INT64[0] <= value <= INT64[1]:
                return QueryRefused("invalid", f"The parameter {name} is a number too large for SQLite.")
        elif isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance] (a caller may pass anything)
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return QueryRefused("invalid", f"The parameter {name} has a character that is not valid text.")
        else:
            msg = f"parameter {name!r} must be an int, a str or None, not {type(value).__name__} (a decimal is text)"
            raise TypeError(msg)
    return None


def _fetch(cursor: sqlite3.Cursor, columns: tuple[str, ...], cap: int, max_bytes: int) -> QueryRows:
    """Rows one at a time, each counted as it arrives: Python never holds more than the kept rows and one more."""
    rows: list[tuple[SqlValue, ...]] = []
    size = sys.getsizeof(columns) + sum(map(sys.getsizeof, columns))
    for row in cursor:
        if len(rows) == cap:
            return QueryRows(columns, tuple(rows), "rows")
        size += SLOT + sys.getsizeof(row) + sum(map(sys.getsizeof, row))
        if size > max_bytes:
            return QueryRows(columns, tuple(rows), "bytes")
        rows.append(row)
    return QueryRows(columns, tuple(rows), None)


def _refusal(error: sqlite3.Error, guard: _Guard) -> QueryRefused | MirrorUnavailable:
    """The outcome of an error while the query ran. An error that is neither the query's nor the file's (a disk
    error) is the app's and propagates (EP-13)."""
    if guard.refused is not None:  # SQLite reports a refused function as a plain error, so ask the guard
        return QueryRefused("not_allowed", f"{ONLY_SELECT} Not allowed: {guard.refused}.")
    code = getattr(error, "sqlite_errorcode", None)
    if code is None:  # raised by Python's sqlite3 before running: a second statement, a missing parameter
        return QueryRefused("invalid", str(error))
    if code == sqlite3.SQLITE_INTERRUPT:
        return QueryRefused("timed_out", "The query took too long and was stopped.")
    if code == sqlite3.SQLITE_TOOBIG:
        return QueryRefused("too_large", "The query made or read a text or blob that is too long.")
    if code in (sqlite3.SQLITE_ERROR, sqlite3.SQLITE_RANGE, sqlite3.SQLITE_MISMATCH):
        return QueryRefused("invalid", str(error))
    if (outcome := unavailable(error)) is not None:
        return outcome
    raise error
