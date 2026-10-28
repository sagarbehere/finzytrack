"""The read-only open of a mirror file (ADR-0015, ADR-0016; INV-05: a read never writes the mirror)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.mirror.files import MirrorUnavailable, open_mirror
from tests.mirror.support import make_mirror


def test_the_connection_is_read_only_and_query_only(tmp_path: Path) -> None:
    con = open_mirror(str(make_mirror(tmp_path / "m.db")))
    assert isinstance(con, sqlite3.Connection)
    try:
        assert con.execute("PRAGMA query_only").fetchone() == (1,)
        con.execute("PRAGMA query_only = 0")  # even then the file itself is open read-only (`mode=ro`)
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            con.execute("INSERT INTO m_meta VALUES ('k', 'v')")
    finally:
        con.close()


def test_a_missing_file_is_never_created(tmp_path: Path) -> None:
    path = tmp_path / "gone.db"
    result = open_mirror(str(path))
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "cannot_open"
    assert not path.exists()


def test_a_relative_path_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="absolute"):
        open_mirror("m.db")
