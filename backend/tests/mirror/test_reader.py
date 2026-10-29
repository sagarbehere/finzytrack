"""Typed reads of the mirror (ARCHITECTURE.md §2.2 `mirror/reader.py`, §9.1): the mirror's own facts and the load's
errors, the first read the API serves (CAP-NOTIFY-SERVER, M2). Internal SQL over the base tables, on a read-only
connection; no ledger load (ADR-0011)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.domain.text import UntrustedText
from app.mirror.files import OUT_OF_MEMORY, MirrorUnavailable
from app.mirror.reader import LedgerError, LedgerErrors, MirrorInfo, Reader
from tests.mirror.support import META, make_mirror, starve


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
        source_file=UntrustedText("/ledger/main.beancount"),
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
    assert isinstance(Reader(str(tmp_path / "m.db")).ledger_errors(2**63 - 1), LedgerErrors)
    for limit in (0, 2**63):  # verifier S0052 F5: 2**63 cannot be bound; a caller error, as 0 is
        with pytest.raises(ValueError, match="limit"):
            Reader(str(tmp_path / "m.db")).ledger_errors(limit)


def test_source_file_is_untrusted_text(tmp_path: Path) -> None:
    """Verifier S0052 F8: an include's file name is ledger text (INV-09)."""
    path = make_mirror(tmp_path / "m.db", errors=[("<img src=x onerror=alert(1)>.bean", 1, "m", None, "E")])
    errors = Reader(str(path)).ledger_errors(5)
    assert isinstance(errors, LedgerErrors)
    assert errors.errors[0].source_file == UntrustedText("<img src=x onerror=alert(1)>.bean")


def changed(tmp_path: Path, sql: str, **kwargs: object) -> Path:
    path = make_mirror(tmp_path / "m.db", **kwargs)  # type: ignore[arg-type]
    con = sqlite3.connect(path)
    try:
        con.executescript(sql)
        con.commit()
    finally:
        con.close()
    return path


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE m_meta SET value = NULL WHERE key = 'ledger_version'",
        "UPDATE m_meta SET value = '' WHERE key = 'ledger_version'",
        "UPDATE m_meta SET value = x'00' WHERE key = 'fingerprint'",
        "INSERT INTO m_meta VALUES (NULL, 'v')",
        f"UPDATE m_meta SET value = '{'9' * 5000}' WHERE key = 'surface_version'",  # noqa: S608 (constant text)
        "UPDATE m_meta SET value = '\u0661' WHERE key = 'surface_version'",  # an Arabic-Indic digit: `int()` takes it
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 70)"
        " INSERT INTO m_meta SELECT 'later_key_' || x, 'v' FROM n",
        "DROP TABLE m_meta; CREATE TABLE m_meta (k TEXT)",
    ],
)
def test_info_refuses_a_meta_that_is_null_empty_oversized_or_of_another_shape(tmp_path: Path, sql: str) -> None:
    """Verifier S0052 F7: each is `not_a_mirror`, never a wrong value or an exception."""
    result = Reader(str(changed(tmp_path, sql))).info()
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE m_ledger_errors SET line_number = 'abc' WHERE id = 1",
        "UPDATE m_ledger_errors SET id = NULL WHERE id = 1",
        "UPDATE m_ledger_errors SET message = NULL WHERE id = 1",
        "UPDATE m_ledger_errors SET error_type = x'00' WHERE id = 1",
        "UPDATE m_ledger_errors SET source_file = x'2f' WHERE id = 1",
        "DROP TABLE m_ledger_errors; CREATE TABLE m_ledger_errors (id INTEGER)",
    ],
)
def test_ledger_errors_refuse_a_row_of_another_kind(tmp_path: Path, sql: str) -> None:
    """Verifier S0052 F7: a row whose values are not of their columns' kinds is `not_a_mirror`, never an exception."""
    result = Reader(str(changed(tmp_path, sql))).ledger_errors(5)
    assert isinstance(result, MirrorUnavailable), result
    assert result.reason == "not_a_mirror"


def test_an_empty_file_is_not_a_mirror(tmp_path: Path) -> None:
    """Verifier S0052 F4: SQLite reads a 0-byte file as an empty database; it is no mirror."""
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    for result in (Reader(str(path)).info(), Reader(str(path)).ledger_errors(10)):
        assert isinstance(result, MirrorUnavailable), result
        assert result.reason == "not_a_mirror"


def test_reads_never_change_the_file(tmp_path: Path) -> None:
    path = make_mirror(tmp_path / "m.db")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = Reader(str(path))
    reader.info()
    reader.ledger_errors(10)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.db"]


@pytest.mark.parametrize(
    ("at", "read"),
    [
        ("connect", "info"),
        ("PRAGMA query_only", "info"),
        ("SELECT key, value FROM m_meta", "info"),
        ("connect", "errors"),
        ("SELECT 1 FROM sqlite_schema", "errors"),  # the open's and the errors' table checks
        ("SELECT COUNT(*) FROM m_ledger_errors", "errors"),
        ("SELECT id, source_file", "errors"),
    ],
)
def test_memory_used_up_is_unavailable_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: str, read: str
) -> None:
    """Verifier S0052 R2-F1 (EP-13): SQLite's heap limit is process-wide, so the queries running at the same moment
    can use it up under the app's own reads. `info()` feeds every envelope's `ledger_version`: it must come back as
    an outcome the caller can answer ("try again"), never as an exception."""
    reader = Reader(str(make_mirror(tmp_path / "m.db")))
    starve(monkeypatch, at=at)
    result = reader.info() if read == "info" else reader.ledger_errors(10)
    assert result == MirrorUnavailable("out_of_memory", OUT_OF_MEMORY)
