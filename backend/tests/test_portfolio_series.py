"""portfolio_series compute function (Layer 2, Slice 1).

Specification (dev-docs/valuations.md §5, investment-dashboards.md §4.1):
value holdings as-of each sampled date at the latest price on-or-before it
(never a future price), report per quote currency the market value and the cost
basis of units still held, fall back to cost + flag `degraded` when a held
commodity has no price yet, and never convert across currencies. Money in/out as
decimal strings.

Tests assert exact values against a hand-computed fixture.
"""

import asyncio
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.compute.functions.portfolio_series import PortfolioSeriesFunction

# VOO: buy 10 @100 (2021-01), sell 3 @cost100/price130 (2022-01) → 7 held.
# PVT: buy 4 @50 (2021-06), *never priced* → always degraded, valued at cost.
_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 commodity VOO
  asset-class: "etf"
2020-01-01 commodity PVT
  asset-class: "stock"

2020-01-01 open Assets:Broker USD,VOO,PVT
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:CapitalGains:VOO USD

2021-01-10 * "buy VOO"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD

2021-06-10 * "buy PVT (never priced)"
  Assets:Broker  4 PVT {50.00 USD}
  Assets:Cash   -200.00 USD

2022-01-10 * "sell 3 VOO"
  Assets:Broker  -3 VOO {100.00 USD} @ 130.00 USD
  Assets:Cash    390.00 USD
  Income:CapitalGains:VOO  -90.00 USD
"""

_SIDECAR = """\
2021-02-01 price VOO 120.00 USD
2021-12-01 price VOO 130.00 USD
2022-06-01 price VOO 150.00 USD
"""

# A ledger with no non-currency holdings at all.
_CASH_ONLY = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary USD
2021-01-10 * "pay"
  Assets:Cash   100.00 USD
  Income:Salary -100.00 USD
"""


def _reader(tmp_path: Path, ledger_text: str, sidecar_text: str | None) -> SqliteReader:
    ledger = tmp_path / "main.beancount"
    ledger.write_text(ledger_text)
    if sidecar_text is not None:
        sidecar_path(ledger).write_text(sidecar_text)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == []
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return reader


def _run(reader, **args):
    fn = PortfolioSeriesFunction(reader)
    rows = asyncio.run(fn.execute(**args))
    return {(r["date"], r["group"]): r for r in rows}


def test_as_of_before_any_price_falls_back_to_cost_and_degrades(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    by = _run(r, **{"from": "2021-01-01", "to": "2021-01-31", "scope": "overall"})
    row = by[("2021-01-31", "Total")]
    # 10 VOO held, but the first price is 2021-02-01 → no as-of price → cost.
    assert row["market_value"] == "1000.00"
    assert row["cost_basis"] == "1000.00"
    assert row["degraded"] is True


def test_priced_point_uses_latest_price_on_or_before(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    by = _run(r, **{"from": "2021-06-01", "to": "2021-06-30", "scope": "holding"})
    voo = by[("2021-06-30", "VOO")]
    # Latest price ≤ Jun 30 is the Feb 120.00 (Dec price is later).
    assert voo["market_value"] == "1200.00"   # 10 × 120
    assert voo["cost_basis"] == "1000.00"
    assert voo["degraded"] is False
    pvt = by[("2021-06-30", "PVT")]
    assert pvt["market_value"] == "200.00" == pvt["cost_basis"]  # never priced
    assert pvt["degraded"] is True


def test_sale_reduces_units_in_later_market_value(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    by = _run(r, **{"from": "2022-06-01", "to": "2022-06-30", "scope": "holding"})
    voo = by[("2022-06-30", "VOO")]
    # 7 held after the sale; latest price 150.00.
    assert voo["market_value"] == "1050.00"   # 7 × 150
    assert voo["cost_basis"] == "700.00"       # 7 × 100


def test_overall_degrades_if_any_holding_unpriced(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    by = _run(r, **{"from": "2022-06-01", "to": "2022-06-30", "scope": "overall"})
    total = by[("2022-06-30", "Total")]
    assert total["market_value"] == "1250.00"  # VOO 1050 + PVT 200 (cost fallback)
    assert total["cost_basis"] == "900.00"     # VOO 700 + PVT 200
    assert total["degraded"] is True           # PVT drags the whole point degraded


def test_asset_class_scope_groups_by_class(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    by = _run(r, **{"from": "2022-06-01", "to": "2022-06-30", "scope": "asset-class"})
    assert by[("2022-06-30", "etf")]["market_value"] == "1050.00"
    assert by[("2022-06-30", "stock")]["market_value"] == "200.00"


def test_currency_filter_excludes_other_quote_currencies(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    fn = PortfolioSeriesFunction(r)
    assert asyncio.run(fn.execute(**{"to": "2022-06-30", "currency": "EUR"})) == []
    usd = asyncio.run(fn.execute(**{"to": "2022-06-30", "currency": "USD"}))
    assert usd and all(row["currency"] == "USD" for row in usd)


def test_no_holdings_returns_empty(tmp_path):
    r = _reader(tmp_path, _CASH_ONLY, None)
    fn = PortfolioSeriesFunction(r)
    assert asyncio.run(fn.execute(**{"to": "2022-01-01"})) == []


def test_last_sample_is_exactly_the_to_date(tmp_path):
    r = _reader(tmp_path, _LEDGER, _SIDECAR)
    fn = PortfolioSeriesFunction(r)
    rows = asyncio.run(fn.execute(**{"from": "2021-01-01", "to": "2021-03-15", "cadence": "monthly"}))
    assert rows[-1]["date"] == "2021-03-15"  # the 'current' point, not just month-ends


def test_registered_and_discoverable(tmp_path):
    """portfolio_series must be in the registry so /api/compute and the AI's
    get_compute_functions both see it."""
    from app.api.routers.compute import build_registry
    r = _reader(tmp_path, _CASH_ONLY, None)
    reg = build_registry(r)
    assert "portfolio_series" in reg.names()
    schema = {s["name"]: s for s in reg.get_schemas()}["portfolio_series"]
    assert schema["description"] and schema["output_shape"]
