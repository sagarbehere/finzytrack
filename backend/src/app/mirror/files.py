"""Open a mirror file read-only and check that it is one (ADR-0015, ADR-0016; INV-05).

A read never writes the mirror and never creates a file. Before SQLite sees the file, its 100-byte header must be an
SQLite database in rollback-journal mode: the build writes that mode, and SQLite reading a WAL-mode file would
create `-wal` and `-shm` files beside it, even read-only. The connection is then opened with SQLite's `mode=ro` URI
(a missing file is an error, not a new empty database) and `query_only` is set before anything else runs. Last, the
mirror's own facts (`m_meta`) are read and checked, so no caller answers from a file that is not a finished mirror.

A file that cannot be opened, is not an SQLite database, still has a hot journal (its build was stopped) or has no
well-formed `m_meta` is an expected outcome (the OS may purge the cache, ADR-0015), so it comes back as
`MirrorUnavailable` (EP-13); the caller decides on a rebuild.

TODO(BL-0113): the path is a generation file the caller names; once the publish exists, a request pins the current
generation and reads that path for its whole life.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, LiteralString

__all__ = ["MirrorInfo", "MirrorUnavailable", "NotAMirrorError", "OpenMirror", "absolute", "open_mirror", "unavailable"]

type UnavailableReason = Literal[
    "cannot_open",  # no file there, or the process may not read it
    "not_a_mirror",  # a file, but not an SQLite database in rollback mode, a stopped build, or no well-formed m_meta
]

HEADER: Final = b"SQLite format 3\x00"
HEADER_BYTES: Final = 100
ROLLBACK: Final = 1  # header bytes 18 and 19, the file format's write and read versions: 1 rollback, 2 WAL

TABLE_SQL: Final[LiteralString] = "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?"
MAX_META_ROWS: Final = 64  # the build writes six keys; a later build may add some, never this many
META_SQL: Final[LiteralString] = "SELECT key, value FROM m_meta LIMIT 65"  # MAX_META_ROWS + 1: one more says "too many"
META_KEYS: Final = ("surface_version", "ledger_version", "fingerprint", "export_version", "built_at", "build_complete")
SURFACE_VERSION: Final = re.compile(r"[0-9]{1,9}")  # ASCII digits only; `int()` would also take other scripts' digits


@dataclass(frozen=True, slots=True)
class MirrorUnavailable:
    """The mirror file could not be read. `detail` is SQLite's message or the missing fact, for the log."""

    reason: UnavailableReason
    detail: str


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
class OpenMirror:
    """A read-only connection to a checked mirror file, and its `meta`. The caller closes `con`."""

    con: sqlite3.Connection
    info: MirrorInfo


class NotAMirrorError(Exception):
    """The file's contents are not a mirror's: raised by a check of the app's own reads, turned into
    `MirrorUnavailable("not_a_mirror")` at the edge of the read."""


def absolute(path: str) -> Path:
    """`path` as a `Path`; a relative path is a caller error (`ValueError`): a mirror lives in the app's cache dir."""
    p = Path(path)
    if not p.is_absolute():
        msg = f"a mirror path must be absolute, not {path!r}"
        raise ValueError(msg)
    return p


def file_uri(path: str) -> str:
    """The read-only URI of an absolute path. `as_uri` percent-encodes `?`, `#` and `%`, so a folder name can never
    add URI parameters such as `mode=rwc`."""
    return f"{absolute(path).as_uri()}?mode=ro"


def unavailable(error: sqlite3.Error) -> MirrorUnavailable | None:
    """The outcome for an SQLite error that says the file is not a readable mirror; None for any other error."""
    # The primary code (SQLITE_CANTOPEN covers its extended codes); an error Python raised itself has none.
    code = getattr(error, "sqlite_errorcode", -1) & 0xFF
    if code == sqlite3.SQLITE_CANTOPEN:
        return MirrorUnavailable("cannot_open", str(error))
    if code in (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT):
        return MirrorUnavailable("not_a_mirror", str(error))
    return None


def app_read_outcome(error: sqlite3.Error) -> MirrorUnavailable | None:
    """The outcome for an error of the app's own fixed SQL. On top of `unavailable`: SQLITE_ERROR means the file's
    tables are not the mirror's (a missing column), and SQLITE_READONLY, on a read, means SQLite found a hot journal
    it may not roll back (`mode=ro`): a build was stopped half-way."""
    if (outcome := unavailable(error)) is not None:
        return outcome
    code = getattr(error, "sqlite_errorcode", -1) & 0xFF
    if code in (sqlite3.SQLITE_ERROR, sqlite3.SQLITE_READONLY):
        return MirrorUnavailable("not_a_mirror", str(error))
    return None


def _check_header(path: Path) -> MirrorUnavailable | None:
    try:
        with path.open("rb") as f:
            header = f.read(HEADER_BYTES)
    except OSError as e:
        return MirrorUnavailable("cannot_open", e.strerror or type(e).__name__)
    if len(header) < HEADER_BYTES or not header.startswith(HEADER):
        return MirrorUnavailable("not_a_mirror", "the file is not an SQLite database")
    if header[18] != ROLLBACK or header[19] != ROLLBACK:
        return MirrorUnavailable("not_a_mirror", "the file is not in rollback-journal mode")
    return None


def open_mirror(path: str) -> OpenMirror | MirrorUnavailable:
    """A read-only connection to the mirror file at `path` (absolute), with its `meta` read and checked.

    Raises `ValueError` for a relative path (a caller error) and re-raises any SQLite error that is not about the
    file being unreadable or not a mirror (a disk error, SQLite out of memory).
    """
    uri = file_uri(path)
    if (bad := _check_header(Path(path))) is not None:
        return bad
    try:
        con = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as e:
        if (outcome := unavailable(e)) is not None:
            return outcome
        raise
    try:
        con.execute("PRAGMA query_only = 1")
        info = read_info(con)
    except BaseException as e:
        con.close()
        if isinstance(e, NotAMirrorError):
            return MirrorUnavailable("not_a_mirror", str(e))
        if isinstance(e, sqlite3.Error) and (outcome := app_read_outcome(e)) is not None:
            return outcome
        raise
    return OpenMirror(con, info)


def require_table(con: sqlite3.Connection, table: str) -> None:
    """A file without the base table is not a mirror (asked of the schema, not read from an error's text)."""
    if con.execute(TABLE_SQL, (table,)).fetchone() is None:
        msg = f"the file has no table {table}"
        raise NotAMirrorError(msg)


def read_info(con: sqlite3.Connection) -> MirrorInfo:
    """The mirror's `meta`. Every key must be there once, as text with a non-empty value of its kind; other keys (a
    later build's) are allowed, up to `MAX_META_ROWS` rows in all."""
    require_table(con, "m_meta")
    found: dict[str, str] = {}
    rows = con.execute(META_SQL).fetchall()
    if len(rows) > MAX_META_ROWS:
        msg = f"meta has more than {MAX_META_ROWS} rows"
        raise NotAMirrorError(msg)
    for key, value in rows:
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            msg = f"meta has a row that is not a key with a text value: {key!r}"
            raise NotAMirrorError(msg)
        if key in found:
            msg = f"meta has {key} twice"
            raise NotAMirrorError(msg)
        found[key] = value
    for key in META_KEYS:
        if key not in found:
            msg = f"meta has no {key}"
            raise NotAMirrorError(msg)
    if not SURFACE_VERSION.fullmatch(found["surface_version"]):
        msg = f"meta surface_version is not a number of at most 9 digits: {found['surface_version'][:20]!r}"
        raise NotAMirrorError(msg)
    if found["build_complete"] not in ("0", "1"):
        msg = f"meta build_complete is not 0 or 1: {found['build_complete'][:20]!r}"
        raise NotAMirrorError(msg)
    return MirrorInfo(
        surface_version=int(found["surface_version"]),
        ledger_version=found["ledger_version"],
        fingerprint=found["fingerprint"],
        export_version=found["export_version"],
        built_at=found["built_at"],
        build_complete=found["build_complete"] == "1",
    )
