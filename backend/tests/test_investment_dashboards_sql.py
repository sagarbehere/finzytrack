"""Investment seed dashboards — their embedded SQL must return the right numbers.

Division of labour with the other dashboard tests:

  - `test_seed_dashboards` validates every dashboard's *shape* and dry-runs its
    SQL (`SELECT * FROM (q) LIMIT 0`), driven by the dashboards directory — so
    it covers new dashboards automatically and cannot go stale.
  - This file goes further for the investment set: it builds a mirror holding a
    priced holding with lots, a dividend and cash, then asserts the *values*
    those queries produce (the net-worth fold, market value, weight). A query
    that runs but computes the wrong thing is only caught here.

The dashboard list below is therefore deliberately explicit — it names the ones
whose numbers we assert, not "all of them". It went stale once when the
factual-reframe restructure (dev-docs/investment-dashboards.md, Update
2026-08-04) folded investment-overview/-income/-realized-gains into
investment-holdings, cash-deposits and returns-income; a missing file fails
loudly here, which is the intended behaviour.
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
    "financial-overview", "investment-holdings", "cash-deposits", "returns-income",
])
def test_all_query_steps_execute(mirror, dashboard):
    con = sqlite3.connect(str(mirror))
    con.row_factory = sqlite3.Row
    params = {"currency": "USD", "holding": "VOO", "asOf": "2022-01-01"}
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
