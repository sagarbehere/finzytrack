"""The mirror's ``latest_prices`` table.

Specification: one row per (base_currency, quote_currency) pair holding the
pair's newest price from ``prices`` (main ledger + price sidecar). When a pair has
several prices on its newest date, the last one declared wins — the same rule
Beancount's price map applies. Recipes join this table instead of computing
"latest price" with a correlated MAX(date) subquery.
"""

import sqlite3
from pathlib import Path

from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter


_MAIN = """\
option "operating_currency" "USD"

2020-01-01 open Assets:Broker USD,EUR,VOO,VTI
2020-01-01 open Assets:Cash USD

2020-02-01 * "Buy"
  Assets:Broker  2 VOO {280.00 USD}
  Assets:Cash   -560.00 USD

2020-06-01 price VOO 300.00 USD
2020-07-01 price VOO 310.00 USD
2020-07-01 price VOO 315.00 USD
2020-05-01 price VOO 250.00 EUR
"""

_SIDECAR = """\
2021-01-15 price VTI 200.00 USD
2021-02-15 price VTI 220.00 USD
"""


def _latest(tmp_path: Path):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(_MAIN)
    sidecar_path(ledger).write_text(_SIDECAR)
    db = tmp_path / "ledger.db"
    entries, errors, options = load_ledger_checked(str(ledger))
    SQLiteExporter(str(db)).export_full_sync(entries, errors, options, ledger_file=str(ledger))
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT base_currency, quote_currency, date, quote_number "
            "FROM latest_prices ORDER BY base_currency, quote_currency"
        ).fetchall()
    finally:
        con.close()


def test_one_newest_row_per_pair(tmp_path):
    assert _latest(tmp_path) == [
        ("VOO", "EUR", "2020-05-01", "250.00"),
        ("VOO", "USD", "2020-07-01", "315.00"),
        ("VTI", "USD", "2021-02-15", "220.00"),
    ]


def test_same_date_tie_keeps_last_declared(tmp_path):
    voo_usd = [r for r in _latest(tmp_path) if r[:2] == ("VOO", "USD")]
    assert voo_usd == [("VOO", "USD", "2020-07-01", "315.00")]
