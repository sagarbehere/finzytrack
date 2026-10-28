"""Typed reads of the mirror for the app's own use cases (ARCHITECTURE.md §2.2, §9.1; ADR-0011, ADR-0015).

The app's SQL is fixed text over the base tables (`m_*`, `design/specs/mirror/README.md` "Three layers"), run on a
read-only connection (`files.open_mirror`); user SQL never comes here (`query.QueryRunner`). Each read opens and
closes its own connection: nothing stays resident (INV-10) and no read loads the ledger (ADR-0011). Text that came
from the ledger is `UntrustedText` (INV-09: data, never markup). A file that is not a readable mirror is
`MirrorUnavailable` (EP-13).

The first reads are the ones the open needs: the mirror's own facts (`info`: its `ledger_version` for the envelope,
§9.1 step 4, and whether the build was complete) and the load's errors (`ledger_errors`: the notice of
CAP-NOTIFY-SERVER, M2).

TODO(BL-0113): the reader reads the generation file it is given; with the publish it reads the pinned generation.
TODO(BL-0125): the freshness check compares `info().fingerprint` with the disk, and `export_version` with this
build's.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, LiteralString

from app.domain.text import UntrustedText
from app.mirror.files import MirrorUnavailable, absolute, open_mirror, unavailable

__all__ = ["LedgerError", "LedgerErrors", "MirrorInfo", "Reader"]

TABLE_SQL: Final[LiteralString] = "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?"
META_SQL: Final[LiteralString] = "SELECT key, value FROM m_meta"
ERROR_COUNT_SQL: Final[LiteralString] = "SELECT COUNT(*) FROM m_ledger_errors"
ERRORS_SQL: Final[LiteralString] = (
    "SELECT id, source_file, line_number, message, error_type, entry_json FROM m_ledger_errors ORDER BY id LIMIT ?"
)
META_KEYS = ("surface_version", "ledger_version", "fingerprint", "export_version", "built_at", "build_complete")


@dataclass(frozen=True, slots=True)
class MirrorInfo:
    """The mirror's `meta` (surface.toml `meta` note). `build_complete` is False while a table is not built yet
    (BL-0120); a published generation is always complete (BL-0113)."""

    surface_version: int
    ledger_version: str
    fingerprint: str
    export_version: str
    built_at: str
    build_complete: bool


@dataclass(frozen=True, slots=True)
class LedgerError:
    """One error of the load (`ledger_errors`): a parser, booking, validation or plugin error. `line_number` and
    `source_file` are None when Beancount gives none."""

    id: int
    source_file: str | None
    line_number: int | None
    message: UntrustedText
    error_type: str
    entry_json: UntrustedText | None


@dataclass(frozen=True, slots=True)
class LedgerErrors:
    """The first errors, in the load's order, and how many there are in all."""

    total: int
    errors: tuple[LedgerError, ...]


class _NotAMirror(Exception):
    pass


class Reader:
    """Reads one mirror file. Holds only its path."""

    __slots__ = ("_path",)

    def __init__(self, path: str) -> None:
        absolute(path)
        self._path = path

    def info(self) -> MirrorInfo | MirrorUnavailable:
        """The mirror's own facts. Every key must be there once, with a value of its kind."""
        return self._read(_info)

    def ledger_errors(self, limit: int) -> LedgerErrors | MirrorUnavailable:
        """The first `limit` errors of the load, in its order, with the total."""
        if limit <= 0:
            msg = f"limit must be positive, not {limit}"
            raise ValueError(msg)
        return self._read(lambda con: _errors(con, limit))

    def _read[T](self, read: Callable[[sqlite3.Connection], T]) -> T | MirrorUnavailable:
        con = open_mirror(self._path)
        if isinstance(con, MirrorUnavailable):
            return con
        try:
            return read(con)
        except _NotAMirror as e:
            return MirrorUnavailable("not_a_mirror", str(e))
        except sqlite3.Error as e:
            if (outcome := unavailable(e)) is not None:
                return outcome
            raise
        finally:
            con.close()


def _require(con: sqlite3.Connection, table: str) -> None:
    """A file without the base table is not a mirror (asked of the schema, not read from an error's text)."""
    if con.execute(TABLE_SQL, (table,)).fetchone() is None:
        msg = f"the file has no table {table}"
        raise _NotAMirror(msg)


def _info(con: sqlite3.Connection) -> MirrorInfo:
    _require(con, "m_meta")
    found: dict[str, str] = {}
    for key, value in con.execute(META_SQL):
        if key in found:
            msg = f"meta has {key} twice"
            raise _NotAMirror(msg)
        found[str(key)] = str(value)
    for key in META_KEYS:
        if key not in found:
            msg = f"meta has no {key}"
            raise _NotAMirror(msg)
    if not found["surface_version"].isdecimal():
        msg = f"meta surface_version is not a number: {found['surface_version']!r}"
        raise _NotAMirror(msg)
    if found["build_complete"] not in ("0", "1"):
        msg = f"meta build_complete is not 0 or 1: {found['build_complete']!r}"
        raise _NotAMirror(msg)
    return MirrorInfo(
        surface_version=int(found["surface_version"]),
        ledger_version=found["ledger_version"],
        fingerprint=found["fingerprint"],
        export_version=found["export_version"],
        built_at=found["built_at"],
        build_complete=found["build_complete"] == "1",
    )


def _errors(con: sqlite3.Connection, limit: int) -> LedgerErrors:
    _require(con, "m_ledger_errors")
    (total,) = con.execute(ERROR_COUNT_SQL).fetchone()
    errors = tuple(
        LedgerError(
            id=int(id_),
            source_file=None if source is None else str(source),
            line_number=None if line is None else int(line),
            message=UntrustedText(str(message)),
            error_type=str(error_type),
            entry_json=None if entry is None else UntrustedText(str(entry)),
        )
        for id_, source, line, message, error_type, entry in con.execute(ERRORS_SQL, (limit,))
    )
    return LedgerErrors(int(total), errors)
