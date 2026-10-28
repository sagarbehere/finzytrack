"""Typed reads of the mirror (ARCHITECTURE.md §2.2 `mirror/reader.py`, §9.1): the mirror's own facts and the load's
errors, the first read the API serves (CAP-NOTIFY-SERVER, M2). Internal SQL over the base tables, on a read-only
connection; no ledger load (ADR-0011)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.domain.text import UntrustedText
from app.mirror.files import MirrorUnavailable
from app.mirror.reader import LedgerError, LedgerErrors, MirrorInfo, Reader
from tests.mirror.support import META, make_mirror


def info_of(path: Path) -> MirrorInfo | MirrorUnavailable:
    return Reader(str(path)).info()


def test_info_decodes_the_builds_meta(tmp_path: Path) -> None:
    info = info_of(make_mirror(tmp_path / "m.db"))
    assert info == MirrorInfo(
        surface_version=1,
        ledger_version="7:abc",
        fingerprint="{}",
        export_version="e1",
        built_at="2026-10-28T09:00:00+00:00",
        build_complete=False,
    )
    complete = [(k, "1" if k == "build_complete" else v) for k, v in META]
    assert isinstance(i := info_of(make_mirror(tmp_path / "c.db", meta=complete)), MirrorInfo)
    assert i.build_complete is True


@pytest.mark.parametrize(
    ("meta", "says"),
    [
        ([(k, v) for k, v in META if k != "ledger_version"], "ledger_version"),
        ([*META, ("ledger_version", "8:def")], "ledger_version"),
        ([(k, "yes" if k == "build_complete" else v) for k, v in META], "build_complete"),
        ([(k, "one" if k == "surface_version" else v) for k, v in META], "surface_version"),
        ([], "surface_version"),
    ],
)
def test_info_refuses_a_meta_that_is_missing_doubled_or_malformed(
    tmp_path: Path, meta: list[tuple[str, str]], says: str
) -> None:
    result = info_of(make_mirror(tmp_path / "m.db", meta=meta))
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"
    assert says in result.detail


def test_a_database_that_is_not_a_mirror_is_unavailable(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "other.db"
    sqlite3.connect(path).close()
    for result in (Reader(str(path)).info(), Reader(str(path)).ledger_errors(10)):
        assert isinstance(result, MirrorUnavailable), result
        assert result.reason == "not_a_mirror"


def test_a_missing_mirror_is_unavailable_and_not_created(tmp_path: Path) -> None:
    path = tmp_path / "gone.db"
    for result in (Reader(str(path)).info(), Reader(str(path)).ledger_errors(10)):
        assert isinstance(result, MirrorUnavailable), result
        assert result.reason == "cannot_open"
    assert not path.exists()


def test_ledger_errors_come_in_order_with_their_total(tmp_path: Path) -> None:
    errors = Reader(str(make_mirror(tmp_path / "m.db"))).ledger_errors(10)
    assert isinstance(errors, LedgerErrors)
    assert errors.total == 3
    assert errors.errors[0] == LedgerError(
        id=1,
        source_file="/ledger/main.beancount",
        line_number=3,
        message=UntrustedText("Balance failed for 'Assets:Bank'"),
        error_type="BalanceError",
        entry_json=UntrustedText('["2026-01-01"]'),
    )
    second, third = errors.errors[1:]
    assert (second.line_number, second.entry_json) == (None, None)  # NULL stays "none", not 0 or ""
    assert second.message == UntrustedText("Unbalanced <b>transaction</b>")  # ledger text is data, never markup
    assert (third.source_file, third.line_number) == (None, 0)


def test_ledger_errors_are_bounded_by_the_limit(tmp_path: Path) -> None:
    errors = Reader(str(make_mirror(tmp_path / "m.db"))).ledger_errors(2)
    assert isinstance(errors, LedgerErrors)
    assert (errors.total, [e.id for e in errors.errors]) == (3, [1, 2])
    with pytest.raises(ValueError, match="limit"):
        Reader(str(tmp_path / "m.db")).ledger_errors(0)


def test_reads_never_change_the_file(tmp_path: Path) -> None:
    path = make_mirror(tmp_path / "m.db")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = Reader(str(path))
    reader.info()
    reader.ledger_errors(10)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.db"]
