"""Demo-ledger generator: build-time anchoring, budget dating, determinism, and
that it parses (dev-docs/seed-content-refresh.md §8, §12).

The generator is a standalone build/dev script (backend/scripts/), imported here
only to unit-test its re-anchoring + budget behaviour.
"""

import re
from datetime import date

import beancount.loader as loader
import pytest

import scripts.generate_fake as gen


def _dates(text):
    return [date.fromisoformat(l[:10]) for l in text.splitlines() if re.match(r"\d{4}-\d{2}-\d{2} ", l)]


def _budget_lines(text):
    return [l for l in text.splitlines() if re.match(r'\d{4}-\d{2}-\d{2} custom "budget"', l)]


def test_written_ledger_is_lf_on_every_platform(tmp_path):
    """The demo ledger must be LF wherever it's built. It is the one bundled asset
    .gitattributes cannot normalize — CI generates it at build time rather than
    checking it out — so a text-mode write would ship a wholly CRLF ledger from the
    Windows runner and an LF one from macOS/Linux, breaking the invariant that a
    bundle is byte-identical whatever host built it.

    (On a POSIX host this passes either way, since there is no translation to
    suppress; it fails on Windows if the `newline=""` on the writers is lost.)"""
    out = gen.ensure_seed_ledger(
        out=tmp_path / "fake.beancount", anchor_month=date(2026, 5, 1)
    )
    data = out.read_bytes()
    assert b"\r" not in data
    # The content itself is LF-only, so a CR could only come from the writer.
    assert "\r" not in gen.generate(anchor_month=date(2026, 5, 1), buffer_months=2)


def test_deterministic_output():
    a = gen.generate(anchor_month=date(2026, 5, 1), buffer_months=2)
    b = gen.generate(anchor_month=date(2026, 5, 1), buffer_months=2)
    assert a == b


@pytest.mark.parametrize("anchor,buffer", [
    (date(2026, 1, 1), 2),
    (date(2027, 9, 1), 2),
    (date(2025, 11, 1), 3),
])
def test_span_ends_at_anchor_plus_buffer(anchor, buffer):
    text = gen.generate(anchor_month=anchor, buffer_months=buffer)
    last = max(_dates(text))
    expected_end = gen._add_months(anchor, buffer)
    # The last transaction lands in the anchor+buffer month (clean calendar end).
    assert (last.year, last.month) == (expected_end.year, expected_end.month)


def test_parses_without_errors_and_currencies_hardcoded():
    text = gen.generate(anchor_month=date(2026, 7, 1), buffer_months=2)
    # Demo currencies are hardcoded (no seed-time substitution needed for the demo).
    assert "{default_currency}" not in text
    assert re.search(r"\bUSD\b", text) and re.search(r"\bINR\b", text)
    _, errors, _ = loader.load_string(text)
    assert errors == []


def test_budget_directives_present_and_dated_relative_to_anchor():
    anchor, buffer = date(2026, 6, 1), 2
    text = gen.generate(anchor_month=anchor, buffer_months=buffer)
    budgets = _budget_lines(text)
    # 19 directives: a quoted-root total + per-account monthly + a mid-span raise +
    # three nested group/child sets (EatingOut, Utilities, Insurance) + yearly + INR.
    assert len(budgets) == 19
    assert any('custom "budget" "Expenses" ' in l for l in budgets)  # quoted-root total (zero-based)
    # A group total with a budgeted child under it (hierarchical zero-based).
    assert any("Expenses:Utilities " in l for l in budgets)
    assert any("Expenses:Utilities:Electric" in l for l in budgets)
    assert any(' "yearly" ' in l for l in budgets)               # non-monthly interval
    assert any(l.rstrip().endswith("INR   ; second currency (multi-currency)") or
               "1300 INR" in l for l in budgets)                 # second currency
    assert sum("Expenses:Groceries" in l for l in budgets) == 3  # base + raise + INR

    # Effective budgets precede the final (anchor+buffer) month, and that month has
    # actual transactions — so a calendar-aligned "this month" has both.
    end = gen._add_months(anchor, buffer)
    final_prefix = f"{end.year:04d}-{end.month:02d}"
    budget_dates = [date.fromisoformat(l[:10]) for l in budgets]
    assert min(budget_dates) < date(end.year, end.month, 1)
    assert any(l.startswith(final_prefix) and ' * "' in l for l in text.splitlines())


def test_shift_preserves_pad_before_balance():
    """The day-based shift must never collapse the pad(30th)/balance(31st) pair
    onto the same day (a month-shift-with-clamp bug). Proven by a clean parse
    across several anchors."""
    for anchor in (date(2026, 2, 1), date(2026, 8, 1), date(2027, 1, 1)):
        text = gen.generate(anchor_month=anchor, buffer_months=2)
        _, errors, _ = loader.load_string(text)
        assert errors == [], f"parse errors at anchor {anchor}: {errors}"


# --- Priced investment holdings (Layer 2, Slice 0) ---

def test_txn_plain_and_total_price_shapes_are_byte_identical():
    """Extending txn() with the new "COST" arities must not perturb the existing
    plain and @@ total-price shapes (they anchor the demo-snapshot invariants)."""
    plain = gen.txn(date(2024, 1, 1), "P", "n", [
        ("Assets:A", -12.5, "USD"),
        ("Expenses:B", 12.5, "USD"),
    ])
    assert "  Assets:A                                                -12.50 USD" in plain
    assert plain.count(" {") == 0 and " @@ " not in plain
    total = gen.txn(date(2024, 1, 1), "P", "n", [
        ("Assets:X", 1000.0, "INR", 12.0, "USD"),
    ])
    assert total.strip().endswith("1000.00 INR @@ 12.00 USD")


def test_commodity_directives_carry_asset_class_and_fetch_symbol():
    text = gen.generate(anchor_month=date(2026, 6, 1), buffer_months=2)
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    from beancount.core import data
    commodities = {e.currency: e for e in entries if isinstance(e, data.Commodity)}
    for sym in ("VOO", "VTI", "AAPL", "VMFXX"):
        assert sym in commodities, f"missing commodity directive: {sym}"
    assert commodities["VOO"].meta.get("asset-class") == "etf"
    assert commodities["AAPL"].meta.get("asset-class") == "stock"
    assert commodities["VMFXX"].meta.get("asset-class") == "money-market"
    assert commodities["VOO"].meta.get("fetch_symbol") == "VOO"


def test_ledger_has_cost_lots_dividends_and_two_sales():
    text = gen.generate(anchor_month=date(2026, 6, 1), buffer_months=2)
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    from beancount.core import data
    txns = [e for e in entries if isinstance(e, data.Transaction)]

    cost_lots = [t for t in txns if any(p.cost for p in t.postings)]
    assert len(cost_lots) >= 10, "expected several cost-lot buys (ETF/stock + MMF)"

    # Exactly the two designed sales (reducing leg carries cost AND price).
    # errors == [] already proves every sale balances (Beancount validates on
    # load); here we assert the structural shape Slice 3 will key off.
    sales = [t for t in txns if any(p.cost and p.price for p in t.postings)]
    assert len(sales) == 2
    for s in sales:
        assert any(p.account.startswith("Income:CapitalGains:") for p in s.postings)

    # Dividends booked to Income:Dividends:<SYM> (ETF/stock + reinvested MMF).
    div_accts = {
        p.account for t in txns for p in t.postings
        if p.account.startswith("Income:Dividends:")
    }
    assert {"Income:Dividends:VOO", "Income:Dividends:VTI",
            "Income:Dividends:AAPL", "Income:Dividends:VMFXX"} <= div_accts


def test_one_long_term_and_one_short_term_sale():
    """The sold lot's acquisition date vs the sale date must give one >1yr
    (long-term) and one <1yr (short-term) holding, so Slice 3's split has both."""
    text = gen.generate(anchor_month=date(2026, 6, 1), buffer_months=2)
    entries, _, _ = loader.load_string(text)
    from beancount.core import data
    holding_days = []
    for t in entries:
        if not isinstance(t, data.Transaction):
            continue
        for p in t.postings:
            if p.cost and p.price:  # a reducing (sale) leg
                holding_days.append((t.date - p.cost.date).days)
    assert len(holding_days) == 2
    assert any(d > 365 for d in holding_days), "expected a long-term sale"
    assert any(d < 365 for d in holding_days), "expected a short-term sale"


def test_price_sidecar_parses_is_not_included_and_is_date_aligned():
    anchor, buffer = date(2026, 6, 1), 2
    prices = gen.generate_prices(anchor_month=anchor, buffer_months=buffer)
    # Deterministic and parseable on its own.
    assert prices == gen.generate_prices(anchor_month=anchor, buffer_months=buffer)
    entries, errors, _ = loader.load_string(prices)
    assert errors == []
    from beancount.core import data
    price_entries = [e for e in entries if isinstance(e, data.Price)]
    assert len(price_entries) == sum(1 for l in prices.splitlines() if " price " in l)
    assert {e.currency for e in price_entries} == {
        "VOO", "VTI", "AAPL", "VMFXX", "IBOND2020", "IBOND2022", "IBOND2024",
    }
    # Money-market fund is flat 1.00.
    assert all(e.amount.number == 1 for e in price_entries if e.currency == "VMFXX")

    # The sidecar is a *separate* file — the ledger must not `include` it.
    ledger = gen.generate(anchor_month=anchor, buffer_months=buffer)
    assert "prices.beancount" not in ledger
    assert " price " not in ledger  # the demo keeps all prices in the sidecar

    # Same shift as the ledger: the sidecar's last price lands in the same
    # anchor+buffer month as the ledger's last transaction.
    end = gen._add_months(anchor, buffer)
    last_price = max(e.date for e in price_entries)
    assert (last_price.year, last_price.month) == (end.year, end.month)


def test_ibonds_are_commodity_per_issue_with_accrual():
    """I-Bonds are modelled commodity-per-issue (asset-class 'i-bond' + issue_date),
    and the shipped sidecar carries their ibonds-computed accrued value (> face)."""
    from beancount.core import data
    text = gen.generate(anchor_month=date(2026, 6, 1), buffer_months=2)
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    comms = {e.currency: e for e in entries if isinstance(e, data.Commodity)}
    for code in ("IBOND2020", "IBOND2022", "IBOND2024"):
        assert code in comms, f"missing I-Bond commodity {code}"
        assert comms[code].meta.get("asset-class") == "i-bond"
        assert comms[code].meta.get("issue_date")
        assert comms[code].meta.get("denomination")

    prices = gen.generate_prices(anchor_month=date(2026, 6, 1), buffer_months=2)
    pentries, perrors, _ = loader.load_string(prices)
    assert perrors == []
    ibond_prices = [e for e in pentries if isinstance(e, data.Price) and e.currency.startswith("IBOND")]
    assert ibond_prices
    assert max(e.amount.number for e in ibond_prices) > 1  # accrued above face value


def test_ensure_seed_ledger_writes_lf_sidecar_next_to_ledger(tmp_path):
    out = gen.ensure_seed_ledger(out=tmp_path / "fake.beancount", anchor_month=date(2026, 5, 1))
    sidecar = out.parent / "fake.prices.beancount"
    assert sidecar.exists()
    assert b"\r" not in sidecar.read_bytes()  # LF on every platform
