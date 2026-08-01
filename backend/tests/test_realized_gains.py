"""Realized gains — structural detection + cost_date export (Layer 2, Slice 3).

Specification (dev-docs/investment-dashboards.md §4.4, valuations.md §9):
realized gains are derived STRUCTURALLY from a reducing posting (units < 0 with
both cost_amount and price_amount) — no dependence on an Income:CapitalGains
account name — and the short/long-term split uses the sold lot's acquisition date,
newly exported as the postings.cost_date column. The multi-leg sale (cost + price
+ gain) must balance.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked
from app.services.sqlite_exporter import SQLiteExporter

_DASHBOARDS = "resources/seed_config/recipes/dashboards"

# VOO: bought 2020, sold 2023 → long term (gain 120).
# AAPL: bought 2024-01, sold 2024-03 → short term (gain 100).
# The reducing legs are the trickiest multi-leg transactions (cost+price+gain);
# a clean parse (errors == []) proves they balance.
_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 commodity VOO
  asset-class: "etf"
2020-01-01 commodity AAPL
  asset-class: "stock"

2020-01-01 open Assets:Broker USD,VOO,AAPL
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:CapitalGains:VOO USD
2020-01-01 open Income:CapitalGains:AAPL USD

2020-01-01 * "buy VOO"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD

2024-01-01 * "buy AAPL"
  Assets:Broker  10 AAPL {150.00 USD}
  Assets:Cash  -1500.00 USD

2023-06-01 * "sell VOO (long term)"
  Assets:Broker  -4 VOO {100.00 USD} @ 130.00 USD
  Assets:Cash    520.00 USD
  Income:CapitalGains:VOO  -120.00 USD

2024-03-01 * "sell AAPL (short term)"
  Assets:Broker  -5 AAPL {150.00 USD} @ 170.00 USD
  Assets:Cash    850.00 USD
  Income:CapitalGains:AAPL  -100.00 USD
"""


@pytest.fixture(scope="module")
def mirror(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("realized")
    ledger = tmp / "main.beancount"
    ledger.write_text(_LEDGER)
    db = tmp / "ledger.db"
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == [], f"multi-leg sale must balance: {errors}"   # balance regression
    SQLiteExporter(str(db)).export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return db


def test_export_emits_cost_date_on_sale_legs(mirror):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT currency, cost_date FROM postings "
        "WHERE price_amount IS NOT NULL AND cost_amount IS NOT NULL "
        "AND CAST(amount AS REAL) < 0 ORDER BY currency"
    ).fetchall()
    con.close()
    got = {r["currency"]: r["cost_date"] for r in rows}
    assert got == {"AAPL": "2024-01-01", "VOO": "2020-01-01"}   # the lots' acquisition dates


def _dashboard_query(widget_id: str) -> str:
    d = json.loads(Path(f"{_DASHBOARDS}/investment-realized-gains.json").read_text())
    w = next(w for w in d["widgets"] if w["id"] == widget_id)
    return next(s["query"] for s in w["steps"] if s.get("kind") == "query")


def test_detail_query_gain_and_term_exact(mirror):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    rows = con.execute(_dashboard_query("detail"), {"currency": "USD"}).fetchall()
    con.close()
    by_holding = {r["holding"]: r for r in rows}
    assert round(by_holding["VOO"]["gain"], 2) == 120.00
    assert by_holding["VOO"]["term"] == "Long"
    assert round(by_holding["AAPL"]["gain"], 2) == 100.00
    assert by_holding["AAPL"]["term"] == "Short"


def test_by_year_splits_short_and_long(mirror):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    rows = {r["year"]: r for r in con.execute(_dashboard_query("by-year"), {"currency": "USD"}).fetchall()}
    con.close()
    assert round(rows["2023"]["long_term"], 2) == 120.00
    assert round(rows["2023"]["short_term"], 2) == 0.00
    assert round(rows["2024"]["short_term"], 2) == 100.00


def test_total_kpi_consolidates_all_sales(mirror):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    rows = con.execute(_dashboard_query("kpi-total")).fetchall()
    con.close()
    assert round(rows[0]["amount"], 2) == 220.00   # 120 + 100, across accounts
