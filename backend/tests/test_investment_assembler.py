"""investment_assembler — typed events → balanced draft transactions.

Specification (dev-docs/investment-view.md §6.3): each event assembles into the
exact Beancount geometry documented there — Buy (`units SECURITY {unit_price CCY}`
+ cash), DRIP (holding + income, nets to zero cash), Cash Dividend (cash + income),
Add-Holding (holding at aggregate cost + opening) — every draft balances, cost is a
per-unit `{}`, accounts come from the event (never invented), and the security is
classified by metadata (currency vs holding). Holdings reads the mirror `lots` +
latest price. Money is exact Decimal (dev-docs/money-types.md).
"""

from decimal import Decimal
from datetime import date

from beancount import loader

from app.core.ledger_loader import load_ledger_checked
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.services.investment_assembler import InvestmentAssembler
from app.schemas.investment_schemas import (
    BuyEvent,
    DripEvent,
    CashDividendEvent,
    SellEvent,
    LotSelection,
    CdMaturityEvent,
    InterestEvent,
    AddHoldingEvent,
)


def _assembler(tmp_path, ledger_text) -> InvestmentAssembler:
    ledger = tmp_path / "main.beancount"
    ledger.write_text(ledger_text)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == [], errors
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return InvestmentAssembler(reader)


# A ledger with a declared holding (VOO), a money-market fund, a currency (USD),
# a cash account, a dividend income account, and an opening-balances account.
_BASE = """\
option "operating_currency" "USD"
2015-01-01 commodity VOO
  name: "Vanguard S&P 500 ETF"
  asset-class: "etf"
2015-01-01 commodity VMFXX
  asset-class: "money-market"
2020-01-01 open Assets:Broker:VOO
2020-01-01 open Assets:Broker:MMF
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Dividends:VOO USD
  income-type: "dividend"
2020-01-01 open Income:CapitalGains USD
  income-type: "capital-gains"
2020-01-01 open Expenses:Fees USD
2020-01-01 open Equity:Opening-Balances
"""

# A ledger holding three VOO lots at different dates and costs — for Sale strategies.
# By date: 2021-01-10 @100, 2021-06-20 @120, 2022-03-05 @110 (5 units each, 15 total).
_MULTI = _BASE + """\
2021-01-10 * "buy lot1"
  Assets:Broker:VOO 5 VOO {100.00 USD}
  Assets:Cash -500.00 USD
2021-06-20 * "buy lot2"
  Assets:Broker:VOO 5 VOO {120.00 USD}
  Assets:Cash -600.00 USD
2022-03-05 * "buy lot3"
  Assets:Broker:VOO 5 VOO {110.00 USD}
  Assets:Cash -550.00 USD
"""

# A ledger that already holds 10 VOO (a real lot) plus a price — for holdings tests.
_HELD = _BASE + """\
2021-03-14 * "Buy VOO"
  Assets:Broker:VOO 10 VOO {417.45 USD}
  Assets:Cash -4174.50 USD
2024-01-02 price VOO 560.00 USD
"""


# ── Buy ──────────────────────────────────────────────────────────────────────

def test_buy_geometry(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("10"), unit_price=Decimal("417.45"),
        cash_account="Assets:Cash",
    )])

    assert data.errors == []
    assert data.warnings == []
    assert len(data.drafts) == 1
    d = data.drafts[0]
    assert d.narration == "Buy VOO"
    assert d.source_account == "Assets:Broker:VOO"
    assert d.flag == "*"
    assert len(d.postings) == 2

    holding, cash = d.postings
    assert holding.account == "Assets:Broker:VOO"
    assert holding.amount == Decimal("10")
    assert holding.currency == "VOO"
    assert holding.cost_amount == Decimal("417.45")
    assert holding.cost_currency == "USD"
    assert holding.cost_date is None  # per-unit {}; Beancount stamps the txn date
    assert cash.account == "Assets:Cash"
    assert cash.amount == Decimal("-4174.50")
    assert cash.currency == "USD"


def test_buy_with_fee(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("10"), unit_price=Decimal("417.45"),
        cash_account="Assets:Cash",
        fee=Decimal("4.95"), fee_account="Expenses:Fees",
    )])

    assert data.errors == []
    d = data.drafts[0]
    assert len(d.postings) == 3
    accounts = {p.account: p for p in d.postings}
    assert accounts["Expenses:Fees"].amount == Decimal("4.95")
    assert accounts["Assets:Cash"].amount == Decimal("-4179.45")  # 4174.50 + 4.95


def test_buy_fee_without_account_errors(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("10"), unit_price=Decimal("417.45"),
        cash_account="Assets:Cash", fee=Decimal("4.95"),
    )])
    assert data.drafts == []
    assert len(data.errors) == 1
    assert data.errors[0].code == "MISSING_FEE_ACCOUNT"


def test_buy_new_commodity_warns_but_builds(tmp_path):
    """First buy of a not-yet-declared security is allowed, with a NEW_COMMODITY nudge."""
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:NEW", security="AVUV",
        units=Decimal("5"), unit_price=Decimal("100.00"),
        cash_account="Assets:Cash",
    )])
    assert len(data.drafts) == 1
    assert len(data.warnings) == 1
    assert data.warnings[0].code == "NEW_COMMODITY"


def test_buy_a_currency_is_rejected(tmp_path):
    """You don't 'buy' a currency as a holding at cost — hard error, no draft.
    (Uses the _HELD ledger: a currency only enters the commodities table once used
    in a posting, so USD must appear in a real transaction to be classified.)"""
    asm = _assembler(tmp_path, _HELD)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="USD",
        units=Decimal("10"), unit_price=Decimal("1.00"),
        cash_account="Assets:Cash",
    )])
    assert data.drafts == []
    assert len(data.errors) == 1
    assert data.errors[0].code == "CURRENCY_NOT_HOLDING"


def test_buy_zero_units_is_rejected(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("0"), unit_price=Decimal("417.45"),
        cash_account="Assets:Cash",
    )])
    assert data.drafts == []
    assert data.errors[0].code == "INVALID_AMOUNT"


# ── DRIP ─────────────────────────────────────────────────────────────────────

def test_drip_geometry(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([DripEvent(
        type="drip", date=date(2024, 6, 26), currency="USD",
        holding_account="Assets:Broker:MMF", security="VMFXX",
        units=Decimal("0.82"), unit_price=Decimal("1.00"),
        income_account="Income:Dividends:VOO",
    )])
    assert data.errors == []
    d = data.drafts[0]
    assert d.narration == "VMFXX dividend reinvested"
    assert d.source_account == "Assets:Broker:MMF"
    assert len(d.postings) == 2
    holding, income = d.postings
    assert holding.amount == Decimal("0.82")
    assert holding.currency == "VMFXX"
    assert holding.cost_amount == Decimal("1.00")
    assert holding.cost_currency == "USD"
    assert income.account == "Income:Dividends:VOO"
    assert income.amount == Decimal("-0.82")  # nets to zero cash


# ── Cash Dividend ─────────────────────────────────────────────────────────────

def test_cash_dividend_geometry(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([CashDividendEvent(
        type="cash_dividend", date=date(2024, 6, 3), currency="USD",
        cash_account="Assets:Cash", security="VOO",
        amount=Decimal("379.09"), income_account="Income:Dividends:VOO",
    )])
    assert data.errors == []
    assert data.warnings == []
    d = data.drafts[0]
    assert d.narration == "VOO dividend"
    assert d.source_account == "Assets:Cash"
    cash, income = d.postings
    assert cash.account == "Assets:Cash"
    assert cash.amount == Decimal("379.09")
    assert income.amount == Decimal("-379.09")


def test_cash_dividend_unknown_security_warns_but_builds(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([CashDividendEvent(
        type="cash_dividend", date=date(2024, 6, 3), currency="USD",
        cash_account="Assets:Cash", security="ZZZZ",
        amount=Decimal("10.00"), income_account="Income:Dividends:VOO",
    )])
    assert len(data.drafts) == 1
    assert len(data.warnings) == 1
    assert data.warnings[0].code == "UNKNOWN_COMMODITY"


# ── Add-Holding-at-Cost ───────────────────────────────────────────────────────

def test_add_holding_geometry(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([AddHoldingEvent(
        type="add_holding", date=date(2024, 1, 1), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("25"), avg_cost=Decimal("400.00"),
        opening_account="Equity:Opening-Balances",
    )])
    assert data.errors == []
    d = data.drafts[0]
    assert d.narration == "Opening holding VOO"
    assert d.source_account == "Assets:Broker:VOO"
    holding, opening = d.postings
    assert holding.amount == Decimal("25")
    assert holding.cost_amount == Decimal("400.00")
    assert holding.cost_currency == "USD"
    assert opening.account == "Equity:Opening-Balances"
    assert opening.amount == Decimal("-10000.00")  # 25 * 400.00


# ── Partial success ───────────────────────────────────────────────────────────

def test_partial_success_mixes_drafts_and_errors(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    good = BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("1"), unit_price=Decimal("400.00"), cash_account="Assets:Cash",
    )
    bad = BuyEvent(
        type="buy", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        units=Decimal("0"), unit_price=Decimal("400.00"), cash_account="Assets:Cash",
    )
    data = asm.assemble([good, bad])
    assert len(data.drafts) == 1
    assert len(data.errors) == 1
    assert data.errors[0].event_index == 1


# ── Holdings ──────────────────────────────────────────────────────────────────

def test_holdings_reads_lots_and_latest_price(tmp_path):
    asm = _assembler(tmp_path, _HELD)
    data = asm.holdings()
    voo = next(h for h in data.holdings if h.commodity == "VOO")
    assert voo.is_currency is False
    assert voo.asset_class == "etf"
    assert voo.total_units == Decimal("10")
    assert voo.last_price == Decimal("560.00")
    assert voo.last_price_currency == "USD"
    assert len(voo.lots) == 1
    lot = voo.lots[0]
    assert lot.account == "Assets:Broker:VOO"
    assert lot.units == Decimal("10")
    assert lot.cost_number == Decimal("417.45")
    assert lot.acquisition_date == date(2021, 3, 14)


def test_holdings_filtered_by_commodity(tmp_path):
    asm = _assembler(tmp_path, _HELD)
    data = asm.holdings(commodity="VOO")
    assert [h.commodity for h in data.holdings] == ["VOO"]
    # A commodity with no lots returns nothing.
    empty = asm.holdings(commodity="VMFXX")
    assert empty.holdings == []


# ── Sale (lot resolution) ─────────────────────────────────────────────────────

def _sell(**kwargs):
    base = dict(
        type="sell", date=date(2024, 6, 3), currency="USD",
        holding_account="Assets:Broker:VOO", security="VOO",
        sale_price=Decimal("200.00"), cash_account="Assets:Cash",
        gains_account="Income:CapitalGains",
    )
    base.update(kwargs)
    return SellEvent(**base)


def _reducing_legs(draft):
    return [p for p in draft.postings if p.currency == "VOO"]


def test_sell_fifo(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("7"), strategy="fifo")])
    assert data.errors == []
    d = data.drafts[0]
    assert d.narration == "Sell VOO"
    assert d.source_account == "Assets:Broker:VOO"

    legs = _reducing_legs(d)
    # FIFO: 5 @100 (2021-01-10) then 2 @120 (2021-06-20)
    assert [(l.amount, l.cost_amount, l.cost_date) for l in legs] == [
        (Decimal("-5"), Decimal("100.00"), date(2021, 1, 10)),
        (Decimal("-2"), Decimal("120.00"), date(2021, 6, 20)),
    ]
    for l in legs:
        assert l.price_amount == Decimal("200.00")
        assert l.price_type == "@"
        assert l.cost_currency == "USD"

    by_acct = {p.account: p for p in d.postings if p.currency == "USD"}
    # proceeds 1400; cost basis 5*100 + 2*120 = 740; gain 660
    assert by_acct["Assets:Cash"].amount == Decimal("1400.00")
    assert by_acct["Income:CapitalGains"].amount == Decimal("-660.00")


def test_sell_lifo(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("7"), strategy="lifo")])
    assert data.errors == []
    legs = _reducing_legs(data.drafts[0])
    # LIFO: 5 @110 (2022-03-05) then 2 @120 (2021-06-20); cost basis 550+240=790; gain 610
    assert [(l.amount, l.cost_amount) for l in legs] == [
        (Decimal("-5"), Decimal("110.00")),
        (Decimal("-2"), Decimal("120.00")),
    ]
    gains = next(p for p in data.drafts[0].postings if p.account == "Income:CapitalGains")
    assert gains.amount == Decimal("-610.00")


def test_sell_hifo(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("7"), strategy="hifo")])
    assert data.errors == []
    legs = _reducing_legs(data.drafts[0])
    # HIFO: 5 @120 then 2 @110; cost basis 600+220=820; gain 580
    assert [(l.amount, l.cost_amount) for l in legs] == [
        (Decimal("-5"), Decimal("120.00")),
        (Decimal("-2"), Decimal("110.00")),
    ]
    gains = next(p for p in data.drafts[0].postings if p.account == "Income:CapitalGains")
    assert gains.amount == Decimal("-580.00")


def test_sell_specific_lots(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(
        units=Decimal("7"),
        lots=[
            LotSelection(acquisition_date=date(2021, 1, 10), cost_number=Decimal("100.00"), cost_currency="USD", units=Decimal("5")),
            LotSelection(acquisition_date=date(2022, 3, 5), cost_number=Decimal("110.00"), cost_currency="USD", units=Decimal("2")),
        ],
    )])
    assert data.errors == []
    legs = _reducing_legs(data.drafts[0])
    assert [(l.amount, l.cost_amount, l.cost_date) for l in legs] == [
        (Decimal("-5"), Decimal("100.00"), date(2021, 1, 10)),
        (Decimal("-2"), Decimal("110.00"), date(2022, 3, 5)),
    ]
    # cost basis 500 + 220 = 720; gain 1400 - 720 = 680
    gains = next(p for p in data.drafts[0].postings if p.account == "Income:CapitalGains")
    assert gains.amount == Decimal("-680.00")


def test_sell_whole_position_expands_to_all_lots(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("15"), strategy="fifo")])
    assert data.errors == []
    legs = _reducing_legs(data.drafts[0])
    assert len(legs) == 3  # one reducing leg per lot — expected, not a bug
    assert sum(l.amount for l in legs) == Decimal("-15")


def test_sell_with_fee_is_a_separate_expense(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(
        units=Decimal("5"), strategy="fifo",
        fee=Decimal("10.00"), fee_account="Expenses:Fees",
    )])
    assert data.errors == []
    d = data.drafts[0]
    by_acct = {p.account: p for p in d.postings if p.currency == "USD"}
    # 5 @100; proceeds 1000; cost basis 500; gross gain 500 (fee does NOT net it)
    assert by_acct["Assets:Cash"].amount == Decimal("990.00")  # proceeds - fee
    assert by_acct["Expenses:Fees"].amount == Decimal("10.00")
    assert by_acct["Income:CapitalGains"].amount == Decimal("-500.00")


def test_sell_insufficient_lots_refuses(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("100"), strategy="fifo")])
    assert data.drafts == []
    assert data.errors[0].code == "INSUFFICIENT_LOTS"


def test_sell_no_holdings_refuses(tmp_path):
    asm = _assembler(tmp_path, _BASE)  # no VOO lots at all
    data = asm.assemble([_sell(units=Decimal("1"), strategy="fifo")])
    assert data.drafts == []
    assert data.errors[0].code == "INSUFFICIENT_LOTS"


def test_sell_specific_lot_units_mismatch(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(
        units=Decimal("7"),
        lots=[LotSelection(acquisition_date=date(2021, 1, 10), cost_number=Decimal("100.00"), cost_currency="USD", units=Decimal("5"))],
    )])
    assert data.drafts == []
    assert data.errors[0].code == "LOT_UNITS_MISMATCH"


def test_sell_specific_lot_not_found(tmp_path):
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(
        units=Decimal("3"),
        lots=[LotSelection(acquisition_date=date(2019, 1, 1), cost_number=Decimal("99.00"), cost_currency="USD", units=Decimal("3"))],
    )])
    assert data.drafts == []
    assert data.errors[0].code == "LOT_NOT_FOUND"


def _render_draft(draft) -> str:
    """Render a draft as Beancount text (the shape the commit path writes)."""
    lines = [f'{draft.date.isoformat()} {draft.flag} "{draft.narration}"']
    for p in draft.postings:
        line = f"  {p.account} {p.amount} {p.currency}"
        if p.cost_amount is not None:
            date_part = f", {p.cost_date.isoformat()}" if p.cost_date else ""
            line += f" {{{p.cost_amount} {p.cost_currency}{date_part}}}"
        if p.price_amount is not None:
            line += f" {p.price_type} {p.price_amount} {p.price_currency}"
        lines.append(line)
    return "\n".join(lines) + "\n"


# ── CD maturity ───────────────────────────────────────────────────────────────

def test_cd_maturity_geometry(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([CdMaturityEvent(
        type="cd_maturity", date=date(2024, 6, 3), currency="USD",
        cd_account="Assets:CD", principal=Decimal("10000.00"), interest=Decimal("250.00"),
        destination_account="Assets:Cash", interest_income_account="Income:Interest",
    )])
    assert data.errors == []
    d = data.drafts[0]
    assert d.narration == "CD matured"
    assert d.source_account == "Assets:CD"
    by = {p.account: p for p in d.postings}
    assert by["Assets:Cash"].amount == Decimal("10250.00")     # principal + interest
    assert by["Assets:CD"].amount == Decimal("-10000.00")      # principal drawn down
    assert by["Income:Interest"].amount == Decimal("-250.00")


def test_cd_maturity_principal_only_omits_interest_leg(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([CdMaturityEvent(
        type="cd_maturity", date=date(2024, 6, 3), currency="USD",
        cd_account="Assets:CD", principal=Decimal("10000.00"),
        destination_account="Assets:Cash",
    )])
    assert data.errors == []
    d = data.drafts[0]
    assert len(d.postings) == 2
    by = {p.account: p for p in d.postings}
    assert by["Assets:Cash"].amount == Decimal("10000.00")
    assert by["Assets:CD"].amount == Decimal("-10000.00")


def test_cd_maturity_interest_without_income_account_errors(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([CdMaturityEvent(
        type="cd_maturity", date=date(2024, 6, 3), currency="USD",
        cd_account="Assets:CD", principal=Decimal("10000.00"), interest=Decimal("250.00"),
        destination_account="Assets:Cash",
    )])
    assert data.drafts == []
    assert data.errors[0].code == "MISSING_INCOME_ACCOUNT"


# ── Interest ──────────────────────────────────────────────────────────────────

def test_interest_compounding_credits_the_instrument(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([InterestEvent(
        type="interest", date=date(2024, 6, 3), currency="USD",
        deposit_account="Assets:CD", interest=Decimal("50.00"), income_account="Income:Interest",
    )])
    assert data.errors == []
    d = data.drafts[0]
    assert d.narration == "Interest"
    assert d.source_account == "Assets:CD"
    by = {p.account: p for p in d.postings}
    assert by["Assets:CD"].amount == Decimal("50.00")
    assert by["Income:Interest"].amount == Decimal("-50.00")


def test_interest_paid_out_to_a_separate_account(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([InterestEvent(
        type="interest", date=date(2024, 6, 3), currency="USD",
        deposit_account="Assets:Checking", interest=Decimal("50.00"), income_account="Income:Interest",
    )])
    assert data.errors == []
    by = {p.account: p for p in data.drafts[0].postings}
    assert by["Assets:Checking"].amount == Decimal("50.00")
    assert by["Income:Interest"].amount == Decimal("-50.00")


def test_interest_zero_rejected(tmp_path):
    asm = _assembler(tmp_path, _BASE)
    data = asm.assemble([InterestEvent(
        type="interest", date=date(2024, 6, 3), currency="USD",
        deposit_account="Assets:CD", interest=Decimal("0"), income_account="Income:Interest",
    )])
    assert data.drafts == []
    assert data.errors[0].code == "INVALID_AMOUNT"


def test_sell_draft_rebooks_cleanly_against_the_ledger(tmp_path):
    """Round-trip safety (§6.5): the assembled multi-lot sale, appended to the real
    ledger, must load with **zero** booking/balance errors — the pinned {cost, date}
    legs book deterministically under default STRICT booking."""
    asm = _assembler(tmp_path, _MULTI)
    data = asm.assemble([_sell(units=Decimal("7"), strategy="fifo")])
    assert data.errors == []

    combined = _MULTI + _render_draft(data.drafts[0])
    _entries, errors, _options = loader.load_string(combined)
    assert errors == [], [str(e.message) for e in errors]
