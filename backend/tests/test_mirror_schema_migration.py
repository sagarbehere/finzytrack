"""Mirror self-heals across a postings *schema* change (regression).

A full export/rebuild must recreate the ``postings`` table, not reuse a stale-
schema one left over from an older version. Before the fix, ``_ensure_postings_
table`` was create-if-absent, so after a column was added (e.g. ``cost_date``)
an existing mirror kept its old table and the rebuild's INSERT supplied more
values than columns — "table postings has N columns but N+1 values were
supplied" — on every launch after an upgrade. The freshness tests never caught
it because they always start from a fresh DB.
"""

import sqlite3
from pathlib import Path

from app.core.ledger_loader import load_ledger_checked
from app.services.sqlite_exporter import SQLiteExporter

_LEDGER = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary USD
2021-03-01 * "pay"
  Assets:Cash   100.00 USD
  Income:Salary -100.00 USD
"""


def test_full_export_recreates_stale_postings_table(tmp_path):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(_LEDGER)
    db = tmp_path / "ledger.db"

    # Simulate an old mirror whose `postings` table predates a column addition.
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE postings (posting_id INTEGER, account TEXT)")  # stale schema
    con.execute("INSERT INTO postings VALUES (1, 'Assets:Old')")
    con.commit()
    con.close()

    entries, errors, options = load_ledger_checked(str(ledger))
    # Must not raise "N columns but N+1 values" — the rebuild recreates the table.
    SQLiteExporter(str(db)).export_full_sync(entries, errors, options, ledger_file=str(ledger))

    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(postings)")}
        assert "cost_date" in cols            # new schema is in place
        assert "Assets:Old" not in {
            r[0] for r in con.execute("SELECT account FROM postings")
        }                                      # stale row is gone, real postings exported
        assert con.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 2
    finally:
        con.close()
