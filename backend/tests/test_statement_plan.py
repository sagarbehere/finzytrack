"""The statement-import plan (dev-docs/cas-import.md §6–§8, §13), from synthetic
statements: every transaction type, pairing, mergers, reversals, segregation,
distributors and investors, the cash side (matching / fallback / cutoff / history
start), re-import, gaps, hand entries, corrections and account checks.

Each plan that should be writable is also rendered and loaded through Beancount, so a
test proves the output books cleanly — not just that the plan looks right.
"""

from datetime import date

import pytest

from app.schemas.statement_import_schemas import RowCorrection, StatementMapping
from app.services.lot_math import unit_cost
from app.services.statement_import.plan import build_plan, generate_code
from app.services.statement_import import types as T
from tests.statement_builders import BASE, D, Stmt, load_errors, render, snapshot_of

MAP = dict(
    holding_accounts={"ARN-1": "Assets:Broker"},
    cash_fallback_account="Equity:Funding",
    dividend_account="Income:Dividends",
    taxes_account="Expenses:Taxes",
    gains_account="Income:Gains",
)


def mapping(**over) -> StatementMapping:
    return StatementMapping(**{**MAP, **over})


def plan_of(stmt, base=BASE, corrections=(), **over):
    m = mapping(**over)
    built = stmt.build()
    result = build_plan(built, m, list(corrections), snapshot_of(base))
    return result, built, m


def ok(result, built, m, base=BASE) -> str:
    """Assert the plan is writable and books cleanly; return the resulting ledger."""
    assert result.data.blocking == [], [i.message for i in result.data.blocking]
    text = render(result, base, built, m)
    assert load_errors(text) == []
    return text


def txn(result, kind=None, index=0):
    ts = [t for t in result.data.transactions if kind is None or t.kind == kind]
    return ts[index]


def legs(t):
    return [(p.account, p.units, p.currency) for p in t.postings]


def fund(result, key):
    return next(f for f in result.data.funds if f.security_key == key)


def row(result, rid):
    return next(r for f in result.data.funds for r in f.rows if r.id == rid)


# ── Purchases, redemptions, taxes ────────────────────────────────────────────

def test_purchase_books_units_at_cost_and_stamp_duty_as_expense():
    s = Stmt()
    a = s.fund("A", isin="INF000A")
    s.row(a, date(2020, 6, 16), T.KIND_PURCHASE, amount="99995.00", units="943.972", nav="105.93")
    s.row(a, date(2020, 6, 16), T.KIND_STAMP_DUTY_TAX, amount="5.00")
    r, b, m = plan_of(s)
    ok(r, b, m)
    t = txn(r, "buy")
    code = fund(r, "A").commodity
    assert legs(t) == [("Assets:Broker", D("943.972"), code), ("Expenses:Taxes", D("5.00"), "INR"),
                       ("Equity:Funding", D("-100000.00"), "INR")]
    assert t.postings[0].cost == unit_cost(D("99995.00"), D("943.972"))
    assert fund(r, "A").reconciled is True


def test_redemption_consumes_lots_fifo_and_books_the_gain():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2020, 2, 1), T.KIND_PURCHASE, amount="1200.00", units="100.000", nav="12")
    s.row(a, date(2021, 1, 1), T.KIND_REDEMPTION, amount="-2250.00", units="-150.000", nav="15")
    r, b, m = plan_of(s)
    ok(r, b, m)
    t = txn(r, "sell")
    sells = [(p.units, p.cost, p.cost_date, p.price) for p in t.postings if p.cost is not None]
    assert sells == [(D("-100.000"), D("10"), date(2020, 1, 1), D("15")),
                     (D("-50.000"), D("12"), date(2020, 2, 1), D("15"))]
    assert legs(t)[-2:] == [("Equity:Funding", D("2250.00"), "INR"), ("Income:Gains", D("-650.00"), "INR")]


@pytest.mark.parametrize("printed", ["-1496.00", "-1500.00"])
def test_redemption_tds_whether_amount_is_printed_net_or_gross(printed):
    # 100 units × NAV 15 = 1500 gross; TDS 4 → 1496 reaches the investor either way.
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2021, 1, 1), T.KIND_REDEMPTION, amount=printed, units="-100.000", nav="15")
    s.row(a, date(2021, 1, 1), T.KIND_TDS_TAX, amount="4.00")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert legs(txn(r, "sell"))[-3:] == [("Equity:Funding", D("1496.00"), "INR"), ("Expenses:Taxes", D("4.00"), "INR"),
                                         ("Income:Gains", D("-500.00"), "INR")]


def test_a_sell_with_too_few_units_fails_that_row_and_shows_unreconciled():
    s = Stmt()
    a = s.fund("A", close_units=0)
    s.row(a, date(2021, 1, 1), T.KIND_REDEMPTION, amount="-150.00", units="-10.000", nav="15")
    r, b, m = plan_of(s)
    assert row(r, s.rows[0].id).status == "failed"
    assert [w.code for w in r.data.warnings].count("INSUFFICIENT_UNITS") == 1
    assert fund(r, "A").reconciled is True  # nothing imported, statement closes at 0


def test_same_day_reinvestment_is_booked_before_the_sale_it_funds():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(a, date(2020, 5, 1), T.KIND_REDEMPTION, amount="-110.00", units="-10.004", nav="11")
    s.row(a, date(2020, 5, 1), T.KIND_DIVIDEND_REINVEST, amount="0.04", units="0.004", nav="11")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert fund(r, "A").reconciled is True
    assert [t.kind for t in r.data.transactions] == ["buy", "reinvest", "sell"]


# ── Dividends ────────────────────────────────────────────────────────────────

def test_reinvestment_books_gross_income_with_tds_and_stamp_duty():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 7, 6), T.KIND_DIVIDEND_REINVEST, amount="195.48", units="15.047", nav="12.991")
    s.row(a, date(2020, 7, 6), T.KIND_TDS_TAX, amount="52.00")
    s.row(a, date(2020, 7, 6), T.KIND_STAMP_DUTY_TAX, amount="0.01")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert legs(txn(r, "reinvest"))[1:] == [("Expenses:Taxes", D("52.01"), "INR"),
                                            ("Income:Dividends", D("-247.49"), "INR")]


def test_tds_stated_only_in_a_reinvestment_description_is_booked():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2022, 3, 14), T.KIND_DIVIDEND_REINVEST, amount="26130.75", units="668.323", nav="39.099",
          desc="IDCW Reinvestment @ Rs.1.75 per unit (TDS :6862.96, TDS Rate: 20.80%)")
    s.row(a, date(2022, 3, 14), T.KIND_STAMP_DUTY_TAX, amount="1.31")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert legs(txn(r, "reinvest"))[1:] == [("Expenses:Taxes", D("6864.27"), "INR"),
                                            ("Income:Dividends", D("-32995.02"), "INR")]


def test_cash_dividend_goes_to_the_cash_side():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2021, 2, 4), T.KIND_DIVIDEND_PAYOUT, amount="350.00", desc="***IDCW Paid @ Rs.3.50 per unit***")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert legs(txn(r, "dividend")) == [("Equity:Funding", D("350.00"), "INR"), ("Income:Dividends", D("-350.00"), "INR")]


def test_dividend_transfers_between_funds_are_income_not_new_money():
    s = Stmt()
    src = s.fund("SRC")
    dst = s.fund("DST")
    s.row(src, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    marker = s.row(src, date(2021, 3, 19), T.KIND_DIVIDEND_PAYOUT, desc="***IDCW TransferDTP @ Rs.1.10 per unit to X***")
    paid_as_units = s.row(dst, date(2021, 3, 19), T.KIND_DIVIDEND_PAYOUT, amount="110.00", units="11.000", nav="10",
                          desc="IDCW Transfer @ Rs.1.10 per unit - From SRC")
    sweep_in = s.row(dst, date(2021, 3, 30), T.KIND_PURCHASE, amount="50.00", units="5.000", nav="10",
                     desc="IDCW Sweep In EO-DP-D-1DTPO(From EO (DP))")
    sweep_out = s.row(src, date(2021, 3, 29), T.KIND_MISC, amount="50.00", desc="***IDCW Sweep Out LF-DP-R***")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert row(r, marker).status == "info"
    assert row(r, sweep_out).status == "info"
    assert (row(r, paid_as_units).interpretation, row(r, sweep_in).interpretation) == ("reinvest", "reinvest")
    assert [t.kind for t in r.data.transactions].count("buy") == 1  # only the real purchase


# ── Switches ─────────────────────────────────────────────────────────────────

def test_switch_becomes_one_transaction_without_a_cash_leg():
    s = Stmt()
    a = s.fund("A")
    bb = s.fund("B")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2021, 7, 7), T.KIND_SWITCH_OUT, amount="-1000.00", units="-50.000", nav="20")
    s.row(bb, date(2021, 7, 7), T.KIND_SWITCH_IN, amount="999.95", units="10.000", nav="99.995")
    s.row(bb, date(2021, 7, 7), T.KIND_STAMP_DUTY_TAX, amount="0.05")
    r, b, m = plan_of(s)
    ok(r, b, m)
    t = txn(r, "switch")
    assert [p.account for p in t.postings] == ["Assets:Broker", "Assets:Broker", "Expenses:Taxes", "Income:Gains"]
    assert t.postings[1].cost == unit_cost(D("999.95"), D("10.000"))
    assert t.postings[-1].units == D("-500.00")  # 50 units bought at 10, switched out at 20
    assert t.cash_matched is None
    assert r.data.summary.fallback == 1  # only the purchase touches the cash side


def test_switch_pairs_across_allotment_lag_unit_rounding_and_rounded_tds():
    # Seen on a real CAS: TDS printed as ₹4.00 while the fund moved gross − ₹3.26,
    # and the switch-in allotted days later.
    s = Stmt()
    a = s.fund("A", institution="Sundaram Mutual Fund", folio="S1")
    bb = s.fund("B", institution="Sundaram Mutual Fund", folio="S1")
    s.row(a, date(2018, 1, 1), T.KIND_PURCHASE, amount="6000.00", units="500.000", nav="12")
    s.row(a, date(2018, 10, 17), T.KIND_SWITCH_OUT, amount="-5996.00", units="-495.602", nav="12.1065")
    s.row(a, date(2018, 10, 17), T.KIND_TDS_TAX, amount="4.00")
    s.row(bb, date(2018, 10, 22), T.KIND_SWITCH_IN, amount="5996.74", units="13.838", nav="433.3492")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert [t.kind for t in r.data.transactions] == ["buy", "switch"]
    assert "UNPAIRED_SWITCH" not in [w.code for w in r.data.warnings]
    sw = txn(r, "switch")
    bought = next(p for p in sw.postings if p.currency == fund(r, "B").commodity)
    assert bought.cost == unit_cost(D("5996.74"), D("13.838"))  # the registrar's purchase amount
    assert ("Expenses:Taxes", D("3.26"), "INR") in legs(sw)     # the TDS actually withheld


def test_switch_unit_rounding_without_tds_adjusts_the_gain():
    s = Stmt()
    a = s.fund("A")
    bb = s.fund("B")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2021, 1, 1), T.KIND_SWITCH_OUT, amount="-600.00", units="-50.000", nav="12")
    s.row(bb, date(2021, 1, 1), T.KIND_SWITCH_IN, amount="600.30", units="1.400", nav="428.80")
    r, b, m = plan_of(s)
    ok(r, b, m)
    sw = txn(r, "switch")
    bought = next(p for p in sw.postings if p.currency == fund(r, "B").commodity)
    assert bought.cost == unit_cost(D("600.30"), D("1.400"))
    assert sw.postings[-1].units == D("-100.30")  # 600 − 500 cost, plus the 0.30 the statement can't place


def test_switches_never_pair_across_fund_houses():
    s = Stmt()
    a = s.fund("A", institution="DSP Mutual Fund", folio="F1")
    bb = s.fund("B", institution="HDFC Mutual Fund", folio="F2")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2021, 1, 1), T.KIND_SWITCH_OUT, amount="-500.00", units="-50.000", nav="10")
    s.row(bb, date(2021, 1, 1), T.KIND_SWITCH_IN, amount="500.00", units="50.000", nav="10")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert [t.kind for t in r.data.transactions] == ["buy", "buy", "sell"]  # a day's additions go first
    assert [w.code for w in r.data.warnings].count("UNPAIRED_SWITCH") == 2


def test_lateral_shift_from_an_idcw_fund_reads_as_a_switch():
    s = Stmt()
    a = s.fund("A", institution="Nippon India Mutual Fund", folio="N1")
    bb = s.fund("B", institution="Nippon India Mutual Fund", folio="N1")
    s.row(a, date(2011, 1, 1), T.KIND_PURCHASE, amount="30000.00", units="3000.000", nav="10")
    out = s.row(a, date(2011, 8, 9), T.KIND_REDEMPTION, amount="-30000.00", units="-2898.187", nav="10.3513",
                desc="Lateral Shift Out (To EO (DP))(To NIPPON INDIA MULTI CAP FUND)")
    inn = s.row(bb, date(2011, 8, 9), T.KIND_PURCHASE, amount="30000.00", units="1346.832", nav="22.2745",
                desc="Lateral Shift In (From LF (DP))(From NIPPON INDIA LIQUID FUND - WEEKLY IDCW OPTION)")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert (row(r, out).interpretation, row(r, inn).interpretation) == ("switch_out", "switch_in")
    assert row(r, out).pair_id == inn


# ── Mergers, segregation, reversals ──────────────────────────────────────────

def test_merger_carries_cost_and_acquisition_dates_without_a_gain():
    s = Stmt()
    old = s.fund("OLD", institution="HDFC Mutual Fund", folio="H1")
    new = s.fund("NEW", institution="HDFC Mutual Fund", folio="H1")
    s.row(old, date(2015, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(old, date(2016, 1, 1), T.KIND_PURCHASE, amount="1500.00", units="100.000", nav="15")
    s.row(old, date(2018, 6, 1), T.KIND_SWITCH_OUT_MERGER, amount="-4000.00", units="-200.000", nav="20")
    s.row(new, date(2018, 6, 1), T.KIND_SWITCH_IN_MERGER, amount="4000.00", units="500.000", nav="8")
    s.row(new, date(2020, 1, 1), T.KIND_REDEMPTION, amount="-2500.00", units="-250.000", nav="10")
    r, b, m = plan_of(s)
    ok(r, b, m)
    merge = txn(r, "merge")
    new_lots = [(p.units, p.cost_date) for p in merge.postings if p.units > 0 and p.cost is not None]
    assert new_lots == [(D("250.000"), date(2015, 1, 1)), (D("250.000"), date(2016, 1, 1))]
    carried = sum(p.units * p.cost for p in merge.postings if p.units > 0 and p.cost is not None)
    assert abs(carried - D("2500")) < D("0.000001")
    assert merge.postings[-1].units == D("0.00")
    sell = txn(r, "sell")
    assert sell.postings[0].cost_date == date(2015, 1, 1)  # the original lot, FIFO
    assert sell.postings[-1].units == D("-1500.00")        # 2500 − carried cost of 1000


def test_merger_described_only_in_words_and_face_value_change():
    s = Stmt()
    a = s.fund("A", institution="Sundaram Mutual Fund", folio="S1")
    bb = s.fund("B", institution="Sundaram Mutual Fund", folio="S1")
    s.row(a, date(2019, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    out = s.row(a, date(2021, 12, 31), T.KIND_SWITCH_OUT, amount="-1200.00", units="-100.000", nav="12",
                desc="Switch Out Merged into FFIW")
    s.row(bb, date(2021, 12, 31), T.KIND_SWITCH_IN, amount="1200.00", units="1.200", nav="1000",
          desc="Switch In Merged from LDWD")
    c = s.fund("C", institution="Sundaram Mutual Fund", folio="S1")
    s.row(c, date(2012, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(c, date(2012, 8, 5), T.KIND_SWITCH_IN, amount="100.00", units="0.100", nav="1000",
          desc="Switch over In FACE VALUE CHANGE IN")
    s.row(c, date(2012, 8, 5), T.KIND_SWITCH_OUT, amount="-100.00", units="-10.000", nav="10",
          desc="Switch over Out FACE VALUE CHANGE OUT")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert row(r, out).interpretation == "merge_out"
    narrations = sorted(t.narration for t in r.data.transactions if t.kind == "merge")
    code_c = fund(r, "C").commodity
    assert narrations[0] == f"Face value change {code_c}"
    assert all(f.reconciled for f in r.data.funds)


def test_segregated_portfolio_arrives_at_zero_cost():
    s = Stmt()
    a = s.fund("SEG")
    s.row(a, date(2020, 3, 1), T.KIND_SEGREGATION, units="100.000")
    r, b, m = plan_of(s)
    ok(r, b, m)
    t = txn(r, "segregation")
    assert [(p.units, p.cost) for p in t.postings] == [(D("100.000"), D("0"))]


def test_reversal_cancels_its_original_in_the_same_statement():
    s = Stmt()
    a = s.fund("A")
    orig = s.row(a, date(2020, 1, 1), T.KIND_PURCHASE_SIP, amount="1000.00", units="100.000", nav="10")
    rev = s.row(a, date(2020, 1, 5), T.KIND_REVERSAL, amount="-1000.00", units="-100.000", nav="10")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert (row(r, orig).status, row(r, rev).status) == ("reversed", "reversed")
    assert r.data.transactions == []
    assert fund(r, "A").reconciled is True


def test_reversal_of_an_already_imported_purchase_becomes_a_reversing_entry():
    first = Stmt(end=date(2020, 1, 2))
    a = first.fund("A", isin="INF000A")
    first.row(a, date(2020, 1, 1), T.KIND_PURCHASE_SIP, amount="1000.00", units="100.000", nav="10")
    r1, b1, m1 = plan_of(first)
    ledger = ok(r1, b1, m1)

    second = Stmt(start=date(2020, 1, 1), end=date(2020, 2, 1))
    a2 = second.fund("A", isin="INF000A")
    second.row(a2, date(2020, 1, 1), T.KIND_PURCHASE_SIP, amount="1000.00", units="100.000", nav="10")
    rev = second.row(a2, date(2020, 1, 5), T.KIND_REVERSAL, amount="-1000.00", units="-100.000", nav="10")
    r2, b2, m2 = plan_of(second, base=ledger)
    ok(r2, b2, m2, base=ledger)
    assert row(r2, rev).status == "import"
    t = txn(r2, "reversal")
    assert [(p.account, p.units) for p in t.postings] == [
        ("Assets:Broker", D("-100.000")), ("Equity:Funding", D("1000.00")), ("Income:Gains", D("0.00"))]
    assert fund(r2, "A").reconciled is True


# ── Never guessed ────────────────────────────────────────────────────────────

def test_misc_and_unknown_rows_are_listed_never_imported():
    s = Stmt()
    a = s.fund("A")
    info = s.row(a, date(2020, 1, 1), T.KIND_MISC, desc="***Address Updated***")
    unknown = s.row(a, date(2020, 2, 1), T.KIND_UNKNOWN, units="5.000", amount="50.00", desc="Something")
    r, b, m = plan_of(s)
    assert (row(r, info).status, row(r, unknown).status) == ("info", "unsupported")
    assert "ROW_NOT_IMPORTED" in [w.code for w in r.data.warnings]
    assert fund(r, "A").reconciled is False  # the unplaced 5 units show as a gap


# ── Distributors, investors, balances ────────────────────────────────────────

def test_each_distributor_needs_its_own_holding_account():
    s = Stmt()
    a = s.fund("A", advisor="ARN-1")
    bb = s.fund("B", advisor=None, folio="F2")  # a direct plan
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(bb, date(2020, 1, 1), T.KIND_PURCHASE, amount="200.00", units="10.000", nav="20")
    r, _b, _m = plan_of(s)
    assert [i.code for i in r.data.blocking] == ["ACCOUNT_REQUIRED"]
    r, b, m = plan_of(s, holding_accounts={"ARN-1": "Assets:Broker", "DIRECT": "Assets:Broker2"})
    ok(r, b, m)
    assert sorted(t.postings[0].account for t in r.data.transactions) == ["Assets:Broker", "Assets:Broker2"]
    assert sorted((x.account, x.units) for x in r.data.balances) == [
        ("Assets:Broker", D("10.000")), ("Assets:Broker2", D("10.000"))]


def test_one_balance_assertion_per_account_and_commodity_with_tolerance():
    s = Stmt(end=date(2024, 3, 31))
    a1 = s.fund("A", folio="F1")
    a2 = s.fund("A", folio="F2")  # same fund in a second folio, same holding account
    s.row(a1, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(a2, date(2020, 1, 1), T.KIND_PURCHASE, amount="50.00", units="5.000", nav="10")
    r, b, m = plan_of(s)
    ok(r, b, m)
    assert [(x.date, x.account, x.units, x.tolerance) for x in r.data.balances] == [
        (date(2024, 4, 1), "Assets:Broker", D("15.000"), D("0.001"))]


def test_several_investors_in_one_statement():
    s = Stmt()
    a = s.fund("A", folio="F1", investor="I1", investor_name="First Holder")
    bb = s.fund("B", folio="F2", investor="I2", investor_name="Second Holder")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(bb, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, b, m = plan_of(s)
    text = ok(r, b, m)
    assert r.data.summary.investors == ["First Holder", "Second Holder"]
    assert sorted(f.investor for f in r.data.funds) == ["First Holder", "Second Holder"]
    assert "Holder" not in text  # investor names never reach the ledger


# ── Cash side ────────────────────────────────────────────────────────────────

BANKED = BASE + """
2020-01-03 * "Bank" "To mutual fund"
  Assets:Transfer   100000.00 INR
  Assets:Bank      -100000.00 INR
2020-03-20 * "Bank" "To mutual fund (late)"
  Assets:Transfer   500.00 INR
  Assets:Bank      -500.00 INR
"""


def _purchases(s, f, *dates):
    for d in dates:
        s.row(f, d, T.KIND_PURCHASE, amount="99995.00", units="9999.500", nav="10")
        s.row(f, d, T.KIND_STAMP_DUTY_TAX, amount="5.00")


def test_matching_uses_the_bank_transaction_once_within_the_window_else_fallback():
    s = Stmt()
    a = s.fund("A")
    _purchases(s, a, date(2020, 1, 1), date(2020, 1, 2))
    s.row(a, date(2020, 3, 1), T.KIND_PURCHASE, amount="500.00", units="50.000", nav="10")  # bank txn 19 days later
    r, b, m = plan_of(s, base=BANKED, cash_matching_account="Assets:Transfer")
    ok(r, b, m, base=BANKED)
    cash = [next(p.account for p in t.postings if p.cost is None and p.account != "Expenses:Taxes")
            for t in r.data.transactions]
    assert cash == ["Assets:Transfer", "Equity:Funding", "Equity:Funding"]
    assert (r.data.summary.matched, r.data.summary.fallback) == (1, 2)
    assert [(u.date, u.amount) for u in r.data.unclaimed_bank_transactions] == [(date(2020, 3, 20), D("500.00"))]
    assert r.transactions[0].meta["stmt_match"]


def test_matching_window_is_tunable():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 3, 1), T.KIND_PURCHASE, amount="500.00", units="50.000", nav="10")
    r, *_ = plan_of(s, base=BANKED, cash_matching_account="Assets:Transfer", match_window_days=31)
    assert r.data.summary.matched == 1


def test_matching_account_alone_takes_every_cash_movement():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 3, 1), T.KIND_PURCHASE, amount="10.00", units="1.000", nav="10")
    r, b, m = plan_of(s, base=BANKED, cash_matching_account="Assets:Transfer", cash_fallback_account=None)
    ok(r, b, m, base=BANKED)
    assert txn(r, "buy").postings[-1].account == "Assets:Transfer"


def test_cutoff_date_switches_the_cash_side():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2012, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(a, date(2022, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, b, m = plan_of(s, cutoff_date=date(2020, 1, 1), cash_before_cutoff_account="Equity:Old")
    ok(r, b, m)
    assert [t.postings[-1].account for t in r.data.transactions] == ["Equity:Old", "Equity:Funding"]


def test_cutoff_without_its_account_blocks():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2012, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, *_ = plan_of(s, cutoff_date=date(2020, 1, 1))
    assert [i.code for i in r.data.blocking] == ["ACCOUNT_REQUIRED"]


def test_history_start_opens_lots_with_their_real_dates_and_costs():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2019, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2019, 6, 1), T.KIND_DIVIDEND_PAYOUT, amount="50.00")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1200.00", units="100.000", nav="12")
    s.row(a, date(2020, 6, 1), T.KIND_REDEMPTION, amount="-1300.00", units="-100.000", nav="13")
    s.row(a, date(2021, 6, 1), T.KIND_REDEMPTION, amount="-1500.00", units="-100.000", nav="15")
    r, b, m = plan_of(s, history_start=date(2021, 1, 1))
    ok(r, b, m)
    opening, sell = r.data.transactions
    assert (opening.kind, opening.date) == ("opening", date(2021, 1, 1))
    assert [(p.units, p.cost, p.cost_date) for p in opening.postings if p.cost is not None] == [
        (D("100.000"), D("12"), date(2020, 1, 1))]
    assert opening.postings[-1] .account == "Equity:Funding"
    assert sell.postings[0].cost_date == date(2020, 1, 1)
    assert fund(r, "A").counts == {"before_start": 4, "import": 1}
    assert fund(r, "A").reconciled is True


# ── Re-import ────────────────────────────────────────────────────────────────

def _two_years():
    s = Stmt(start=date(2019, 1, 1), end=date(2020, 12, 31))
    a = s.fund("A", isin="INF000A", amfi="100")
    s.row(a, date(2019, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1200.00", units="100.000", nav="12")
    return s, a


def test_reimport_of_an_overlapping_statement_adds_only_new_rows():
    s1, _ = _two_years()
    r1, b1, m1 = plan_of(s1)
    ledger = ok(r1, b1, m1)

    s2, a2 = _two_years()
    s2.end = date(2021, 12, 31)
    new = s2.row(a2, date(2021, 6, 1), T.KIND_REDEMPTION, amount="-1500.00", units="-100.000", nav="15")
    r2, b2, m2 = plan_of(s2, base=ledger)
    after = ok(r2, b2, m2, base=ledger)
    assert [t.row_ids for t in r2.data.transactions] == [[new]]
    assert r2.data.summary.already_imported == 2
    assert r2.data.commodities == []  # matched by ISIN, metadata already set
    assert fund(r2, "A").commodity == fund(r1, "A").commodity
    assert fund(r2, "A").reconciled is True
    assert [b.date for b in r2.data.balances] == [date(2022, 1, 1)]
    assert load_errors(after) == []


def test_reimport_of_the_same_statement_writes_no_transactions():
    s1, _ = _two_years()
    r1, b1, m1 = plan_of(s1)
    ledger = ok(r1, b1, m1)
    s2, _ = _two_years()
    r2, *_ = plan_of(s2, base=ledger)
    assert r2.data.transactions == [] and r2.data.balances == []


def test_statement_starting_later_continues_from_the_ledger_lots():
    s1, _ = _two_years()
    r1, b1, m1 = plan_of(s1)
    ledger = ok(r1, b1, m1)
    s2 = Stmt(start=date(2021, 1, 1), end=date(2021, 12, 31))
    a2 = s2.fund("A", isin="INF000A", amfi="100", open_units="200")
    s2.row(a2, date(2021, 6, 1), T.KIND_REDEMPTION, amount="-1500.00", units="-150.000", nav="10")
    r2, b2, m2 = plan_of(s2, base=ledger)
    ok(r2, b2, m2, base=ledger)
    sell = txn(r2, "sell")
    assert [(p.units, p.cost_date) for p in sell.postings if p.cost is not None] == [
        (D("-100.000"), date(2019, 1, 1)), (D("-50.000"), date(2020, 1, 1))]
    assert fund(r2, "A").reconciled is True


def test_period_gap_is_warned():
    s1, _ = _two_years()
    r1, b1, m1 = plan_of(s1)
    ledger = ok(r1, b1, m1)
    s2 = Stmt(start=date(2021, 8, 1), end=date(2021, 12, 31))
    s2.fund("A", isin="INF000A", open_units="200")
    r2, *_ = plan_of(s2, base=ledger)
    assert "PERIOD_GAP" in [w.code for w in r2.data.warnings]


HAND = BASE + """
2019-01-01 commodity MYFUND
  isin: "INF000A"
2023-01-01 * "Opening holding MYFUND"
  Assets:Broker   200.000 MYFUND {11.00 INR}
  Equity:Funding  -2200.00 INR
"""


def test_hand_entered_holdings_must_be_resolved_first():
    s, _ = _two_years()
    r, *_ = plan_of(s, base=HAND)
    assert [i.code for i in r.data.blocking] == ["HAND_ENTRIES"]
    assert fund(r, "A").hand_entries == 1


def test_hand_entries_replaced_by_the_statement_history():
    s, _ = _two_years()
    r, *_ = plan_of(s, base=HAND, hand_entries={"A": "replace"})
    assert r.data.blocking == []
    assert r.removed_refs and r.data.removed_hand_entries == 1
    assert len(r.data.transactions) == 2


def test_hand_entries_kept_and_only_later_rows_imported():
    s, a = _two_years()
    s.end = date(2024, 12, 31)
    later = s.row(a, date(2024, 6, 1), T.KIND_REDEMPTION, amount="-1500.00", units="-100.000", nav="15")
    r, b, m = plan_of(s, base=HAND, hand_entries={"A": "after_latest"})
    ok(r, b, m, base=HAND)
    assert [t.row_ids for t in r.data.transactions] == [[later]]
    assert txn(r, "sell").postings[0].cost == D("11.00")  # from the hand-entered lot
    assert fund(r, "A").counts == {"before_hand": 2, "import": 1}


# ── Corrections ──────────────────────────────────────────────────────────────

def test_corrections_exclude_reclassify_edit_and_pair():
    s = Stmt()
    a = s.fund("A", close_units="80")  # the statement's closing units don't count the duplicate
    bb = s.fund("B")
    buy = s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    dup = s.row(a, date(2020, 1, 2), T.KIND_PURCHASE, amount="10.00", units="1.000", nav="10")
    sweep = s.row(bb, date(2020, 2, 1), T.KIND_PURCHASE, amount="50.00", units="5.000", nav="10")
    out = s.row(a, date(2020, 3, 1), T.KIND_REDEMPTION, amount="-200.00", units="-20.000", nav="10")
    inn = s.row(bb, date(2020, 3, 9), T.KIND_PURCHASE, amount="199.00", units="19.900", nav="10")
    corrections = [
        RowCorrection(row_id=dup, exclude=True),
        RowCorrection(row_id=sweep, interpretation="reinvest"),
        RowCorrection(row_id=out, pair_with=inn),
        RowCorrection(row_id=buy, amount=D("1000.50")),
    ]
    r, b, m = plan_of(s, corrections=corrections)
    ok(r, b, m)
    assert row(r, dup).status == "excluded" and row(r, dup).corrected is True
    assert row(r, sweep).interpretation == "reinvest" and row(r, sweep).proposed == "buy"
    assert row(r, out).pair_id == inn
    assert txn(r, "buy").postings[0].cost == unit_cost(D("1000.50"), D("100.000"))
    sw = txn(r, "switch")
    assert ("Equity:Funding", D("1.00"), "INR") in legs(sw)  # a forced pair that doesn't add up: the gap is cash


def test_unpair_correction_keeps_halves_apart():
    s = Stmt()
    a = s.fund("A")
    bb = s.fund("B")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    out = s.row(a, date(2021, 1, 1), T.KIND_SWITCH_OUT, amount="-500.00", units="-50.000", nav="10")
    s.row(bb, date(2021, 1, 1), T.KIND_SWITCH_IN, amount="500.00", units="50.000", nav="10")
    r, *_ = plan_of(s, corrections=[RowCorrection(row_id=out, pair_with="")])
    assert [t.kind for t in r.data.transactions] == ["buy", "buy", "sell"]


def test_year_end_check_pins_the_year_a_gap_starts():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2019, 5, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    gap = s.row(a, date(2020, 5, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(a, date(2021, 5, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, *_ = plan_of(s, corrections=[RowCorrection(row_id=gap, exclude=True)])
    years = {y.year: y.ok for y in fund(r, "A").years}
    assert years == {2019: True, 2020: False, 2021: False}


# ── Accounts, commodities, setup checks ──────────────────────────────────────

def test_account_problems_block_with_a_reason():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    late = BASE.replace("2000-01-01 open Equity:Funding", "2021-01-01 open Equity:Funding")
    r, *_ = plan_of(s, base=late)
    assert [i.code for i in r.data.blocking] == ["ACCOUNT_OPENS_TOO_LATE"]
    r, *_ = plan_of(s, cash_fallback_account="Equity:Nope")
    assert [i.code for i in r.data.blocking] == ["ACCOUNT_NOT_FOUND"]
    usd = BASE.replace("2000-01-01 open Assets:Broker\n", "2000-01-01 open Assets:Broker USD\n")
    r, *_ = plan_of(s, base=usd)
    assert [i.code for i in r.data.blocking] == ["ACCOUNT_CURRENCY"]


def test_inr_must_be_an_operating_currency():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, *_ = plan_of(s, base=BASE.replace('"INR"', '"USD"'))
    assert [i.code for i in r.data.blocking] == ["CURRENCY_NOT_OPERATING"]


def test_existing_commodity_matched_by_isin_or_amfi_and_metadata_only_filled():
    base = BASE + '2000-01-01 commodity PPFAS\n  fetch_symbol: "amfi:122639"\n  name: "My own name"\n'
    s = Stmt()
    a = s.fund("A", isin="INF879O01027", amfi="122639", name="Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
               institution="PPFAS Mutual Fund")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, b, m = plan_of(s, base=base)
    ok(r, b, m, base=base)
    assert fund(r, "A").commodity == "PPFAS"
    assert r.data.commodities[0].set_metadata == {
        "asset-class": "mutual-fund", "amc": "PPFAS Mutual Fund", "isin": "INF879O01027"}


def test_commodity_code_overrides_are_validated():
    s = Stmt()
    a = s.fund("A", isin="I1")
    bb = s.fund("B", isin="I2")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    s.row(bb, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, *_ = plan_of(s, commodity_codes={"A": "bad code"})
    assert [i.code for i in r.data.blocking] == ["COMMODITY_CODE_INVALID"]
    r, *_ = plan_of(s, commodity_codes={"A": "SAME", "B": "SAME"})
    assert [i.code for i in r.data.blocking] == ["COMMODITY_CODE_DUPLICATE"]
    r, *_ = plan_of(s, commodity_codes={"A": "INR"})
    assert [i.code for i in r.data.blocking] == ["COMMODITY_CODE_IS_CURRENCY"]


def test_generated_codes_are_readable_valid_and_unique():
    sec = T.StmtSecurity(key="k", name="HDFC Balanced Advantage Fund - Regular Plan - Growth", institution="HDFC Mutual Fund")
    assert generate_code(sec, set()) == "HDFC-BALANCEDADV-RG"
    assert generate_code(sec, {"HDFC-BALANCEDADV-RG"}) == "HDFC-BALANCEDADV2-RG"
    weekly = T.StmtSecurity(key="w", name="Nippon India Liquid Fund - Weekly IDCW Option (Non", institution="Nippon India Mutual Fund")
    assert generate_code(weekly, set()) == "NIPPON-LIQUID-RIW"
    direct = T.StmtSecurity(key="d", name="Aditya Birla Sun Life Flexi Cap Fund - Direct Plan - Growth",
                            institution="Aditya Birla Sun Life Mutual Fund")
    assert generate_code(direct, set()) == "ABSL-FLEXICAP-DG"


def test_new_commodity_gets_its_metadata_and_a_price():
    s = Stmt(end=date(2024, 12, 31))
    a = s.fund("A", isin="INF000A", amfi="555", nav="12.5", nav_date=date(2024, 12, 30))
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, b, m = plan_of(s)
    ok(r, b, m)
    c = r.data.commodities[0]
    assert (c.status, c.set_metadata) == ("new", {
        "name": "DSP A Fund - Regular Plan - Growth", "asset-class": "mutual-fund", "amc": "DSP Mutual Fund",
        "isin": "INF000A", "fetch_symbol": "amfi:555"})
    assert [(p.date, p.commodity, p.price) for p in r.data.prices] == [(date(2024, 12, 30), c.code, D("12.5"))]


def test_dividend_account_without_income_type_is_offered_a_tag():
    base = BASE.replace('2000-01-01 open Income:Dividends\n  income-type: "dividend"\n', "2000-01-01 open Income:Dividends\n")
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_DIVIDEND_REINVEST, amount="10.00", units="1.000", nav="10")
    r, *_ = plan_of(s, base=base)
    assert "DIVIDEND_ACCOUNT_UNTAGGED" in [w.code for w in r.data.warnings]
    r, *_ = plan_of(s, base=base, tag_dividend_account=True)
    assert r.account_metadata == {"Income:Dividends": {"income-type": "dividend"}}


def test_target_file_must_already_be_part_of_the_ledger():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    r, *_ = plan_of(s, target_file="/somewhere/else.beancount")
    assert [i.code for i in r.data.blocking] == ["TARGET_FILE_UNKNOWN"]


def test_preview_hash_is_stable_and_follows_the_mapping():
    s = Stmt()
    a = s.fund("A")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="100.00", units="10.000", nav="10")
    h1 = plan_of(s)[0].data.preview_hash
    assert plan_of(s)[0].data.preview_hash == h1
    assert plan_of(s, cash_fallback_account="Equity:Old")[0].data.preview_hash != h1


def test_an_existing_assertion_that_now_disagrees_is_warned_not_changed():
    s1, _ = _two_years()
    r1, b1, m1 = plan_of(s1)
    ledger = ok(r1, b1, m1)
    s2, a2 = _two_years()
    s2.row(a2, date(2020, 6, 1), T.KIND_PURCHASE, amount="10.00", units="1.000", nav="10")  # a row the first lacked
    r2, *_ = plan_of(s2, base=ledger)
    assert r2.data.balances == []
    assert "BALANCE_DIFFERS" in [w.code for w in r2.data.warnings]


def test_row_messages_say_which_fund_date_and_amount():
    s = Stmt()
    a = s.fund("A", name="Fund Alpha", institution="DSP Mutual Fund", folio="F1")
    bb = s.fund("B", name="Fund Beta", institution="HDFC Mutual Fund", folio="F2")
    s.row(a, date(2020, 1, 1), T.KIND_PURCHASE, amount="1000.00", units="100.000", nav="10")
    out = s.row(a, date(2022, 6, 29), T.KIND_SWITCH_OUT, amount="-500.00", units="-50.000", nav="10")
    s.row(bb, date(2022, 6, 30), T.KIND_SWITCH_IN, amount="500.00", units="50.000", nav="10")
    r, *_ = plan_of(s)
    w = next(w for w in r.data.warnings if w.row_id == out)
    assert w.message.startswith("Fund Alpha, 2022-06-29, -500.00: ")
