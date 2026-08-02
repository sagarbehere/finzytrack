"""I-Bond valuator (Layer 2, Slice 5).

Specification (dev-docs/valuations.md, investment-dashboards.md §1):
I-Bonds are modelled commodity-per-issue (units = face dollars @ 1.00). The
opt-in valuator (asset-class 'i-bond' + issue_date) computes accrued value
formulaically via the ibonds library and writes it as an ordinary price
(per unit = value / denomination), so units × price values the holding. It
degrades gracefully when the marker/rate-table can't cover a date, and never
touches a commodity without an issue_date.
"""

import datetime
from datetime import date
from decimal import Decimal

import pytest
from ibonds import IBond

from app.core.backup_manager import BackupManager
from app.core.ledger_initializer import LedgerInitializer
from app.core.ledger_manager import LedgerManager
from app.core.ledger_loader import load_ledger_checked
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.services.price_fetcher import PriceFetcher


# IBONDX: a proper I-Bond issue. NOISSUE: marked i-bond but no issue_date → opt-out.
_LEDGER = """\
option "operating_currency" "USD"

2015-12-31 commodity IBONDX
  asset-class: "i-bond"
  issue_date: "2020-04-15"
  denomination: "10000"
2015-12-31 commodity NOISSUE
  asset-class: "i-bond"

2020-01-01 open Assets:Broker USD,IBONDX,NOISSUE
2020-01-01 open Assets:Cash USD

2020-04-15 * "buy i-bond"
  Assets:Broker  10000 IBONDX {1.00 USD}
  Assets:Cash  -10000.00 USD
2020-04-15 * "buy unmarked i-bond"
  Assets:Broker  5000 NOISSUE {1.00 USD}
  Assets:Cash  -5000.00 USD
"""


@pytest.fixture
def wired(tmp_path):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(_LEDGER)
    db = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db))
    backup_manager = BackupManager(backup_dir=tmp_path / "backups", retention_count=3)
    initializer = LedgerInitializer(ledger_file=str(ledger), backup_manager=backup_manager)
    lm = LedgerManager(
        ledger_file=str(ledger), backup_manager=backup_manager,
        ledger_initializer=initializer, sqlite_exporter=exporter,
    )
    reader = SqliteReader(sqlite_path=db, ledger_file=ledger, exporter=exporter, write_lock=None)
    entries, errors, options = load_ledger_checked(str(ledger))
    assert errors == []
    exporter.export_full_sync(entries, errors, options, ledger_file=str(ledger))
    return reader, lm


def test_issue_ym_normalisation():
    assert PriceFetcher._issue_ym("04/2020") == "04/2020"
    assert PriceFetcher._issue_ym("2020-04-15") == "04/2020"
    assert PriceFetcher._issue_ym("2020-04") == "04/2020"
    assert PriceFetcher._issue_ym(None) is None


def test_accrued_price_matches_ibonds_at_fixed_asof(wired):
    reader, lm = wired
    PriceFetcher(reader, lm, today=date(2024, 4, 1)).fetch_and_persist()

    prices = {r["date"]: r["quote_number"] for r in reader.get_prices(currency="IBONDX")}
    # Per-unit price = ibonds value / denomination, to 4 dp.
    expected = (Decimal(str(IBond("04/2020", 10000).value(date(2024, 4, 1)))) / 10000).quantize(Decimal("0.0001"))
    assert prices["2024-04-01"] == str(expected)
    # units × price reconstructs the full accrued value.
    assert Decimal("10000") * Decimal(prices["2024-04-01"]) == Decimal(str(IBond("04/2020", 10000).value(date(2024, 4, 1))))


def test_opt_out_when_no_issue_date(wired):
    reader, lm = wired
    res = PriceFetcher(reader, lm, today=date(2024, 4, 1)).fetch_and_persist()
    assert "NOISSUE" in res["skipped"]
    assert reader.get_prices(currency="NOISSUE") == []


def test_graceful_degradation_past_rate_table(wired):
    """A window straddling the rate table's coverage yields prices up to what the
    table supports and simply skips the rest — no error, no fabricated values."""
    reader, lm = wired
    # 'today' well past the shipped table; the 5y lookback window (from ~2025)
    # starts inside coverage and runs past its end.
    PriceFetcher(reader, lm, today=date(2030, 6, 1)).fetch_and_persist()
    dates = [date.fromisoformat(r["date"]) for r in reader.get_prices(currency="IBONDX")]
    assert dates                                   # coverable dates were produced
    assert max(dates) < date(2030, 6, 1)           # …but not fabricated past the table


def test_no_prices_before_issue(wired):
    reader, lm = wired
    PriceFetcher(reader, lm, today=date(2024, 4, 1)).fetch_and_persist()
    dates = [date.fromisoformat(r["date"]) for r in reader.get_prices(currency="IBONDX")]
    assert min(dates) >= date(2020, 4, 1)          # nothing before the issue month
