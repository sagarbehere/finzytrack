"""portfolio_returns compute function + XIRR solver (Layer 2, Slice 2).

Specification (dev-docs/investment-dashboards.md §5, valuations.md §5):
money-weighted XIRR at portfolio/asset-class from the external cash flows (buys
negative, sales & cash dividends positive, DRIP zero) plus terminal market value;
graceful null on degenerate series; simple gain alongside; money + rate as
decimal strings.
"""

import asyncio
from datetime import date
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.compute.functions.portfolio_returns import PortfolioReturnsFunction, _xirr


# ── The solver, directly ──────────────────────────────────────────────────────

def test_xirr_exact_one_year_double_digit():
    d0 = date(2021, 1, 1)
    r = _xirr([(d0, -1000.0), (date(2022, 1, 1), 1300.0)])
    assert r is not None and abs(r - 0.30) < 1e-4   # -1000 → +1300 in 1yr = 30%


def test_xirr_null_on_single_flow():
    assert _xirr([(date(2021, 1, 1), -1000.0)]) is None


def test_xirr_null_when_all_same_sign():
    assert _xirr([(date(2021, 1, 1), 1000.0), (date(2022, 1, 1), 1300.0)]) is None
    assert _xirr([(date(2021, 1, 1), -1000.0), (date(2022, 1, 1), -300.0)]) is None


# ── End-to-end via a mirror ───────────────────────────────────────────────────

# One cash-funded buy, priced a year later — a clean hand-computable 30% XIRR.
_LEDGER_30PCT = """\
option "operating_currency" "USD"
2020-01-01 commodity VOO
  asset-class: "etf"
2020-01-01 open Assets:Broker USD,VOO
2020-01-01 open Assets:Cash USD
2021-01-01 * "buy"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD
"""
_SIDECAR_30PCT = "2022-01-01 price VOO 130.00 USD\n"

# A DRIP-only, unpriced holding: no cash flow ever, terminal falls back to cost →
# a single flow → XIRR must be null (not a fabricated number).
_LEDGER_DRIP = """\
option "operating_currency" "USD"
2020-01-01 commodity MMF
  asset-class: "money-market"
2020-01-01 open Assets:Broker USD,MMF
2020-01-01 open Income:Dividends:MMF USD
2021-02-01 * "drip"
  Assets:Broker  100 MMF {1.00 USD}
  Income:Dividends:MMF -100.00 USD
"""


def _reader(tmp_path, ledger_text, sidecar_text):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(ledger_text)
    if sidecar_text:
        sidecar_path(ledger).write_text(sidecar_text)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == []
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return reader


def test_end_to_end_xirr_and_simple_gain(tmp_path):
    r = _reader(tmp_path, _LEDGER_30PCT, _SIDECAR_30PCT)
    rows = asyncio.run(PortfolioReturnsFunction(r).execute(**{"to": "2022-01-01", "scope": "portfolio"}))
    assert len(rows) == 1
    row = rows[0]
    assert row["group"] == "Portfolio" and row["currency"] == "USD"
    assert abs(float(row["xirr"]) - 0.30) < 1e-4
    assert row["market_value"] == "1300.00" and row["cost_basis"] == "1000.00"
    assert row["simple_gain"] == "300.00"
    assert abs(float(row["simple_gain_pct"]) - 0.30) < 1e-9


def test_degenerate_series_returns_null_xirr(tmp_path):
    r = _reader(tmp_path, _LEDGER_DRIP, None)
    rows = asyncio.run(PortfolioReturnsFunction(r).execute(**{"to": "2022-06-01", "scope": "portfolio"}))
    assert len(rows) == 1
    assert rows[0]["xirr"] is None            # single flow → no XIRR
    assert rows[0]["simple_gain"] == "0.00"   # unpriced MMF: market == cost


def test_no_holdings_returns_empty(tmp_path):
    r = _reader(tmp_path, "option \"operating_currency\" \"USD\"\n2020-01-01 open Assets:Cash USD\n", None)
    rows = asyncio.run(PortfolioReturnsFunction(r).execute(**{"to": "2022-01-01"}))
    assert rows == []


def test_registered_and_discoverable(tmp_path):
    from app.api.routers.compute import build_registry
    r = _reader(tmp_path, "option \"operating_currency\" \"USD\"\n2020-01-01 open Assets:Cash USD\n", None)
    reg = build_registry(r)
    assert "portfolio_returns" in reg.names()
    schema = {s["name"]: s for s in reg.get_schemas()}["portfolio_returns"]
    assert schema["description"] and schema["output_shape"]
