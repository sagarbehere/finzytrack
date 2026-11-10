"""The mirror's `commodities` rows (BL-0120; spec design/specs/mirror/README.md "`is_currency`" and surface.toml
`commodities`; ADR-0025 and its amendment of 2026-10-25; the owner's answer of 2026-10-29,
design/inbox/answered/20261028-iso-currency-list.md). One behaviour per test, each with its near miss (EP-29).
Ledgers are synthetic (INV-23) and built in temp folders (INV-24); the rows are read through the public and the
compatibility views, as users' SQL reads them.

What `is_currency` means, in plain words: 1 is cash (money in some country's currency, counted in Cash & Deposits),
0 is a holding (a stock, a fund, gold: something with a price). First rule that applies: the commodity's declared
`asset-class` (`cash` or `currency` gives 1, anything else 0) unless the code is an operating currency, which is
always 1; else 0 when the ledger holds the code at cost and never without (`10 AMD {80 USD}`: the ticker AMD, not
Armenian drams; the owner's decisions of 2026-11-08); else 1 when the code is a country's money in ISO 4217 (current
or historic); else 0.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.amount import Amount
from beancount.core.data import EMPTY_SET, Posting, Price, Transaction

from ledger_engine.mirror.core import iso4217
from ledger_engine.mirror.core.rows import used_codes

from .support import build_text, one, rows

pytestmark = pytest.mark.inv("INV-05")

ACCOUNTS = """2020-01-01 open Assets:Bank
2020-01-01 open Assets:Broker
2020-01-01 open Expenses:Food
2020-01-01 open Equity:Opening
"""


def build_clean(tmp_path: Path, text: str) -> sqlite3.Connection:
    """The mirror of a ledger that Beancount loads with no error (S0086 r3 verifier F4: a test ledger that does not
    balance tests something else). A test about an error calls `build_text` itself."""
    con = build_text(tmp_path, text)
    assert rows(con, "SELECT message FROM ledger_errors") == []
    return con


def classes(tmp_path: Path, text: str) -> dict[str, int]:
    """code -> is_currency, read through the public view."""
    return dict(rows(build_clean(tmp_path, text), "SELECT code, is_currency FROM r_commodities"))


# ---- which commodities get a row -----------------------------------------------------------------------------------


def test_every_declared_or_used_commodity_has_one_row_sorted_by_code(tmp_path: Path) -> None:
    con = build_clean(
        tmp_path,
        ACCOUNTS + "2020-01-01 commodity DECLARED\n"
        '2021-01-01 * "units"\n  Assets:Bank  1 UNITS\n  Equity:Opening\n'
        '2021-01-02 * "cost"\n  Assets:Broker  2 HELD {10 COSTCUR}\n  Equity:Opening\n'
        '2021-01-03 * "price"\n  Assets:Bank  3 PRICED @ 2 PRICECUR\n  Equity:Opening\n'
        "2021-01-04 price PRICEBASE 1.5 PRICEQUOTE\n"
        "2021-01-05 balance Assets:Bank 0 ASSERTED\n",
    )
    assert [r[0] for r in rows(con, "SELECT code FROM r_commodities")] == sorted(
        [
            "ASSERTED",
            "COSTCUR",
            "DECLARED",
            "HELD",
            "PRICEBASE",
            "PRICECUR",
            "PRICED",
            "PRICEQUOTE",
            "UNITS",
        ]
    )
    # Near miss: a code named only in metadata, an `open` constraint or a `custom` amount is not a commodity row.
    con2 = build_clean(
        tmp_path / "near",
        "2020-01-01 open Assets:Cash ONLYOPEN\n"
        '2020-01-02 custom "budget" Assets:Cash 10 ONLYCUSTOM\n'
        '2020-01-03 note Assets:Cash "x"\n  memo: 1 ONLYMETA\n',
    )
    assert rows(con2, "SELECT code FROM r_commodities") == []


def test_a_currency_seen_only_as_a_cost_has_a_row(tmp_path: Path) -> None:
    # Two legs at cost balance each other, so `COSTCUR` is never anyone's units: only the cost currency gives it a row.
    con = build_clean(
        tmp_path,
        '2020-01-01 open Assets:Long\n2020-01-01 open Assets:Short "NONE"\n'
        '2021-01-02 * "swap"\n  Assets:Long  2 HELD {10 COSTCUR}\n  Assets:Short  -1 OTHER {20 COSTCUR}\n',
    )
    assert rows(con, "SELECT count(*) FROM postings WHERE currency = 'COSTCUR'") == [(0,)]
    assert rows(con, "SELECT code FROM r_commodities") == [("COSTCUR",), ("HELD",), ("OTHER",)]


def test_a_currency_that_is_not_text_names_no_commodity() -> None:
    # A plugin may hand over an amount with no currency; it must neither become a row nor break the sort.
    meta = {"filename": "plugin", "lineno": 0}
    day = date(2021, 1, 1)
    units = Amount(Decimal(1), None)  # pyright: ignore[reportArgumentType]
    txn = Transaction(
        meta, day, "*", None, "x", EMPTY_SET, EMPTY_SET, [Posting("Assets:Bank", units, None, None, None, None)]
    )
    price = Price(meta, day, "VTI", Amount(Decimal(2), None))  # pyright: ignore[reportArgumentType]
    assert used_codes([txn, price]) == {"VTI"}


def test_a_code_used_many_ways_has_one_row(tmp_path: Path) -> None:
    con = build_clean(
        tmp_path,
        ACCOUNTS + "2020-01-01 commodity USD\n"
        '2021-01-01 * "a"\n  Assets:Bank  1 USD\n  Equity:Opening\n'
        '2021-01-02 * "b"\n  Assets:Broker  1 VTI {1 USD}\n  Assets:Bank  -1 USD\n'
        "2021-01-03 price VTI 2 USD\n",
    )
    assert rows(con, "SELECT code FROM commodities") == [("USD",), ("VTI",)]


# ---- the declaration's columns -------------------------------------------------------------------------------------


@pytest.mark.inv("INV-21")  # `name` and `asset-class`, keys v0.2.x wrote, read forever (S0095)
def test_a_declared_commodity_carries_its_directive(tmp_path: Path) -> None:
    con = build_clean(
        tmp_path,
        ACCOUNTS + '2020-03-04 commodity VTI\n  name: "Vanguard Total"\n  asset-class: "etf"\n  issuer: "Example"\n'
        '2021-01-01 * "used, never declared"\n  Assets:Bank  1 ABC\n  Equity:Opening\n',
    )
    got = one(con, "SELECT code, declaration_date, name, asset_class, is_currency FROM r_commodities WHERE code='VTI'")
    assert got == ("VTI", "2020-03-04", "Vanguard Total", "etf", 0)
    meta = json.loads(one(con, "SELECT metadata_json FROM r_commodities WHERE code = 'VTI'")[0])
    assert meta == {
        "filename": str(tmp_path / "ledger" / "main.beancount"),
        "lineno": 5,
        "name": "Vanguard Total",
        "asset-class": "etf",
        "issuer": "Example",
    }
    # Near miss: a used, never declared code has NULL in every declared column.
    assert one(con, "SELECT declaration_date, name, asset_class, metadata_json FROM commodities WHERE code='ABC'") == (
        None,
        None,
        None,
        None,
    )


def test_a_second_declaration_replaces_the_first(tmp_path: Path) -> None:
    # Beancount reports the duplicate as an error; the row keeps the last declaration in entry order, as Beancount's
    # own `getters.get_commodity_directives` and v0.2.2 did.
    con = build_text(
        tmp_path,
        '2020-01-01 commodity GLD\n  name: "first"\n  asset-class: "cash"\n'
        '2021-06-01 commodity GLD\n  name: "second"\n  asset-class: "commodity"\n',
    )
    assert rows(con, "SELECT code, declaration_date, name, asset_class, is_currency FROM commodities") == [
        ("GLD", "2021-06-01", "second", "commodity", 0)
    ]
    assert "Duplicate commodity directives for 'GLD'" in one(con, "SELECT message FROM ledger_errors")[0]


@pytest.mark.inv("INV-21")  # `name` and `asset-class`, keys v0.2.x wrote, read forever (S0095)
def test_metadata_that_is_not_text_is_written_as_its_text(tmp_path: Path) -> None:
    # `name` and `asset-class` are TEXT columns; a number or a flag is stored as its Python text, never as a number.
    con = build_clean(tmp_path, "2020-01-01 commodity ODD\n  name: 42\n  asset-class: TRUE\n")
    assert one(con, "SELECT name, typeof(name), asset_class, typeof(asset_class), is_currency FROM commodities") == (
        "42",
        "text",
        "True",
        "text",
        0,
    )


# ---- is_currency: an operating currency, then the declared asset-class ---------------------------------------------


@pytest.mark.parametrize("value", ["cash", "currency", "Cash", " CURRENCY ", "cAsH"])
def test_a_cash_like_asset_class_makes_any_code_cash(tmp_path: Path, value: str) -> None:
    text = (
        f'2020-01-01 commodity GOLDBAR\n  asset-class: "{value}"\n2020-01-01 commodity XAU\n  asset-class: "{value}"\n'
    )
    assert classes(tmp_path, text) == {"GOLDBAR": 1, "XAU": 1}


@pytest.mark.parametrize("value", ["etf", "stock", "cash-equivalent", "cash fund", "money market", "fx"])
def test_any_other_asset_class_makes_even_an_iso_code_a_holding(tmp_path: Path, value: str) -> None:
    text = f'option "operating_currency" "INR"\n2020-01-01 commodity USD\n  asset-class: "{value}"\n'
    assert classes(tmp_path, text) == {"USD": 0}


@pytest.mark.parametrize("value", ["fiat", "stock", "etf"])
def test_an_operating_currency_is_cash_whatever_its_asset_class(tmp_path: Path, value: str) -> None:
    # Red team F4: `asset-class: "fiat"` on the reporting currency must not move INR out of Cash & Deposits
    # (ADR-0025: a reporting currency is never demoted).
    text = f'option "operating_currency" "INR"\n2020-01-01 commodity INR\n  asset-class: "{value}"\n'
    assert classes(tmp_path, text) == {"INR": 1}


@pytest.mark.parametrize("value", ['""', '"   "'])
def test_an_empty_asset_class_is_not_a_declaration(tmp_path: Path, value: str) -> None:
    # Empty or blank text says nothing, so the next rules decide: USD is ISO cash, VTI a holding.
    text = f"2020-01-01 commodity USD\n  asset-class: {value}\n2020-01-01 commodity VTI\n  asset-class: {value}\n"
    assert classes(tmp_path, text) == {"USD": 1, "VTI": 0}


def test_an_asset_class_that_is_not_text_makes_a_holding(tmp_path: Path) -> None:
    text = "2020-01-01 commodity USD\n  asset-class: 1\n2020-01-01 commodity EUR\n  asset-class: TRUE\n"
    assert classes(tmp_path, text) == {"EUR": 0, "USD": 0}


# ---- is_currency: then ISO 4217 or the operating currencies --------------------------------------------------------


def test_the_readme_example_a_usd_bank_account_in_an_inr_ledger_is_cash(tmp_path: Path) -> None:
    # v0.2.2 marked USD a holding (only operating currencies counted); ADR-0025's amendment makes it cash.
    text = (
        'option "operating_currency" "INR"\n'
        + ACCOUNTS
        + '2021-01-01 * "salary"\n  Assets:Bank  100 USD\n  Equity:Opening\n'
        '2021-01-02 * "buy"\n  Assets:Broker  1 VTI {50 INR}\n  Equity:Opening\n'
    )
    assert classes(tmp_path, text) == {"INR": 1, "USD": 1, "VTI": 0}


def test_a_non_iso_operating_currency_stays_cash(tmp_path: Path) -> None:
    text = (
        'option "operating_currency" "RMB"\n' + ACCOUNTS + '2021-01-01 * "x"\n  Assets:Bank  1 RMB\n  Equity:Opening\n'
    )
    assert classes(tmp_path, text) == {"RMB": 1}
    # Near miss: the same code without the option is a holding.
    near = ACCOUNTS + '2021-01-01 * "x"\n  Assets:Bank  1 RMB\n  Equity:Opening\n'
    assert classes(tmp_path / "near", near) == {"RMB": 0}


def test_an_undeclared_non_iso_code_is_a_holding(tmp_path: Path) -> None:
    # v0.2.2 gave it 1 in a ledger with no `operating_currency`; ADR-0025's amendment makes it 0.
    assert classes(tmp_path, ACCOUNTS + '2021-01-01 * "x"\n  Assets:Broker  1 VTI\n  Equity:Opening\n') == {"VTI": 0}


def test_every_code_that_was_a_countrys_money_is_cash(tmp_path: Path) -> None:
    # The owner's answer: current and historic national money, including money countries share (CFA francs, the
    # East Caribbean dollar, the CFP franc, the Caribbean guilder).
    codes = ["USD", "INR", "EUR", "JPY", "DEM", "FRF", "ITL", "ESP", "BGN", "ZWL", "XOF", "XAF", "XCD", "XPF", "XCG"]
    text = "".join(f"2020-01-01 commodity {c}\n" for c in codes)
    assert classes(tmp_path, text) == dict.fromkeys(codes, 1)


def test_metals_funds_and_accounting_units_are_holdings(tmp_path: Path) -> None:
    # The owner's answer: gold is an investment unless the ledger says `asset-class: "cash"` (tested above).
    codes = ["XAU", "XAG", "XPT", "XPD", "XDR", "XUA", "XSU", "XEU", "CLF", "COU", "MXV", "BOV", "USN", "XTS", "XXX"]
    text = "".join(f"2020-01-01 commodity {c}\n" for c in codes)
    assert classes(tmp_path, text) == dict.fromkeys(codes, 0)


def test_the_iso_test_is_exact(tmp_path: Path) -> None:
    # A code that only looks like a currency is not one: no case folding, no prefix or suffix match.
    text = "2020-01-01 commodity USDT\n2020-01-01 commodity USD.X\n2020-01-01 commodity CNH\n"
    assert classes(tmp_path, text) == {"CNH": 0, "USD.X": 0, "USDT": 0}


def test_every_pinned_code_reaches_the_row_as_the_list_says(tmp_path: Path) -> None:
    # The whole pinned list through one build: no code is dropped or misread between the list and the rows.
    every = sorted(iso4217.CASH | set(iso4217.NOT_CASH))
    text = "".join(f"2020-01-01 commodity {c}\n" for c in every)
    assert classes(tmp_path, text) == {c: 1 if c in iso4217.CASH else 0 for c in every}


def test_the_compatibility_view_has_the_v022_columns(tmp_path: Path) -> None:
    con = build_clean(tmp_path, '2020-01-01 commodity EUR\n  name: "Euro"\n')
    assert rows(con, "SELECT code, name, is_currency FROM commodities") == [("EUR", "Euro", 1)]


# ---- is_currency: a code the ledger only ever holds at cost is a holding (owner, 2026-11-08) ------------------------

CASH_VIEW = (
    "SELECT ab.currency FROM account_balances ab JOIN commodities c ON c.code = ab.currency AND c.is_currency = 1"
    " WHERE ab.account LIKE 'Assets:%' ORDER BY 1"
)
HOLDINGS_VIEW = (
    "SELECT DISTINCT p.currency FROM postings p JOIN commodities c ON c.code = p.currency AND c.is_currency = 0"
    " WHERE p.cost_currency IS NOT NULL ORDER BY 1"
)


def test_us_tickers_spelt_like_iso_codes_are_holdings(tmp_path: Path) -> None:
    # Red team F1: AMD (the chip maker), BND (a bond ETF), COP, YUM are ISO 4217 codes too (Armenian dram, Brunei
    # dollar, Colombian peso, a Yugoslav dinar). Bought at cost, they are holdings, never cash.
    con = build_clean(
        tmp_path,
        'option "operating_currency" "USD"\n2020-01-01 open Assets:Bank USD\n2020-01-01 open Assets:Brokerage\n'
        '2020-01-01 open Equity:Opening\n2021-01-02 * "open"\n  Assets:Bank  10000 USD\n  Equity:Opening\n'
        '2021-01-03 * "buy"\n  Assets:Brokerage  10 AMD {80 USD}\n  Assets:Brokerage  20 BND {85 USD}\n'
        "  Assets:Brokerage  5 COP {50 USD}\n  Assets:Brokerage  7 YUM {100 USD}\n  Assets:Brokerage  3 VTI {200 USD}\n"
        "  Assets:Bank  -4050 USD\n2024-01-01 price AMD 150 USD\n",
    )
    assert dict(rows(con, "SELECT code, is_currency FROM r_commodities")) == {
        "AMD": 0,
        "BND": 0,
        "COP": 0,
        "USD": 1,
        "VTI": 0,
        "YUM": 0,
    }
    assert rows(con, CASH_VIEW) == [("USD",)]
    assert rows(con, HOLDINGS_VIEW) == [("AMD",), ("BND",), ("COP",), ("VTI",), ("YUM",)]


def test_indian_tickers_and_gold_held_at_cost_are_holdings(tmp_path: Path) -> None:
    # Red team F2: BEL (Bharat Electronics) is also the Belgian financial franc's code.
    con = build_clean(
        tmp_path,
        'option "operating_currency" "INR"\n2020-01-01 open Assets:Bank INR\n2020-01-01 open Assets:Demat\n'
        '2020-01-01 open Equity:Opening\n2021-01-02 * "o"\n  Assets:Bank  100000 INR\n  Equity:Opening\n'
        '2021-01-03 * "buy"\n  Assets:Demat  100 BEL {250 INR}\n  Assets:Demat  10 XAU {5000 INR}\n'
        "  Assets:Bank  -75000 INR\n",
    )
    assert dict(rows(con, "SELECT code, is_currency FROM r_commodities")) == {"BEL": 0, "INR": 1, "XAU": 0}
    assert rows(con, CASH_VIEW) == [("INR",)]
    assert rows(con, HOLDINGS_VIEW) == [("BEL",), ("XAU",)]


def test_a_ticker_recorded_without_cost_stays_cash(tmp_path: Path) -> None:
    """@regression-guard (it held on a195dbf3): the near miss of the held-at-cost rule; it kills `at-cost-any-price`."""
    # The stated limit of the owner's rule: only a cost says "holding"; a price with `@` says nothing, so BEL bought
    # this way is read as the ISO code it spells. `asset-class` fixes it.
    text = (
        'option "operating_currency" "INR"\n2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Demat\n'
        '2021-01-03 * "buy"\n  Assets:Demat  10 BEL @ 250 INR\n  Assets:Bank  -2500 INR\n'
    )
    assert classes(tmp_path, text) == {"BEL": 1, "INR": 1}
    declared = '2020-01-01 commodity BEL\n  asset-class: "stock"\n' + text
    assert classes(tmp_path / "declared", declared) == {"BEL": 0, "INR": 1}


def test_a_foreign_currency_only_held_at_cost_is_a_holding_unless_declared(tmp_path: Path) -> None:
    # USD that the ledger only ever holds as a lot at cost is a holding unless declared cash or an operating
    # currency; with any plain USD balance it is cash (test_a_forex_lot_beside_a_plain_balance_leaves_the_code_cash).
    held = (
        'option "operating_currency" "INR"\n2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Forex\n'
        '2021-01-03 * "buy usd"\n  Assets:Forex  100 USD {83 INR}\n  Assets:Bank  -8300 INR\n'
    )
    assert classes(tmp_path, held) == {"INR": 1, "USD": 0}
    declared = '2020-01-01 commodity USD\n  asset-class: "cash"\n' + held
    assert classes(tmp_path / "declared", declared) == {"INR": 1, "USD": 1}
    operating = held.replace('"INR"\n', '"INR"\noption "operating_currency" "USD"\n', 1)
    assert classes(tmp_path / "operating", operating) == {"INR": 1, "USD": 1}


def test_an_empty_cost_and_a_future_lot_count_as_held_at_cost(tmp_path: Path) -> None:
    # `{}` is a cost too (Beancount fills it in); the rule reads the whole ledger, future-dated entries included,
    # since a code is a cash or a holding whatever the date.
    text = (
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n"
        '2021-01-03 * "empty cost"\n  Assets:Broker  2 AMD {}\n  Assets:Bank  -100 USD\n'
        '2030-01-03 * "future"\n  Assets:Broker  1 BND {70 USD}\n  Assets:Bank  -70 USD\n'
    )
    assert classes(tmp_path, text) == {"AMD": 0, "BND": 0, "USD": 1}


def test_the_cost_currency_itself_is_not_held_at_cost(tmp_path: Path) -> None:
    """@regression-guard (it held on a195dbf3): the near miss of the held-at-cost rule; it kills `at-cost-cost-cur`."""
    # Near miss: only the units currency of a posting with a cost is held; the currency the cost is paid in is not.
    text = "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n" + (
        '2021-01-03 * "buy"\n  Assets:Broker  1 VTI {80 EUR}\n  Assets:Bank  -80 EUR\n'
    )
    assert classes(tmp_path, text) == {"EUR": 1, "VTI": 0}


RT2_NRI = (
    'option "operating_currency" "INR"\n2015-01-01 open Assets:IN:Bank INR\n2015-01-01 open Assets:US:Bank USD\n'
    "2015-01-01 open Assets:Wise USD\n2015-01-01 open Equity:Opening\n2015-01-01 open Income:Salary\n"
    '2016-01-01 * "o"\n  Assets:IN:Bank  500000 INR\n  Equity:Opening\n'
    '2016-01-01 * "salary"\n  Assets:US:Bank  20000 USD\n  Income:Salary\n'
    '2018-03-01 * "forex buy, tracked at cost"\n  Assets:Wise  1000 USD {65 INR}\n  Assets:IN:Bank  -65000 INR\n'
    "2026-01-01 price USD 88 INR\n"
)
CASH_BALANCES = (
    "SELECT ab.account, ab.currency, ab.balance FROM account_balances ab JOIN commodities c ON c.code = ab.currency"
    " AND c.is_currency = 1 WHERE ab.account LIKE 'Assets:%' ORDER BY 1, 2"
)


@pytest.mark.parametrize("operating", [True, False])
def test_a_forex_lot_beside_a_plain_balance_leaves_the_code_cash(tmp_path: Path, operating: bool) -> None:
    # Red team round 2 F1 (owner, 2026-11-08, "Only-at-cost = holding"): one lot `1000 USD {65 INR}` must not take
    # the plain USD salary account out of every view; the lot's USD is money too and counts as USD cash.
    text = RT2_NRI if operating else RT2_NRI.replace('option "operating_currency" "INR"\n', "")
    con = build_clean(tmp_path, text)
    assert dict(rows(con, "SELECT code, is_currency FROM r_commodities")) == {"INR": 1, "USD": 1}
    assert rows(con, CASH_BALANCES) == [
        ("Assets:IN:Bank", "INR", "435000"),
        ("Assets:US:Bank", "USD", "20000"),
        ("Assets:Wise", "USD", "1000"),
    ]


def test_a_forex_lot_closed_long_ago_leaves_the_code_cash(tmp_path: Path) -> None:
    text = (
        'option "operating_currency" "INR"\n2015-01-01 open Assets:IN:Bank INR\n2015-01-01 open Assets:US:Bank USD\n'
        "2015-01-01 open Assets:Old USD\n2015-01-01 open Equity:Opening\n2015-01-01 open Income:Gains\n"
        '2016-01-01 * "o"\n  Assets:IN:Bank  500000 INR\n  Assets:US:Bank  20000 USD\n  Equity:Opening\n'
        '2017-03-01 * "one forex trade at cost"\n  Assets:Old  100 USD {65 INR}\n  Assets:IN:Bank  -6500 INR\n'
        '2017-06-01 * "sold"\n  Assets:Old  -100 USD {65 INR} @ 66 INR\n  Assets:IN:Bank  6600 INR\n'
        "  Income:Gains  -100 INR\n2017-07-01 close Assets:Old\n"
    )
    assert classes(tmp_path, text) == {"INR": 1, "USD": 1}


def test_adjusted_cost_base_tracking_leaves_the_bank_currency_cash(tmp_path: Path) -> None:
    # A Canadian tracking the cost base of USD bought through a broker, with a plain USD bank account beside it.
    text = (
        'option "operating_currency" "CAD"\n2020-01-01 open Assets:Bank:CAD CAD\n2020-01-01 open Assets:Bank:USD USD\n'
        "2020-01-01 open Assets:Broker:USD USD\n2020-01-01 open Equity:Opening\n"
        '2020-01-02 * "o"\n  Assets:Bank:CAD  50000 CAD\n  Assets:Bank:USD  3000 USD\n  Equity:Opening\n'
        '2021-01-02 * "norbert gambit"\n  Assets:Broker:USD  10000 USD {1.30 CAD}\n  Assets:Bank:CAD  -13000 CAD\n'
    )
    assert classes(tmp_path, text) == {"CAD": 1, "USD": 1}


def test_a_none_booking_account_with_a_lot_holds_its_currency_at_cost(tmp_path: Path) -> None:
    # Round 3 (owner, 2026-11-08, "stricter reading"): every USD posting here is in `Assets:US:Bank`, which holds a
    # USD lot at cost (the short sale under NONE booking), or in `Equity:Opening`, which holds no balance; so USD is
    # held only at cost: a holding. Round 2 expected cash (its Equity leg counted); see the journal's Golden changes.
    text = (
        'option "operating_currency" "INR"\n2015-01-01 open Assets:IN:Bank INR\n'
        '2015-01-01 open Assets:US:Bank USD "NONE"\n2015-01-01 open Equity:Opening\n'
        '2016-01-01 * "o"\n  Assets:IN:Bank  500000 INR\n  Assets:US:Bank  20000 USD\n  Equity:Opening\n'
        '2017-01-01 * "x"\n  Assets:US:Bank  -100 USD {70 INR}\n  Assets:IN:Bank  7000 INR\n'
    )
    assert classes(tmp_path, text) == {"INR": 1, "USD": 0}


def test_a_sale_from_a_lot_alone_marks_held_at_cost(tmp_path: Path) -> None:
    """@regression-guard (it held on 37d56ae0; it kills `purchases-only`, S0086 r2 verifier F2)."""
    # S0086 r2 verifier F2: a sale from a lot is held at cost as much as a purchase (here the only posting of AMD).
    text = (
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n2020-01-01 open Equity:Opening\n"
        '2021-01-03 * "sell"\n  Assets:Broker  -2 AMD {80 USD}\n  Assets:Bank  160 USD\n'
    )
    assert classes(tmp_path, text) == {"AMD": 0, "USD": 1}


def test_a_ticker_that_also_sits_somewhere_without_cost_is_cash(tmp_path: Path) -> None:
    # The owner's stated edge of 2026-11-08 (red team round 2 F3): an ISO-spelt ticker held without cost anywhere
    # (padded, opened or moved plain, or priced with `@` only) counts as cash; a non-ISO ticker stays a holding.
    padded = (
        'option "operating_currency" "USD"\n2020-01-01 open Assets:Bank USD\n2020-01-01 open Assets:Broker\n'
        "2020-01-01 open Equity:Opening\n2020-01-01 pad Assets:Broker Equity:Opening\n"
        "2020-01-02 balance Assets:Broker 10 AMD\n2020-01-02 balance Assets:Broker 20 VTI\n"
    )
    assert classes(tmp_path, padded) == {"AMD": 1, "VTI": 0}
    # A padding transaction carries no cost, so it counts as holding the code without cost, beside a lot at cost.
    bought_and_padded = padded + (
        "2020-01-01 open Assets:Bank2\n2020-01-01 open Assets:Broker2\n"
        '2021-01-03 * "buy"\n  Assets:Broker2  10 BND {80 USD}\n  Assets:Bank2  -800 USD\n'
        "2020-01-01 open Assets:Old\n2020-01-01 pad Assets:Old Equity:Opening\n2020-01-02 balance Assets:Old 5 BND\n"
    )
    assert classes(tmp_path / "both", bought_and_padded) == {"AMD": 1, "BND": 1, "USD": 1, "VTI": 0}
    moved = (  # the owner's "transferred plain": into an asset account that never holds AMD at cost
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Broker\n2020-01-01 open Assets:Other\n"
        '2021-01-03 * "buy"\n  Assets:Broker  10 AMD {80 USD}\n  Assets:Bank  -800 USD\n'
        '2021-02-03 * "moved out without its cost"\n  Assets:Broker  -10 AMD {80 USD}\n'
        "  Assets:Other  10 AMD @ 80 USD\n"
    )
    assert classes(tmp_path / "moved", moved) == {"AMD": 1, "USD": 1}


def test_a_fund_tagged_cash_is_cash_even_at_cost(tmp_path: Path) -> None:
    """@regression-guard (it held on 37d56ae0; it pins the owner's "tag wins" of 2026-11-08)."""
    # Owner, 2026-11-08 ("Operating first; tag wins"): the declared tag wins over held at cost. The docs and BL-0207
    # warn that such a fund then shows as units in the cash views and leaves the holdings (red team round 2 F2).
    text = (
        'option "operating_currency" "INR"\n2020-01-01 commodity LIQUIDBEES\n  asset-class: "cash"\n'
        "2020-01-01 open Assets:Bank INR\n2020-01-01 open Assets:Demat\n"
        '2021-01-02 * "park"\n  Assets:Demat  50 LIQUIDBEES {1000 INR}\n  Assets:Bank  -50000 INR\n'
    )
    assert classes(tmp_path, text) == {"INR": 1, "LIQUIDBEES": 1}


# ---- round 3: a posting without cost counts only in an account that never holds the code at cost --------------------

RT3 = (
    'option "operating_currency" "INR"\n2020-01-01 open Assets:IN:Bank INR\n2020-01-01 open Assets:US:Broker\n'
    "2020-01-01 open Assets:US:Cash USD\n2020-01-01 open Assets:US:Other\n2020-01-01 open Equity:Opening\n"
    "2020-01-01 open Income:Gains\n2020-01-01 open Income:Vest\n2020-01-01 open Expenses:Fees\n"
    "2020-01-01 open Liabilities:Card\n"
    '2020-01-02 * "o"\n  Assets:US:Cash  5000 USD\n  Equity:Opening\n'
    '2020-02-01 * "buy"\n  Assets:US:Broker  10 AMD {80 USD}\n  Assets:US:Cash  -800 USD\n'
    "2026-01-01 price AMD 150 USD\n"
)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            '2021-01-01 * "sell 2, forgot {}"\n  Assets:US:Broker  -2 AMD @ 100 USD\n  Assets:US:Cash  200 USD\n',
            id="sale_plain_price",
        ),
        pytest.param(
            '2021-06-01 * "2:1 split as extra shares at price 0"\n  Assets:US:Broker  10 AMD @ 0 USD\n',
            id="split_plain",
        ),
        pytest.param(
            '2021-03-15 * "RSU vest, income in shares"\n  Assets:US:Broker  10 AMD {80 USD}\n'
            "  Income:Vest  -10 AMD @ 80 USD\n",
            id="rsu_income_in_shares",
        ),
        pytest.param(
            '2021-01-01 * "fee in shares"\n  Assets:US:Broker  -1 AMD {80 USD}\n  Expenses:Fees  1 AMD @ 80 USD\n',
            id="fee_in_shares",
        ),
    ],
)
def test_plain_postings_in_the_lot_account_or_outside_the_balance_sheet_keep_a_ticker_a_holding(
    tmp_path: Path, extra: str
) -> None:
    # Red team round 3 (t1, t2, t3) and verifier r3 F3. A plain posting of AMD in the account that holds its lots
    # (a sale or split written without `{}`, a pad into it) says nothing; nor does one in an Income, Expenses or
    # Equity account (an RSU's income leg, a fee paid in shares, a pad's source), which holds no balance.
    assert classes(tmp_path, RT3 + extra) == {"AMD": 0, "USD": 1}


def pad_error_classes(tmp_path: Path, text: str) -> dict[str, int]:
    """A pad into an account holding lots at cost: Beancount reports an error and still inserts a padding
    transaction without cost, its legs in the lot account and in `Equity` (checked here)."""
    con = build_text(tmp_path, text)
    assert [m[:45] for (m,) in rows(con, "SELECT message FROM ledger_errors")] == [
        "Attempt to pad an entry with cost for balance"
    ]
    assert rows(con, "SELECT count(*) FROM postings WHERE transaction_flag = 'P' AND cost_currency IS NULL") == [(2,)]
    return dict(rows(con, "SELECT code, is_currency FROM r_commodities"))


def test_a_pad_into_a_lot_account_keeps_a_ticker_a_holding(tmp_path: Path) -> None:
    # Red team round 3 t3 `broker_pad_fraction`: the padding legs are in the lot's own account and in Equity.
    text = RT3 + "2021-01-15 pad Assets:US:Broker Equity:Opening\n2021-02-01 balance Assets:US:Broker 10.04 AMD\n"
    assert pad_error_classes(tmp_path, text) == {"AMD": 0, "USD": 1}


def test_a_bond_fund_with_lots_and_one_pad_stays_a_holding(tmp_path: Path) -> None:
    # Red team round 3 t3 `bnd_bogle_lots_plus_one_pad`: BND (also the Brunei dollar's code) with lots and a pad
    # into the same account is a holding.
    text = (
        'option "operating_currency" "USD"\n2020-01-01 open Assets:Bank USD\n2020-01-01 open Assets:Vanguard\n'
        '2020-01-01 open Equity:Opening\n2020-01-02 * "o"\n  Assets:Bank  50000 USD\n  Equity:Opening\n'
        '2020-02-01 * "buy"\n  Assets:Vanguard  200 BND {85 USD}\n  Assets:Bank  -17000 USD\n'
        "2021-01-01 pad Assets:Vanguard Equity:Opening\n2021-02-01 balance Assets:Vanguard 200.5 BND\n"
    )
    assert pad_error_classes(tmp_path, text) == {"BND": 0, "USD": 1}


def test_a_price_only_posting_beside_a_lot_counts_only_outside_the_lot_account(tmp_path: Path) -> None:
    # Verifier r3 F2: `5 AMD @ 80 USD` (a price, not a cost) in an asset account that never holds AMD at cost makes
    # the code cash, read as the Armenian dram; the same posting in the lot's own account does not.
    other = (
        RT3 + '2021-02-03 * "bought with only a price"\n  Assets:US:Other  5 AMD @ 80 USD\n  Assets:US:Cash  -400 USD\n'
    )
    assert classes(tmp_path, other) == {"AMD": 1, "USD": 1}
    same = (
        RT3
        + '2021-02-03 * "bought with only a price"\n  Assets:US:Broker  5 AMD @ 80 USD\n  Assets:US:Cash  -400 USD\n'
    )
    assert classes(tmp_path / "same", same) == {"AMD": 0, "USD": 1}


def test_a_plain_liability_in_the_code_counts_as_held_without_cost(tmp_path: Path) -> None:
    # Verifier r3 F3: a liability holds a balance too. USD held only as a lot at cost, plus a USD credit card: the
    # card is money owed in USD, so USD is cash.
    text = (
        'option "operating_currency" "INR"\n2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Wise\n'
        "2020-01-01 open Liabilities:Card\n2020-01-01 open Expenses:Travel\n"
        '2021-01-03 * "fx lot"\n  Assets:Wise  100 USD {83 INR}\n  Assets:Bank  -8300 INR\n'
        '2021-02-03 * "hotel"\n  Expenses:Travel  50 USD\n  Liabilities:Card  -50 USD\n'
    )
    assert classes(tmp_path, text) == {"INR": 1, "USD": 1}
    # Near miss: the same plain USD only in an Expenses account (a cost paid out of the lot) leaves USD a holding.
    spent = (
        'option "operating_currency" "INR"\n2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Wise\n'
        "2020-01-01 open Expenses:Travel\n"
        '2021-01-03 * "fx lot"\n  Assets:Wise  100 USD {83 INR}\n  Assets:Bank  -8300 INR\n'
        '2021-02-03 * "hotel"\n  Expenses:Travel  50 USD @ 83 INR\n  Assets:Wise  -50 USD {83 INR}\n'
    )
    assert classes(tmp_path / "spent", spent) == {"INR": 1, "USD": 0}


def test_custom_root_names_decide_the_account_kind(tmp_path: Path) -> None:
    """@regression-guard (it held on c01066bf, which read no account kind; it kills `literal-roots`)."""
    # The kind comes from the ledger's `name_*` options (ADR-0024), never from the literal text `Assets`.
    text = (
        'option "name_assets" "Vermoegen"\n2020-01-01 open Vermoegen:Bank\n2020-01-01 open Vermoegen:Depot\n'
        "2020-01-01 open Vermoegen:Wise\n"
        '2021-01-03 * "fx lot"\n  Vermoegen:Wise  100 USD {0.9 EUR}\n  Vermoegen:Bank  -90 EUR\n'
        '2021-01-04 * "plain"\n  Vermoegen:Depot  10 USD @ 0.9 EUR\n  Vermoegen:Bank  -9 EUR\n'
    )
    assert classes(tmp_path, text) == {"EUR": 1, "USD": 1}
