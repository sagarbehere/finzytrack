"""Financial-overview valuation: cost fallback + closed-account exclusion.

Specification (dev-docs/valuations.md §5): a holding with no current price is
valued at its cost basis (degraded) — never dropped to $0. And the Assets
Breakdown pie hides closed accounts (and sub-cent float-epsilon residuals) so a
closed CD that nets to zero doesn't linger as a $0.00 slice.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter

_DASHBOARDS = "resources/seed_config/recipes/dashboards"

# VOO priced (130); ACME never priced -> must fall back to cost (200), not vanish.
# Assets:Old is funded then emptied then closed -> a closed, net-zero account.
_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 commodity VOO
  asset-class: "etf"

2020-01-01 open Assets:Broker USD,VOO,ACME
2020-01-01 open Assets:Cash USD
2020-01-01 open Assets:Old USD

2023-01-01 * "buy VOO"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD
2023-01-01 * "buy ACME (never priced)"
  Assets:Broker  4 ACME {50.00 USD}
  Assets:Cash   -200.00 USD

2023-06-01 * "fund old"
  Assets:Old   500.00 USD
  Assets:Cash -500.00 USD
2023-07-01 * "empty old"
  Assets:Old  -500.00 USD
  Assets:Cash  500.00 USD
2023-07-02 close Assets:Old
"""
_SIDECAR = "2023-06-01 price VOO 130.00 USD\n"


@pytest.fixture(scope="module")
def mirror(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("valfb")
    ledger = tmp / "main.beancount"
    ledger.write_text(_LEDGER)
    sidecar_path(ledger).write_text(_SIDECAR)
    db = tmp / "ledger.db"
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == []
    SQLiteExporter(str(db)).export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return db


def _q(dashboard: str, widget_id: str) -> str:
    d = json.loads(Path(f"{_DASHBOARDS}/{dashboard}.json").read_text())
    w = next(w for w in d["widgets"] if w["id"] == widget_id)
    return next(s["query"] for s in w["steps"] if s.get("kind") == "query")


def test_unpriced_holding_valued_at_cost_not_dropped(mirror):
    con = sqlite3.connect(str(mirror)); con.row_factory = sqlite3.Row
    rows = {r["name"]: r["value"] for r in con.execute(_q("financial-overview", "assets-pie"), {"currency": "USD"}).fetchall()}
    con.close()
    # Broker = priced VOO (10×130 = 1300) + cost-fallback ACME (4×50 = 200) = 1500.
    assert round(rows["Broker"], 2) == 1500.00


def test_closed_account_excluded_from_pie(mirror):
    con = sqlite3.connect(str(mirror)); con.row_factory = sqlite3.Row
    names = {r["name"] for r in con.execute(_q("financial-overview", "assets-pie"), {"currency": "USD"}).fetchall()}
    con.close()
    assert "Old" not in names  # closed account, even though it nets to zero


def test_net_worth_includes_unpriced_holding_at_cost(mirror):
    con = sqlite3.connect(str(mirror)); con.row_factory = sqlite3.Row
    rows = {r["currency"]: r["amount"] for r in con.execute(_q("financial-overview", "net-worth")).fetchall()}
    con.close()
    # Cash -1200 (spent on VOO+ACME) + VOO market 1300 + ACME cost 200 = 300.
    assert round(rows["USD"], 2) == 300.00
