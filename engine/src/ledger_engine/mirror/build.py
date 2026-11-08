"""Build the mirror of one load into a fresh SQLite file (ADR-0015 "Build a new generation file, then publish it
atomically. A failed build never publishes"; INV-05).

`build(loaded, path, today, stamp)` computes every row first (`tables`, from `core.rows`, pure), then creates `path`,
which must not exist, and writes the DDL and the rows in one transaction (`write`). A build that fails leaves no file:
a rounding refusal (README "Exact numbers") is found before the file is created and returned as `BuildFailed`; any
other error removes the file and is raised. The file is left in SQLite's default rollback-journal mode, closed, with
no journal beside it, ready to be published.

`write` opens the file with SQLite's URI mode `rw`, which never creates a file (`design/specs/mirror/publish.md` §2
"The build's own open"): the publish creates the generation exclusively through its folder's descriptor
(`ledger_engine.mirror.store.SqliteStore`), and if the path no longer leads to that file the open fails instead of
writing elsewhere. The publish around it is `ledger_engine.mirror.publish` (S0087, BL-0113).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.overlay import Loaded
from ledger_engine.mirror.core import rows, schema
from ledger_engine.mirror.core.rows import Row, Stamp

__all__ = ["BuildFailed", "Built", "Stamp", "Tables", "build", "tables", "write"]

type Tables = dict[str, list[Row]]

FILE_MODE = 0o600  # the ledger's content: readable by the user's own tools only


@dataclass(frozen=True, slots=True)
class Built:
    """The file was written. `complete` is False when the build left a table out (none does since S0086, BL-0120):
    such a file says so in `meta.build_complete` too, and the publish must refuse it (BL-0113)."""

    path: str
    rows: dict[str, int]
    complete: bool


@dataclass(frozen=True, slots=True)
class BuildFailed:
    """No file was written. `detail` names the table and column (`account_balances.balance`)."""

    reason: Literal["inexact"]
    detail: str


def tables(
    entries: Sequence[port.Directive],
    errors: Sequence[port.BeancountError],
    options: port.OptionsMap,
    today: date,
    stamp: Stamp,
) -> Tables | BuildFailed:
    """Every row of every table (pure); a rounding refusal is `BuildFailed`, found before any file exists."""
    try:
        return rows.table_rows(entries, errors, options, today, stamp)
    except rows.InexactError as e:
        return BuildFailed("inexact", f"{e.table}.{e.column}")


def counts(made: Tables) -> dict[str, int]:
    return {name: len(table) for name, table in made.items()}


def build(loaded: Loaded, path: str, today: date, stamp: Stamp) -> Built | BuildFailed:
    """Raises `FileExistsError` when `path` exists: a build never writes over a file."""
    made = tables(loaded.entries, loaded.errors, loaded.options, today, stamp)
    if isinstance(made, BuildFailed):
        return made
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, FILE_MODE))
    try:
        write(path, made)
    except BaseException:
        for leftover in (path, f"{path}-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(leftover)
        raise
    return Built(path, counts(made), rows.COMPLETE)


def write(path: str, made: Tables) -> None:
    """The DDL and the rows into the existing empty file at the absolute `path`, in one transaction (rollback mode: at
    the commit SQLite deletes its journal). The open never creates a file (`mode=rw`)."""
    con = sqlite3.connect(f"{Path(path).as_uri()}?mode=rw", uri=True, isolation_level=None)
    try:
        con.execute("BEGIN")
        for statement in schema.statements():
            con.execute(statement)
        for table in schema.TABLES:
            marks = ", ".join("?" * len(table.columns))
            con.executemany(f"INSERT INTO {table.base} VALUES ({marks})", made[table.name])  # noqa: S608 (names from the schema)
        con.execute("COMMIT")
    finally:
        con.close()
