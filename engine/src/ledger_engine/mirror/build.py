"""Build the mirror of one load into a fresh SQLite file (ADR-0015 "Build a new generation file, then publish it
atomically. A failed build never publishes"; INV-05).

`build(loaded, path, today, stamp)` computes every row first (`core.rows`, pure), then creates `path`, which must
not exist, and writes the DDL and the rows in one transaction. A build that fails leaves no file: a rounding refusal
(README "Exact numbers") is found before the file is created and returned as `BuildFailed`; any other error removes
the file and is raised. The file is left in SQLite's default rollback-journal mode, closed, with no journal beside
it, ready to be published.

TODO(BL-0113): the generation publish (pointer swap, pinning, GC, the stale-WAL rule) wraps this, built with its
formal model. TODO(BL-0109): an engine job runs it, after the overlay load, with the job's `today` and stamp.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Literal

from ledger_engine.ledger.overlay import Loaded
from ledger_engine.mirror.core import rows, schema
from ledger_engine.mirror.core.rows import Stamp

__all__ = ["BuildFailed", "Built", "Stamp", "build"]

FILE_MODE = 0o600  # the ledger's content: readable by the user's own tools only


@dataclass(frozen=True, slots=True)
class Built:
    """The file was written. `complete` is False while a table is not built yet (BL-0120): such a file says so in
    `meta.build_complete` too, and the publish must refuse it (BL-0113)."""

    path: str
    rows: dict[str, int]
    complete: bool


@dataclass(frozen=True, slots=True)
class BuildFailed:
    """No file was written. `detail` names the table and column (`account_balances.balance`)."""

    reason: Literal["inexact"]
    detail: str


def build(loaded: Loaded, path: str, today: date, stamp: Stamp) -> Built | BuildFailed:
    """Raises `FileExistsError` when `path` exists: a build never writes over a file."""
    try:
        tables = rows.table_rows(loaded.entries, loaded.errors, loaded.options, today, stamp)
    except rows.InexactError as e:
        return BuildFailed("inexact", f"{e.table}.{e.column}")
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, FILE_MODE))
    try:
        _write(path, tables)
    except BaseException:
        for leftover in (path, f"{path}-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(leftover)
        raise
    return Built(path, {name: len(table) for name, table in tables.items()}, rows.COMPLETE)


def _write(path: str, tables: dict[str, list[rows.Row]]) -> None:
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("BEGIN")
        for statement in schema.statements():
            con.execute(statement)
        for table in schema.TABLES:
            marks = ", ".join("?" * len(table.columns))
            con.executemany(f"INSERT INTO {table.base} VALUES ({marks})", tables[table.name])  # noqa: S608 (names from the schema)
        con.execute("COMMIT")
    finally:
        con.close()
