"""Investment seed dashboards — their embedded SQL must actually run.

Schema validation (test_seed_dashboards) checks the recipe *shape*; it does not
execute the queries. This runs every `query` step of the investment dashboards
(and the holdings-folded financial-overview) against a real mirror so a SQL typo
or a wrong column name is caught here rather than at render time.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter

_DASHBOARDS = "resources/seed_config/recipes/dashboards"

# A minimal but complete investment ledger: a priced holding with lots + a
# dividend + cash, so every investment query returns something to shape.
_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 commodity VOO
  asset-class: "etf"

2020-01-01 open Assets:Broker USD,VOO
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Dividends:VOO USD

2021-01-10 * "buy"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD

2021-03-10 * "div"
  Assets:Cash   12.00 USD
  Income:Dividends:VOO -12.00 USD
"""
_SIDECAR = "2021-06-01 price VOO 130.00 USD\n"


@pytest.fixture(scope="module")
def mirror(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("inv_dash")
    ledger = tmp / "main.beancount"
    ledger.write_text(_LEDGER)
    sidecar_path(ledger).write_text(_SIDECAR)
    db = tmp / "ledger.db"
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == []
    SQLiteExporter(str(db)).export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return db


def _query_steps(dashboard: str):
    d = json.loads(Path(f"{_DASHBOARDS}/{dashboard}.json").read_text())
    steps = list(d.get("steps", []))
    for w in d["widgets"]:
        steps += w.get("steps", [])
    return [(s["id"], s["query"]) for s in steps if s.get("kind") == "query"]


@pytest.mark.parametrize("dashboard", [
    "financial-overview", "investment-overview", "investment-holdings",
    "investment-income", "investment-realized-gains",
])
def test_all_query_steps_execute(mirror, dashboard):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    params = {"currency": "USD", "holding": "VOO"}
    steps = _query_steps(dashboard)
    assert steps, f"{dashboard} has no query steps"
    for step_id, sql in steps:
        try:
            con.execute(sql, params).fetchall()
        except sqlite3.Error as e:
            pytest.fail(f"{dashboard}/{step_id} SQL failed: {e}\n{sql}")
    con.close()


def test_net_worth_includes_holdings_market_value(mirror):
    """The folded net-worth query must add the holding's market value (10 × 130 =
    1300) to the USD cash, proving Slice 1's fold actually lands."""
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    sql = next(q for i, q in _query_steps("financial-overview") if i == "main")
    rows = {r["currency"]: r["amount"] for r in con.execute(sql).fetchall()}
    con.close()
    # Cash after buy+div = -1000 + 12 = -988; + VOO market 1300 → 312.
    assert round(rows["USD"], 2) == 312.00


def test_holdings_table_market_value_and_weight(mirror):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    sql = next(q for i, q in _query_steps("investment-holdings") if i == "main")
    rows = con.execute(sql, {"currency": "USD"}).fetchall()
    con.close()
    assert len(rows) == 1
    voo = rows[0]
    assert voo["holding"] == "VOO"
    assert round(voo["market_value"], 2) == 1300.00   # 10 × 130
    assert round(voo["cost_basis"], 2) == 1000.00
    assert round(voo["unrealized"], 2) == 300.00
    assert round(voo["weight"], 4) == 1.0             # only holding → 100%
