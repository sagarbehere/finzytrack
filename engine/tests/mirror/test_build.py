"""The build writes a fresh, complete file or nothing (ADR-0015 "Build a new generation file ... A failed build never
publishes"; INV-05; README "Exact numbers": a sum or product that would need rounding fails the build, INV-06,
INV-22). The file is read back by plain `sqlite3`, as an outside tool would (ADR-0015 "Outside tools may open the
current generation read-only"; README "Three layers").
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ledger_engine.mirror.build import BuildFailed, build
from ledger_engine.mirror.core import schema

from .support import STAMP, TODAY, build_text, load_root, rows, try_build, write_ledger

pytestmark = pytest.mark.inv("INV-05")

HUGE = "10000000000000000000000000000000000000000"  # 1E+40
TINY = "0.000000000000000000000000000001"  # 1E-30: with HUGE, a sum of 71 significant digits


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    loaded = load_root(write_ledger(tmp_path / "ledger", "2020-01-01 open Assets:Bank\n"))
    out = tmp_path / "mirror.db"
    out.write_bytes(b"not a mirror")
    with pytest.raises(FileExistsError):
        build(loaded, str(out), TODAY, STAMP)
    assert out.read_bytes() == b"not a mirror"


def test_the_built_file_leaves_no_journal_beside_it(tmp_path: Path) -> None:
    build_text(tmp_path, "2020-01-01 open Assets:Bank\n").close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger", "mirror.db"]
    con = sqlite3.connect(tmp_path / "mirror.db")
    assert con.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def assert_fails_and_leaves_nothing(tmp_path: Path, text: str, where: str) -> None:
    result = try_build(tmp_path, text)
    assert isinstance(result, BuildFailed), result
    assert result.reason == "inexact"
    assert where in result.detail
    assert not (tmp_path / "mirror.db").exists()
    assert not (tmp_path / "mirror.db-journal").exists()


def test_a_balance_that_needs_rounding_fails_the_build(tmp_path: Path) -> None:
    text = (
        "2020-01-01 open Assets:Bank\n2020-01-01 open Equity:Opening\n"
        f'2020-04-01 * "huge"\n  Assets:Bank  {HUGE} USD\n  Equity:Opening\n'
        f'2020-04-02 * "tiny"\n  Assets:Bank  {TINY} USD\n  Equity:Opening\n'
    )
    assert_fails_and_leaves_nothing(tmp_path, text, "account_balances")


def test_a_commodity_volume_that_needs_rounding_fails_the_build(tmp_path: Path) -> None:
    # Two accounts, so no balance sums HUGE and TINY; only the volume of USD does. Dated after TODAY, so
    # account_balances does not see them at all.
    text = (
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Cash\n2020-01-01 open Equity:Opening\n"
        f'2027-04-01 * "huge"\n  Assets:Bank  {HUGE} USD\n  Equity:Opening\n'
        f'2027-04-02 * "tiny"\n  Assets:Cash  {TINY} USD\n  Equity:Opening\n'
    )
    assert_fails_and_leaves_nothing(tmp_path, text, "commodity_usage")


def test_a_book_value_that_needs_rounding_fails_the_build(tmp_path: Path) -> None:
    units, cost = "1234567890123456789012345678901234", "1.234567890123456789012345678901234"  # 34 digits each
    text = (
        "2020-01-01 open Assets:Broker\n2020-01-01 open Equity:Opening\n"
        f'2021-01-01 * "buy"\n  Assets:Broker  {units} VTI {{{cost} USD}}\n  Equity:Opening\n'
    )
    assert_fails_and_leaves_nothing(tmp_path, text, "lots")


def test_a_write_that_fails_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fault injection at the shell's seam: the DDL ends in a statement SQLite refuses, after every table exists.
    loaded = load_root(write_ledger(tmp_path / "ledger", "2020-01-01 open Assets:Bank\n"))
    good = schema.statements()
    monkeypatch.setattr(schema, "statements", lambda: [*good, "CREATE TABLE broken ("])
    out = tmp_path / "mirror.db"
    with pytest.raises(sqlite3.OperationalError):
        build(loaded, str(out), TODAY, STAMP)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger"]


def test_sums_within_64_digits_are_exact(tmp_path: Path) -> None:
    # The near miss of the rounding tests: 41 + 30 digits apart is too many, 34 + 30 is not.
    small_huge = "1000000000000000000000000000000000"  # 1E+33
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Bank\n2020-01-01 open Equity:Opening\n"
        f'2020-04-01 * "huge"\n  Assets:Bank  {small_huge} USD\n  Equity:Opening\n'
        f'2020-04-02 * "tiny"\n  Assets:Bank  {TINY} USD\n  Equity:Opening\n',
    )
    assert rows(con, "SELECT balance FROM account_balances WHERE account = 'Assets:Bank'") == [(small_huge + TINY[1:],)]


def test_an_outside_reader_reads_every_view(tmp_path: Path) -> None:
    build_text(
        tmp_path,
        "2020-01-01 open Assets:Bank\n2020-01-01 open Income:Salary\n"
        '2021-01-01 * "in"\n  Assets:Bank  0.00 USD\n  Income:Salary  0.00 USD\n',
    ).close()
    con = sqlite3.connect(f"file:{tmp_path / 'mirror.db'}?mode=ro", uri=True)
    views = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name")]
    assert len(views) == 35  # 17 public views (meta has none) and 18 compatibility views
    for view in views:
        con.execute(f'SELECT * FROM "{view}"').fetchall()  # noqa: S608 (names read from the file itself)
    # Money is TEXT, so a v0.2.2 query compares text: '0.00' > 0 is true (README "Traps"), as in v0.2.2.
    assert con.execute("SELECT count(*) FROM postings WHERE amount > 0").fetchone() == (2,)
    assert con.execute("SELECT count(*) FROM postings WHERE CAST(amount AS REAL) > 0").fetchone() == (0,)
