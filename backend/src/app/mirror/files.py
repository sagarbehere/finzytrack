"""Open a mirror file read-only (ADR-0015, ADR-0016; INV-05).

A read never writes the mirror and never creates a file: the connection is opened with SQLite's `mode=ro` URI (a
missing file is an error, not a new empty database) and `query_only` is set before anything else runs. A file that
cannot be opened, or that is not an SQLite database, is an expected outcome (the OS may purge the cache, ADR-0015),
so it comes back as `MirrorUnavailable` (EP-13); the caller decides on a rebuild.

TODO(BL-0113): the path is a generation file the caller names; once the publish exists, a request pins the current
generation and reads that path for its whole life.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = ["MirrorUnavailable", "absolute", "open_mirror", "unavailable"]

type UnavailableReason = Literal[
    "cannot_open",  # no file there, or the process may not read it
    "not_a_mirror",  # a file, but not an SQLite database, or not one with the mirror's tables
]


@dataclass(frozen=True, slots=True)
class MirrorUnavailable:
    """The mirror file could not be read. `detail` is SQLite's message or the missing fact, for the log."""

    reason: UnavailableReason
    detail: str


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


def open_mirror(path: str) -> sqlite3.Connection | MirrorUnavailable:
    """A read-only connection to the mirror file at `path` (absolute), with its schema read; the caller closes it.

    Raises `ValueError` for a relative path (a caller error) and re-raises any SQLite error that is not about the
    file being unreadable.
    """
    uri = file_uri(path)
    try:
        con = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as e:
        if (outcome := unavailable(e)) is not None:
            return outcome
        raise
    try:
        con.execute("PRAGMA query_only = 1")
        con.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()  # reads the header: a non-database fails here
    except BaseException as e:
        con.close()
        if isinstance(e, sqlite3.Error) and (outcome := unavailable(e)) is not None:
            return outcome
        raise
    return con
