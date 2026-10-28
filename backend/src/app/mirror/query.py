"""The one hardened runner for user SQL over the mirror (ADR-0016; ARCHITECTURE.md §2.2; L1-10, L5-07; INV-09).

Every user query (a dashboard's `query` step, Analyze, the assistant's tools) runs here, never on a connection of
its own. A read-only open alone is not enough: on a `mode=ro` connection SQLite still runs `ATTACH`, `PRAGMA`,
`CREATE TEMP TABLE` and `VACUUM INTO` (which writes a new file wherever the process may write). So, on top of the
read-only open with `query_only` (`files.open_mirror`), `confine` sets on each connection:

- an authorizer **allowlist**: only `SELECT`, reads, function calls and recursive CTEs pass. A read must name an
  object of the mirror file itself (its tables and views, `sqlite_schema`) or a JSON table function; this keeps out
  every `pragma_*()` table, `dbstat` and the `temp` schema. A function call passes unless it is `load_extension` or
  `fts3_tokenizer`, the two core functions that reach outside the query (SQLite's other built-ins only compute).
  Anything else (`PRAGMA`, `ATTACH`, writes, `CREATE`, transactions, `ANALYZE`, `VACUUM`) is refused before it runs;
- `SQLITE_LIMIT_ATTACHED` = 0 (no other file, even if the authorizer were bypassed) and `SQLITE_LIMIT_LENGTH`, the
  longest text or blob a query may make (`max_value_bytes`);
- a progress handler that stops the query in SQLite itself once its time is up, so a slow query frees its thread
  (L1-10 "working timeout"; v0.3's `wait_for` left the query running).

Python's `sqlite3` refuses a second statement before running anything, and binds parameters by name only. The
result is capped by rows and by bytes (`QueryRows.truncated` says which cap ended it). Expected outcomes are values
(EP-13): `QueryRows`, `QueryRefused` (the query, not the app, is at fault), `MirrorUnavailable`. Limits are
configuration values (INV-10 style), not development targets.

TODO(BL-0113): the runner reads the generation file it is given; with the publish it reads the generation the
request pinned, and the freshness check against the disk is BL-0125's.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, NewType

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
]

UserSql = NewType("UserSql", str)
"""SQL text a user, a recipe or the assistant wrote (ARCHITECTURE.md §2.6). Only `QueryRunner` runs it."""

type SqlValue = int | float | str | bytes | None
# No float parameter: money is text (INV-06); a recipe passes a decimal as its text.
type SqlParam = int | str | None

type RefusalReason = Literal[
    "not_allowed",  # outside the allowlist (PRAGMA, ATTACH, a write, a denied function or table)
    "timed_out",  # stopped at its time limit
    "too_large",  # made a text or blob longer than max_value_bytes
    "invalid",  # a mistake in the query: syntax, an unknown name, a missing parameter, two statements
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


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Bounds of one user query. Desktop defaults; a hosted deployment sets its own (INV-10)."""

    timeout_s: float = 30.0  # v0.3's limit, now enforced inside SQLite
    max_rows: int = 10_000
    max_value_bytes: int = 16 * 1024 * 1024  # SQLITE_LIMIT_LENGTH: one text or blob, also inside the query
    max_result_bytes: int = 64 * 1024 * 1024  # the rows handed back, counted as their text and blob lengths
    progress_steps: int = 10_000  # SQLite virtual-machine steps between two looks at the clock

    def __post_init__(self) -> None:
        for name in ("timeout_s", "max_rows", "max_value_bytes", "max_result_bytes", "progress_steps"):
            value = getattr(self, name)
            if not (value > 0 and math.isfinite(value)):
                msg = f"{name} must be a positive, finite number, not {value!r}"
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


class _Guard:
    """The authorizer of one query. It records the first thing it refused, for the message."""

    __slots__ = ("_objects", "refused")

    def __init__(self, objects: frozenset[str]) -> None:
        self._objects = objects
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
            if db is None:  # a CTE or subquery of this statement (`COUNT(*)` over it), not a stored object
                return True
            table = (arg1 or "").lower()
            return db == "main" and (table in self._objects or table in SCHEMA_TABLES or table in TABLE_FUNCTIONS)
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


def confine(con: sqlite3.Connection, limits: QueryLimits, objects: frozenset[str]) -> _Guard:
    """Lock a read-only connection down for user SQL: the limits and the authorizer (module docstring). `objects` are
    the lower-case names of the tables and views a query may read. Anything the app runs on the connection itself
    (the `steps` schema of ADR-0029, M7) runs before this."""
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
    guard = _Guard(objects)
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
        """Run one SELECT with named parameters (`:name`). `max_rows` may lower the configured cap, never raise it."""
        if not sql.strip():
            return QueryRefused("invalid", "There is no statement to run.")
        if max_rows is not None and max_rows <= 0:
            msg = f"max_rows must be positive, not {max_rows}"
            raise ValueError(msg)
        limits = self._limits
        cap = limits.max_rows if max_rows is None else min(max_rows, limits.max_rows)
        con = open_mirror(self._path)
        if isinstance(con, MirrorUnavailable):
            return con
        try:
            objects = frozenset(name.lower() for (name,) in con.execute(OBJECTS_SQL))
            guard = confine(con, limits, objects)
            deadline = self._monotonic() + limits.timeout_s
            con.set_progress_handler(lambda: self._monotonic() >= deadline, limits.progress_steps)
            try:
                cursor = con.execute(sql, dict(params or {}))
                if cursor.description is None:  # it ran and returns no rows: no statement, or a no-op such as REINDEX
                    return QueryRefused("not_allowed", ONLY_SELECT)
                columns = tuple(str(d[0]) for d in cursor.description)
                return _fetch(cursor, columns, cap, limits.max_result_bytes)
            except sqlite3.Error as e:
                return _refusal(e, guard)
        finally:
            con.close()


def _fetch(cursor: sqlite3.Cursor, columns: tuple[str, ...], cap: int, max_bytes: int) -> QueryRows:
    rows: list[tuple[SqlValue, ...]] = []
    size = 0
    while batch := cursor.fetchmany(min(cap + 1 - len(rows), 500)):
        for row in batch:
            if len(rows) == cap:
                return QueryRows(columns, tuple(rows), "rows")
            size += sum(len(v) if isinstance(v, str | bytes) else 8 for v in row)
            if size > max_bytes:
                return QueryRows(columns, tuple(rows), "bytes")
            rows.append(tuple(row))
    return QueryRows(columns, tuple(rows), None)


def _refusal(error: sqlite3.Error, guard: _Guard) -> QueryRefused | MirrorUnavailable:
    """The outcome of an error while the query ran. An error that is neither the query's nor the file's (a disk
    error, SQLite running out of memory) is the app's and propagates (EP-13)."""
    if guard.refused is not None:  # SQLite reports a refused function as a plain error, so ask the guard
        return QueryRefused("not_allowed", f"{ONLY_SELECT} Not allowed: {guard.refused}.")
    code = getattr(error, "sqlite_errorcode", None)
    if code is None:  # raised by Python's sqlite3 before running: a second statement, a missing parameter
        return QueryRefused("invalid", str(error))
    if code == sqlite3.SQLITE_INTERRUPT:
        return QueryRefused("timed_out", "The query took too long and was stopped.")
    if code == sqlite3.SQLITE_TOOBIG:
        return QueryRefused("too_large", "The query made a text or blob that is too long.")
    if code in (sqlite3.SQLITE_ERROR, sqlite3.SQLITE_RANGE, sqlite3.SQLITE_MISMATCH):
        return QueryRefused("invalid", str(error))
    if (outcome := unavailable(error)) is not None:
        return outcome
    raise error
