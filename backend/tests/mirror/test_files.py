"""The read-only open of a mirror file (ADR-0015, ADR-0016; INV-05: a read never writes the mirror)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.mirror.files import MirrorInfo, MirrorUnavailable, OpenMirror, open_mirror, pin
from app.mirror.query import QueryRows, QueryRunner, UserSql
from app.mirror.reader import Reader
from tests.mirror.support import META, make_mirror


def test_the_connection_is_read_only_and_query_only(tmp_path: Path) -> None:
    mirror = open_mirror(str(make_mirror(tmp_path / "m.db")))
    assert isinstance(mirror, OpenMirror)
    assert isinstance(mirror.info, MirrorInfo)  # the open checked the file is a mirror
    con = mirror.con
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


# -- the reader's pin (publish.md §7; S0087, BL-0113) -----------------------------------------------------------------

GEN1 = "gen-1-" + "a" * 16 + ".db"
GEN2 = "gen-2-" + "b" * 16 + ".db"


def published(tmp_path: Path) -> Path:
    """A mirror folder as the engine's publish leaves it: two generations and `current.db` on the first."""
    folder = tmp_path / "workspaces" / "k1" / "mirror"
    folder.mkdir(parents=True)
    for name, version in ((GEN1, "1/sha256:" + "1" * 64), (GEN2, "2/sha256:" + "2" * 64)):
        meta = [(k, version if k == "ledger_version" else v) for k, v in META]
        make_mirror(folder / name, meta=meta)
    (folder / "current.db").symlink_to(GEN1)
    return folder


def swap(folder: Path, name: str) -> None:
    """The engine's swap: a temporary link renamed over `current.db`."""
    (folder / "current.db.t.tmp").symlink_to(name)
    (folder / "current.db.t.tmp").rename(folder / "current.db")


def test_pin_resolves_current_once_and_names_the_generation(tmp_path: Path) -> None:
    folder = published(tmp_path)
    assert pin(str(folder)) == str(folder / GEN1)


def test_a_request_pinned_before_a_publish_reads_its_generation_after_it(tmp_path: Path) -> None:
    folder = published(tmp_path)
    pinned = pin(str(folder))
    assert isinstance(pinned, str)
    swap(folder, GEN2)
    info = Reader(pinned).info()
    assert isinstance(info, MirrorInfo)
    assert info.ledger_version.startswith("1/")  # the pinned generation, never the new one
    rows = QueryRunner(pinned).run_user_sql(UserSql("SELECT value FROM meta WHERE key = 'ledger_version'"))
    assert isinstance(rows, QueryRows)
    assert str(rows.rows[0][0]).startswith("1/")
    again = pin(str(folder))
    assert again == str(folder / GEN2)  # the next request resolves again


def test_a_pinned_generation_that_is_gone_is_cannot_open(tmp_path: Path) -> None:
    folder = published(tmp_path)
    pinned = pin(str(folder))
    assert isinstance(pinned, str)
    swap(folder, GEN2)
    (folder / GEN1).unlink()  # the cleanup removed it (or the OS purged the cache): "try again"
    out = Reader(pinned).info()
    assert isinstance(out, MirrorUnavailable)
    assert out.reason == "cannot_open"


@pytest.mark.parametrize("damage", ["no_folder", "no_link", "not_a_generation", "absolute", "a_file", "link_at_mirror"])
def test_no_published_mirror_is_not_published(tmp_path: Path, damage: str) -> None:
    folder = published(tmp_path)
    if damage == "no_folder":
        folder = tmp_path / "nowhere"
    elif damage == "no_link":
        (folder / "current.db").unlink()
    elif damage == "not_a_generation":
        swap(folder, "../../other.db")
    elif damage == "absolute":
        swap(folder, str(folder / GEN1))
    elif damage == "a_file":
        (folder / "current.db").unlink()
        make_mirror(folder / "current.db")
    else:
        folder.rename(tmp_path / "away")
        folder.symlink_to(tmp_path / "away")  # a whole mirror behind a link at `mirror/`: never followed
    out = pin(str(folder))
    assert isinstance(out, MirrorUnavailable), out
    assert out.reason == "not_published"


def test_pin_takes_an_absolute_folder_only() -> None:
    with pytest.raises(ValueError, match="absolute"):
        pin("mirror")
