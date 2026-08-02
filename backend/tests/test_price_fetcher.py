"""Price fetcher + sidecar write path + /api/prices/update (Layer 2, Slice 4).

Specification (dev-docs/valuations.md §3/§4, the Slice 4 gotcha):
the fetcher gap-fills prices per holding from a keyless source (mocked here),
skips money-market funds, honours fetch_symbol, and is idempotent; it writes to
the sidecar through a DEDICATED path that never rewrites transaction files, and a
transaction write never clobbers the sidecar; the endpoint round-trips.
"""

import asyncio
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from beancount.core import data
from beancount.core.amount import Amount
from beanprice.source import SourcePrice

from app.core.backup_manager import BackupManager
from app.core.ledger_initializer import LedgerInitializer
from app.core.ledger_manager import LedgerManager
from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader
from app.services.price_fetcher import PriceFetcher


_LEDGER = """\
option "operating_currency" "USD"

2020-01-01 commodity VOO
  asset-class: "etf"
  fetch_symbol: "VOO"
2020-01-01 commodity ACME
  asset-class: "stock"
  fetch_symbol: "ACME.XYZ"
2020-01-01 commodity VMFXX
  asset-class: "money-market"

2020-01-01 open Assets:Broker USD,VOO,ACME,VMFXX
2020-01-01 open Assets:Cash USD

2021-01-10 * "buy VOO"
  Assets:Broker  10 VOO {100.00 USD}
  Assets:Cash  -1000.00 USD
2021-01-10 * "buy ACME"
  Assets:Broker  5 ACME {50.00 USD}
  Assets:Cash  -250.00 USD
2021-01-10 * "buy MMF"
  Assets:Broker  100 VMFXX {1.00 USD}
  Assets:Cash  -100.00 USD
"""


class FakeSource:
    """Offline stand-in for beanprice's Yahoo source."""

    def __init__(self, by_symbol: dict[str, list[tuple[date, str]]]):
        self.by_symbol = by_symbol
        self.calls: list[tuple[str, date, date]] = []

    def get_prices_series(self, ticker, time_begin, time_end):
        self.calls.append((ticker, time_begin.date(), time_end.date()))
        out = []
        for d, price in self.by_symbol.get(ticker, []):
            if time_begin.date() <= d <= time_end.date():
                out.append(SourcePrice(
                    Decimal(price), datetime.combine(d, time.min, tzinfo=timezone.utc), "USD"
                ))
        return out


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
    return ledger, db, reader, lm


def _sidecar_prices(ledger: Path):
    p = sidecar_path(ledger)
    if not p.is_file():
        return []
    entries, _e, _o = load_ledger_checked(str(p))
    return [e for e in entries if isinstance(e, data.Price)]


def test_fetches_priced_holdings_and_skips_money_market(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({
        "VOO": [(date(2021, 2, 1), "120.00"), (date(2021, 2, 2), "121.00")],
        "ACME.XYZ": [(date(2021, 2, 1), "55.00")],
    })
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 2)).fetch_and_persist()

    bases = {p.currency for p in _sidecar_prices(ledger)}
    assert bases == {"VOO", "ACME"}          # VMFXX (money-market) never fetched
    assert "VMFXX" in res["skipped"]
    assert res["added"] == 3
    assert res["as_of"] == "2021-02-02"


def test_uses_fetch_symbol_metadata(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"ACME.XYZ": [(date(2021, 2, 1), "55.00")], "VOO": []})
    PriceFetcher(reader, lm, source=source, today=date(2021, 2, 2)).fetch_and_persist()
    tickers = {c[0] for c in source.calls}
    assert "ACME.XYZ" in tickers             # fetch_symbol, not the code "ACME"


def test_gap_fill_only_requests_dates_after_last_persisted(wired):
    ledger, _, reader, lm = wired
    # Seed one existing VOO price.
    lm.write_price_directives([data.Price({}, date(2021, 3, 15), "VOO", Amount(Decimal("130.00"), "USD"))])
    source = FakeSource({"VOO": [(date(2021, 3, 20), "131.00")], "ACME.XYZ": []})
    PriceFetcher(reader, lm, source=source, today=date(2021, 3, 25)).fetch_and_persist()
    voo_call = next(c for c in source.calls if c[0] == "VOO")
    assert voo_call[1] == date(2021, 3, 16)   # begins the day after the last persisted price


def test_idempotent_second_run_adds_nothing(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"VOO": [(date(2021, 2, 1), "120.00")], "ACME.XYZ": []})
    first = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert first["added"] == 1
    second = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert second["added"] == 0               # nothing new; historical closes immutable


def test_price_write_does_not_touch_transaction_file(wired):
    ledger, _, reader, lm = wired
    before = ledger.read_bytes()
    lm.write_price_directives([data.Price({}, date(2021, 2, 1), "VOO", Amount(Decimal("120.00"), "USD"))])
    assert ledger.read_bytes() == before      # the sidecar write never rewrites the ledger
    assert _sidecar_prices(ledger)            # …but the sidecar got the price


def test_transaction_write_does_not_clobber_sidecar(wired):
    ledger, _, reader, lm = wired
    lm.write_price_directives([data.Price({}, date(2021, 2, 1), "VOO", Amount(Decimal("120.00"), "USD"))])
    sidecar_before = sidecar_path(ledger).read_bytes()
    # A normal transaction write (the _do_write_entries path).
    txn = data.Transaction(
        {"filename": str(ledger)}, date(2021, 4, 1), "*", None, "later", data.EMPTY_SET, data.EMPTY_SET,
        [data.Posting("Assets:Cash", Amount(Decimal("5.00"), "USD"), None, None, None, None),
         data.Posting("Assets:Broker", Amount(Decimal("-5.00"), "USD"), None, None, None, None)],
    )
    lm.append_entries([txn])
    assert sidecar_path(ledger).read_bytes() == sidecar_before   # sidecar not in known_files → untouched


def test_endpoint_round_trips(wired, monkeypatch):
    ledger, db, reader, lm = wired
    # The endpoint uses the real "today", so use a recent date within the
    # first-fetch lookback window.
    recent = date.today()
    source = FakeSource({"VOO": [(recent, "120.00")], "ACME.XYZ": [(recent, "55.00")]})
    # Endpoint constructs its own fetcher; inject the offline source.
    monkeypatch.setattr(PriceFetcher, "_get_source", lambda self: source)

    import json as _json
    from app.api.routers.prices import update_prices
    resp = asyncio.run(update_prices(reader=reader, ledger_manager=lm))

    body = _json.loads(resp.body)
    assert body["success"] is True
    assert body["data"]["added"] == 2 and body["data"]["as_of"] == recent.isoformat()
    # Verify via a read: the prices are now in the mirror.
    voo = reader.get_prices(currency="VOO")
    assert any(r["date"] == recent.isoformat() and r["quote_number"] == "120.00" for r in voo)
