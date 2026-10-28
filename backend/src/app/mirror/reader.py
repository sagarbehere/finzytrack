"""Typed reads of the mirror for the app's own use cases (ARCHITECTURE.md §2.2, §9.1; ADR-0011, ADR-0015).

The app's SQL is fixed text over the base tables (`m_*`, `design/specs/mirror/README.md` "Three layers"), run on a
read-only connection (`files.open_mirror`); user SQL never comes here (`query.QueryRunner`). Each read opens and
closes its own connection: nothing stays resident (INV-10) and no read loads the ledger (ADR-0011). Text that came
from the ledger is `UntrustedText` (INV-09: data, never markup). A file that is not a readable mirror, or a row that
is not of its column's kind, is `MirrorUnavailable` (EP-13).

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
from typing import Final, LiteralString, TypeGuard

from app.domain.text import UntrustedText
from app.mirror.files import (
    MirrorInfo,
    MirrorUnavailable,
    NotAMirrorError,
    OpenMirror,
    absolute,
    app_read_outcome,
    open_mirror,
    require_table,
)

__all__ = ["LedgerError", "LedgerErrors", "MirrorInfo", "Reader"]

ERROR_COUNT_SQL: Final[LiteralString] = "SELECT COUNT(*) FROM m_ledger_errors"
ERRORS_SQL: Final[LiteralString] = (
    "SELECT id, source_file, line_number, message, error_type, entry_json FROM m_ledger_errors ORDER BY id LIMIT ?"
)
MAX_LIMIT: Final = 2**63 - 1  # SQLite's largest integer: a larger limit cannot be bound


@dataclass(frozen=True, slots=True)
class LedgerError:
    """One error of the load (`ledger_errors`): a parser, booking, validation or plugin error. `line_number` and
    `source_file` are None when Beancount gives none. `source_file` is the path the build wrote (an include's name
    is ledger text, so it is `UntrustedText` too)."""

    id: int
    source_file: UntrustedText | None
    line_number: int | None
    message: UntrustedText
    error_type: str
    entry_json: UntrustedText | None


@dataclass(frozen=True, slots=True)
class LedgerErrors:
    """The first errors, in the load's order, and how many there are in all."""

    total: int
    errors: tuple[LedgerError, ...]


class Reader:
    """Reads one mirror file. Holds only its path."""

    __slots__ = ("_path",)

    def __init__(self, path: str) -> None:
        absolute(path)
        self._path = path

    def info(self) -> MirrorInfo | MirrorUnavailable:
        """The mirror's own facts, as checked by the open (`files.read_info`)."""
        return self._read(lambda mirror: mirror.info)

    def ledger_errors(self, limit: int) -> LedgerErrors | MirrorUnavailable:
        """The first `limit` errors of the load, in its order, with the total. A limit outside 1 to 2**63 - 1 is a
        caller error (`ValueError`)."""
        if not 0 < limit <= MAX_LIMIT:
            msg = f"limit must be between 1 and {MAX_LIMIT}, not {limit}"
            raise ValueError(msg)
        return self._read(lambda mirror: _errors(mirror.con, limit))

    def _read[T](self, read: Callable[[OpenMirror], T]) -> T | MirrorUnavailable:
        mirror = open_mirror(self._path)
        if isinstance(mirror, MirrorUnavailable):
            return mirror
        try:
            return read(mirror)
        except NotAMirrorError as e:
            return MirrorUnavailable("not_a_mirror", str(e))
        except sqlite3.Error as e:
            if (outcome := app_read_outcome(e)) is not None:
                return outcome
            raise
        finally:
            mirror.con.close()


def _text(value: object) -> TypeGuard[str]:
    return isinstance(value, str)


def _int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _errors(con: sqlite3.Connection, limit: int) -> LedgerErrors:
    require_table(con, "m_ledger_errors")
    (total,) = con.execute(ERROR_COUNT_SQL).fetchone()
    errors: list[LedgerError] = []
    for id_, source, line, message, error_type, entry in con.execute(ERRORS_SQL, (limit,)):
        if not (
            _int(id_)
            and (source is None or _text(source))
            and (line is None or _int(line))
            and _text(message)
            and _text(error_type)
            and (entry is None or _text(entry))
        ):
            msg = f"ledger_errors has a row whose values are not of their columns' kinds (id {id_!r})"
            raise NotAMirrorError(msg)
        errors.append(
            LedgerError(
                id=id_,
                source_file=None if source is None else UntrustedText(source),
                line_number=line,
                message=UntrustedText(message),
                error_type=error_type,
                entry_json=None if entry is None else UntrustedText(entry),
            )
        )
    return LedgerErrors(int(total), tuple(errors))
