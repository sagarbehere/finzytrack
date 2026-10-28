"""The build writes a fresh, complete file or nothing (ADR-0015 "Build a new generation file ... A failed build never
publishes"; INV-05; README "Exact numbers": a sum or product that would need rounding fails the build, INV-06,
INV-22). The file is read back by plain `sqlite3`, as an outside tool would (ADR-0015 "Outside tools may open the
current generation read-only"; README "Three layers").
"""

from __future__ import annotations

import decimal
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_engine.mirror.build import BuildFailed, Built, build
from ledger_engine.mirror.core import rows as row_builders
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


# ---- more than 28 significant digits (S0050 verifier F1): Python's default context keeps 28, the mirror 64 ----------

LONG = "123456789012345678901234567.891"  # 30 significant digits


def exact_sum(numbers: list[str]) -> str:
    """The sum of the magnitudes, with digits enough never to round (the test's own oracle)."""
    with decimal.localcontext(prec=200):
        return format(sum((Decimal(n).copy_abs() for n in numbers), Decimal(0)), "f")


def test_a_units_number_over_28_digits_is_summed_exactly(tmp_path: Path) -> None:
    # Beancount keeps the written positive number whole; its parser rounds the negated one to 28 digits, so the
    # oracle reads both legs from the postings table instead of from the text. Reading the rounded leg back is a
    # known deviation from "Exact numbers" (BL-0123, open owner question): this test pins only the build's own sum;
    # test_a_written_negative_number_keeps_its_digits holds the written value as a strict xfail.
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Wallet\n2020-01-01 open Equity:Opening\n"
        f'2021-01-01 * "x"\n  Assets:Wallet  {LONG} ETH\n  Equity:Opening  -{LONG} ETH\n',
    )
    legs = [r[0] for r in rows(con, "SELECT amount FROM postings WHERE currency = 'ETH' ORDER BY posting_id")]
    assert legs[0] == LONG
    volume = rows(con, "SELECT total_volume FROM commodity_usage WHERE code = 'ETH'")
    assert volume == [(exact_sum(legs),)]
    assert len(volume[0][0].replace(".", "")) > 28  # more digits than Python's default context keeps
    assert rows(con, "SELECT balance FROM account_balances WHERE account = 'Assets:Wallet'") == [(LONG,)]


# An 18-decimal token: 29 significant digits (S0050 verifier R2-F1, repro test_written_negative_rounded.py).
SHIB_18 = "12345678901.123456789012345678"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BL-0123: Beancount's parser rounds a written negative number past 28 digits; owner question open",
)
def test_a_written_negative_number_keeps_its_digits(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Wallet\n2020-01-01 open Equity:Opening\n"
        f'2021-01-01 * "buy"\n  Assets:Wallet  {SHIB_18} SHIB\n  Equity:Opening  -{SHIB_18} SHIB\n',
    )
    assert rows(con, "SELECT count(*) FROM ledger_errors") == [(0,)]
    assert rows(con, "SELECT amount FROM postings WHERE account = 'Equity:Opening'") == [("-" + SHIB_18,)]
    assert rows(con, "SELECT balance FROM account_balances WHERE account = 'Equity:Opening'") == [("-" + SHIB_18,)]


def test_a_lot_merge_that_needs_rounding_fails_the_build(tmp_path: Path) -> None:
    # The NONE booking keeps a negative lot at another cost (dated first), so the account balance cancels to a small
    # exact number while Beancount's inventory merges the two lots at cost 1 USD into 1E+60 + 1E-10 (71 digits):
    # lot_rows must refuse it as BuildFailed and leave no file, never let decimal.Inexact escape (R2-F2).
    big = "1" + "0" * 60
    result = try_build(
        tmp_path,
        '2020-01-01 open Assets:Broker "NONE"\n2020-01-01 open Equity:A\n2020-01-01 open Equity:B\n'
        "2020-01-01 open Equity:C\n"
        f'2020-12-31 * "b"\n  Assets:Broker  -{big} VTI {{2 USD}}\n  Equity:B\n'
        f'2021-01-01 * "a"\n  Assets:Broker  {big} VTI {{1 USD}}\n  Equity:A\n'
        '2021-01-01 * "c"\n  Assets:Broker  0.0000000001 VTI {1 USD}\n  Equity:C\n',
    )
    assert isinstance(result, BuildFailed), result
    assert (result.reason, result.detail) == ("inexact", "lots.units_number")
    assert not (tmp_path / "mirror.db").exists()


def test_a_lot_merged_past_28_digits_is_exact(tmp_path: Path) -> None:
    # Two buys at one cost on one date are one lot in Beancount's inventory; their sum has 31 digits.
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Broker\n2020-01-01 open Equity:Opening\n"
        '2021-01-01 * "a"\n  Assets:Broker  1000000000000000000000000000 VTI {1 USD}\n'
        "  Equity:Opening  -1000000000000000000000000000 USD\n"
        '2021-01-01 * "b"\n  Assets:Broker  0.001 VTI {1 USD}\n  Equity:Opening  -0.001 USD\n',
    )
    whole = "1000000000000000000000000000.001"
    assert rows(con, "SELECT units_number, book_value FROM lots") == [(whole, whole)]
    assert rows(con, "SELECT balance FROM account_balances WHERE currency = 'VTI'") == [(whole,)]


def test_a_book_value_within_64_digits_is_exact(tmp_path: Path) -> None:
    # The near miss of the book-value rounding test: 32 x 31 digits fit in 64.
    units, cost = "12345678901234567890123456789012", "1.234567890123456789012345678901"
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Broker\n2020-01-01 open Equity:Opening\n"
        f'2021-01-01 * "buy"\n  Assets:Broker  {units} VTI {{{cost} USD}}\n  Equity:Opening\n',
    )
    with decimal.localcontext(prec=200):
        expected = format(Decimal(units) * Decimal(cost), "f")
    assert rows(con, "SELECT units_number, cost_number, book_value FROM lots") == [(units, cost, expected)]


def test_the_rows_do_no_arithmetic_in_the_default_context(tmp_path: Path) -> None:
    # The gate for the whole class: every sum, product, sign change and inventory merge the rows make runs in the
    # 64-digit context. With the thread's default context cut to one digit and rounding trapped, any operation left
    # in it raises here; comparisons and text conversion never round, so they pass.
    loaded = load_root(
        write_ledger(
            tmp_path / "ledger",
            "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n2020-01-01 open Equity:Opening\n"
            "2020-01-01 open Expenses:Food\n"
            '2021-01-01 * "a"\n  Assets:Broker  12 VTI {1.25 USD}\n  Assets:Bank  -15.00 USD\n'
            '2021-01-01 * "b"\n  Assets:Broker  13 VTI {1.25 USD}\n  Assets:Bank  -16.25 USD\n'
            '2021-02-01 * "c"\n  Assets:Broker  -5 VTI {1.25 USD}\n  Assets:Bank  6.25 USD\n'
            '2021-03-01 * "Cafe" "lunch"\n  Expenses:Food  12.34 USD\n  Assets:Bank  -12.34 USD\n'
            "2021-03-02 balance Assets:Bank  -37.34 USD\n",
        )
    )
    with decimal.localcontext(
        decimal.Context(prec=1, traps=[decimal.Rounded, decimal.Inexact, decimal.InvalidOperation])
    ):
        tables = row_builders.table_rows(loaded.entries, loaded.errors, loaded.options, TODAY, STAMP)
    assert tables["lots"] == [(1, "Assets:Broker", "20", "VTI", "1.25", "USD", "2021-01-01", None, "25.00")]
    assert tables["commodity_usage"] == [
        ("USD", 5, "62.18", "2021-01-01", "2021-03-01"),
        ("VTI", 3, "30", "2021-01-01", "2021-02-01"),
    ]


# ---- completeness (S0050 verifier F2; BL-0120, BL-0113) --------------------------------------------------------------


def test_a_build_without_commodity_rows_is_marked_incomplete(tmp_path: Path) -> None:
    # The `commodities` rows wait for BL-0120; until then no build may say it is complete, so the publish can refuse
    # it (BL-0113). Both places say so: the result the caller checks and the `meta` row a reader checks.
    loaded = load_root(write_ledger(tmp_path / "ledger", "2020-01-01 commodity VTI\n2020-01-01 open Assets:Bank\n"))
    result = build(loaded, str(tmp_path / "mirror.db"), TODAY, STAMP)
    assert isinstance(result, Built), result
    assert result.complete is False
    con = sqlite3.connect(f"file:{tmp_path / 'mirror.db'}?mode=ro", uri=True)
    assert rows(con, "SELECT count(*) FROM commodities") == [(0,)]
    assert rows(con, "SELECT value FROM meta WHERE key = 'build_complete'") == [("0",)]


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
