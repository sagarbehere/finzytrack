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

    def __init__(self, by_symbol: dict[str, list[tuple[date, str]]],
                 latest: dict[str, tuple[date, str]] | None = None,
                 currency: dict[str, str] | None = None):
        self.by_symbol = by_symbol
        self.latest = latest or {}
        self.currency = currency or {}          # symbol → quote currency (default USD)
        self.calls: list[tuple[str, date, date]] = []

    def get_prices_series(self, ticker, time_begin, time_end):
        self.calls.append((ticker, time_begin.date(), time_end.date()))
        out = []
        for d, price in self.by_symbol.get(ticker, []):
            if time_begin.date() <= d <= time_end.date():
                out.append(SourcePrice(
                    Decimal(price), datetime.combine(d, time.min, tzinfo=timezone.utc),
                    self.currency.get(ticker, "USD"),
                ))
        return out

    def get_latest_price(self, ticker):
        v = self.latest.get(ticker)
        if v is None:
            return None
        d, price = v
        return SourcePrice(Decimal(price), datetime.combine(d, time.min, tzinfo=timezone.utc),
                           self.currency.get(ticker, "USD"))


def _wire(tmp_path, ledger_text):
    ledger = tmp_path / "main.beancount"
    ledger.write_text(ledger_text)
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


@pytest.fixture
def wired(tmp_path):
    return _wire(tmp_path, _LEDGER)


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


def _voo(ledger: Path) -> list[tuple[date, str]]:
    return sorted((p.date, str(p.amount.number)) for p in _sidecar_prices(ledger) if p.currency == "VOO")


def test_held_fetch_starts_at_and_replaces_the_newest_stored_day(wired):
    """P-Q6: the window starts AT the newest stored day; its value is replaced
    (a mid-day price becomes the close) and later days are added."""
    ledger, _, reader, lm = wired
    lm.write_price_directives([
        data.Price({}, date(2021, 3, 10), "VOO", Amount(Decimal("128.00"), "USD")),
        data.Price({}, date(2021, 3, 15), "VOO", Amount(Decimal("130.00"), "USD")),
    ])
    source = FakeSource({"VOO": [(date(2021, 3, 10), "999.00"), (date(2021, 3, 15), "130.50"),
                                 (date(2021, 3, 20), "131.00")], "ACME.XYZ": []})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 3, 25)).fetch_and_persist()
    voo_call = next(c for c in source.calls if c[0] == "VOO")
    assert voo_call[1:] == (date(2021, 3, 15), date(2021, 3, 25))
    assert (res["added"], res["updated"]) == (1, 1)
    assert _voo(ledger) == [  # older day stays fixed history
        (date(2021, 3, 10), "128.00"), (date(2021, 3, 15), "130.50"), (date(2021, 3, 20), "131.00"),
    ]


def test_second_fetch_on_the_same_day_replaces_todays_price(wired):
    ledger, _, reader, lm = wired
    today = date(2021, 2, 1)
    PriceFetcher(reader, lm, source=FakeSource({"VOO": [(today, "120.00")], "ACME.XYZ": []}),
                 today=today).fetch_and_persist()
    PriceFetcher(reader, lm, source=FakeSource({"VOO": [(today, "121.40")], "ACME.XYZ": []}),
                 today=today).fetch_and_persist()
    assert _voo(ledger) == [(today, "121.40")]
    assert [(r["date"], r["quote_number"]) for r in reader.get_prices(currency="VOO")] == [
        ("2021-02-01", "121.40"),
    ]


def test_hand_price_on_the_newest_day_is_overwritten_by_a_fetch(wired):
    ledger, _, reader, lm = wired
    lm.edit_price_directives([data.Price({}, date(2021, 2, 1), "VOO", Amount(Decimal("1.00"), "USD"))], [])
    PriceFetcher(reader, lm, source=FakeSource({"VOO": [(date(2021, 2, 1), "120.00")], "ACME.XYZ": []}),
                 today=date(2021, 2, 1)).fetch_and_persist()
    assert _voo(ledger) == [(date(2021, 2, 1), "120.00")]


def test_fetch_never_writes_a_key_that_is_in_the_ledger_file(tmp_path):
    """A price in the ledger file on the newest day stands; the fetch neither
    duplicates it into the sidecar nor changes it."""
    ledger_text = _LEDGER + "\n2021-02-01 price VOO 119.00 USD\n"
    ledger, _, reader, lm = _wire(tmp_path, ledger_text)
    source = FakeSource({"VOO": [(date(2021, 2, 1), "120.00"), (date(2021, 2, 2), "121.00")],
                         "ACME.XYZ": []})
    PriceFetcher(reader, lm, source=source, today=date(2021, 2, 2)).fetch_and_persist()
    assert _voo(ledger) == [(date(2021, 2, 2), "121.00")]
    assert [(r["date"], r["quote_number"]) for r in reader.get_prices(currency="VOO")] == [
        ("2021-02-01", "119.00"), ("2021-02-02", "121.00"),
    ]


_SOLD = """\
option "operating_currency" "USD"

2020-01-01 commodity OLD
  asset-class: "stock"
2020-01-01 open Assets:Broker USD,OLD
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Gains USD

2021-01-10 * "buy"
  Assets:Broker  10 OLD {100.00 USD}
  Assets:Cash  -1000.00 USD
2021-03-01 * "sell"
  Assets:Broker  -10 OLD {100.00 USD} @ 110.00 USD
  Assets:Cash  1100.00 USD
  Income:Gains  -100.00 USD
"""


def test_sold_out_holding_does_not_refetch_its_last_price(tmp_path):
    ledger, _, reader, lm = _wire(tmp_path, _SOLD)
    lm.write_price_directives([data.Price({}, date(2021, 3, 1), "OLD", Amount(Decimal("110.00"), "USD"))])
    source = FakeSource({"OLD": [(date(2021, 3, 1), "999.00")]})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 6, 1)).fetch_and_persist()
    assert source.calls == []                 # window (Mar 2 → Mar 1) is empty
    assert res["added"] == 0
    # Nothing fetched: the result describes the price file as it stands.
    assert (res["symbols"], res["failed"], res["total"], res["as_of"]) == ([], [], 1, "2021-03-01")
    assert [(r["date"], r["quote_number"]) for r in reader.get_prices(currency="OLD")] == [
        ("2021-03-01", "110.00"),
    ]


_MONTHLY = """\
option "operating_currency" "USD"

2020-01-01 commodity BND
  asset-class: "bond"
2020-01-01 open Assets:Broker USD,BND
2020-01-01 open Assets:Cash USD

2021-01-10 * "buy"
  Assets:Broker  2 BND {1000.00 USD}
  Assets:Cash  -2000.00 USD
"""


def test_monthly_refresh_keeps_one_point_per_month(tmp_path):
    """A newer point in the newest month replaces the older one stored in that
    month; earlier months are untouched."""
    ledger, _, reader, lm = _wire(tmp_path, _MONTHLY)
    lm.write_price_directives([
        data.Price({}, date(2021, 9, 30), "BND", Amount(Decimal("990.00"), "USD")),
        data.Price({}, date(2021, 10, 5), "BND", Amount(Decimal("995.00"), "USD")),
    ])
    source = FakeSource({"BND": [(date(2021, 10, 5), "996.00"), (date(2021, 10, 12), "997.00"),
                                 (date(2021, 10, 20), "998.00")]})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 10, 20)).fetch_and_persist()
    assert source.calls == [("BND", date(2021, 10, 5), date(2021, 10, 20))]
    assert sorted((p.date, str(p.amount.number)) for p in _sidecar_prices(ledger)) == [
        (date(2021, 9, 30), "990.00"), (date(2021, 10, 20), "998.00"),
    ]
    assert (res["added"], res["total"]) == (1, 2)
    assert [r["date"] for r in reader.get_prices(currency="BND")] == ["2021-09-30", "2021-10-20"]


def test_falls_back_to_latest_price_when_series_is_empty(wired):
    """Yahoo's chart endpoint returns no series for some ranges even when the
    latest quote works — the fetcher must fall back to get_latest_price so the
    holding still gets today's price (and record what still returned nothing)."""
    ledger, _, reader, lm = wired
    source = FakeSource(
        {"VOO": [], "ACME.XYZ": []},                       # no historical series for anything
        latest={"VOO": (date(2021, 2, 1), "120.00")},      # …but VOO has a latest quote
    )
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    voo = reader.get_prices(currency="VOO")
    assert any(r["date"] == "2021-02-01" and r["quote_number"] == "120.00" for r in voo)
    assert res["added"] == 1
    assert any(f.startswith("ACME") for f in res["failed"])  # attempted, nothing → reported with reason


def test_idempotent_second_run_adds_nothing(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"VOO": [(date(2021, 2, 1), "120.00")], "ACME.XYZ": []})
    first = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert first["added"] == 1
    second = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert (second["added"], second["updated"]) == (0, 0)   # same day, same value: no change
    assert _voo(ledger) == [(date(2021, 2, 1), "120.00")]


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


def test_a_price_in_another_currency_does_not_move_the_fetch_window(wired):
    """VOO is bought in USD; a hand EUR price dated today must not make the USD
    series look up to date (its gap must still be filled)."""
    ledger, _, reader, lm = wired
    lm.write_price_directives([
        data.Price({}, date(2021, 3, 1), "VOO", Amount(Decimal("130.00"), "USD")),
        data.Price({}, date(2021, 3, 10), "VOO", Amount(Decimal("110.00"), "EUR")),
    ])
    source = FakeSource({"VOO": [(date(2021, 3, 1), "130.00"), (date(2021, 3, 5), "132.00")],
                         "ACME.XYZ": []})
    PriceFetcher(reader, lm, source=source, today=date(2021, 3, 10)).fetch_and_persist()
    assert next(c for c in source.calls if c[0] == "VOO")[1] == date(2021, 3, 1)
    assert [(r["date"], r["quote_currency"]) for r in reader.get_prices(currency="VOO")] == [
        ("2021-03-01", "USD"), ("2021-03-05", "USD"), ("2021-03-10", "EUR"),
    ]


def test_a_price_dated_after_today_is_reported_not_silently_skipped(wired):
    ledger, _, reader, lm = wired
    lm.write_price_directives([data.Price({}, date(2022, 2, 1), "VOO", Amount(Decimal("1.00"), "USD"))])
    source = FakeSource({"VOO": [(date(2021, 2, 1), "120.00")], "ACME.XYZ": []})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert all(c[0] != "VOO" for c in source.calls)
    assert "VOO: newest price is dated after today (2022-02-01)" in res["failed"]


def test_a_tiny_price_is_written_without_an_exponent(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"VOO": [(date(2021, 2, 1), "0.0000001")], "ACME.XYZ": []})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert res["added"] == 1
    assert "2021-02-01 price VOO 0.0000001 USD" in sidecar_path(ledger).read_text().splitlines()


def test_quote_outside_the_window_gives_a_reason(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"VOO": [], "ACME.XYZ": []}, latest={"VOO": (date(2020, 1, 1), "99.00")})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert "VOO (VOO): no price in the date range" in res["failed"]


def test_update_endpoint_refuses_a_price_file_with_unreadable_lines(wired, monkeypatch):
    ledger, _, reader, lm = wired
    sidecar_path(ledger).write_text("2021-01-15 price VOO 1OO USD\n")
    before = sidecar_path(ledger).read_bytes()
    recent = date.today()
    monkeypatch.setattr(PriceFetcher, "_get_source",
                        lambda self: FakeSource({"VOO": [(recent, "120.00")], "ACME.XYZ": []}))
    from app.api.routers.prices import update_prices
    from app.exceptions import APIError
    with pytest.raises(APIError) as exc:
        asyncio.run(update_prices(reader=reader, ledger_manager=lm))
    assert (exc.value.code, exc.value.status_code) == ("PRICE_SIDECAR_INVALID", 422)
    assert sidecar_path(ledger).read_bytes() == before


@pytest.mark.parametrize("raw,expected", [
    (709.4299926757812, "709.43"),   # Yahoo float noise
    (342.0, "342.0"),                # already short: kept as sent
    (1.2736, "1.2736"),
    (67123.456, "67123.456"),
    (0.000123456789, "0.000123457"),  # tiny: 6 significant digits
    (1e-7, "0.0000001"),
])
def test_clean_price(raw, expected):
    from app.services.price_fetcher import clean_price
    assert format(clean_price(raw), "f") == expected


def test_fetched_float_noise_is_not_written(wired):
    ledger, _, reader, lm = wired
    source = FakeSource({"VOO": [(date(2021, 2, 1), "709.4299926757812")], "ACME.XYZ": []})
    PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert "2021-02-01 price VOO 709.43 USD" in sidecar_path(ledger).read_text().splitlines()


def test_minor_unit_quotes_are_written_in_the_main_currency(wired):
    """Yahoo quotes London in pence (GBp) and Johannesburg in cents (ZAc): the
    price is written in GBP / ZAR, divided by 100."""
    ledger, _, reader, lm = wired
    source = FakeSource(
        {"VOO": [(date(2021, 2, 1), "7250")], "ACME.XYZ": [(date(2021, 2, 1), "1234.5")]},
        currency={"VOO": "GBp", "ACME.XYZ": "ZAc"},
    )
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert res["added"] == 2
    assert sorted((p.currency, p.amount.number, p.amount.currency) for p in _sidecar_prices(ledger)) == [
        ("ACME", Decimal("12.345"), "ZAR"), ("VOO", Decimal("72.5"), "GBP"),
    ]


def test_an_invalid_provider_currency_fails_only_that_holding(wired):
    """A currency Beancount can't read is dropped for that holding (reported in
    `failed`); the other holdings are still written."""
    ledger, _, reader, lm = wired
    source = FakeSource(
        {"VOO": [(date(2021, 2, 1), "120.00")], "ACME.XYZ": [(date(2021, 2, 1), "5.00")]},
        currency={"ACME.XYZ": "xyz"},
    )
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert res["added"] == 1
    assert [f.split(":")[0] for f in res["failed"]] == ["ACME (ACME.XYZ)"]
    assert [(p.currency, p.date, p.amount.currency) for p in _sidecar_prices(ledger)] == [
        ("VOO", date(2021, 2, 1), "USD"),
    ]


def test_a_holding_quoted_in_another_currency_is_not_refetched_from_first_purchase(wired):
    """VOO is bought in USD but the provider quotes it in EUR. Once EUR prices
    are stored, the next fetch starts at the newest of them, not at the first
    purchase again."""
    ledger, _, reader, lm = wired
    source = FakeSource(
        {"VOO": [(date(2021, 1, 20), "100.00"), (date(2021, 2, 1), "101.00"), (date(2021, 2, 3), "102.00")],
         "ACME.XYZ": []},
        currency={"VOO": "EUR"},
    )
    PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    PriceFetcher(reader, lm, source=source, today=date(2021, 2, 3)).fetch_and_persist()
    assert [c[1:] for c in source.calls if c[0] == "VOO"] == [
        (date(2021, 1, 10), date(2021, 2, 1)),   # first fetch: from first purchase
        (date(2021, 2, 1), date(2021, 2, 3)),    # second: from the newest EUR price
    ]
    assert [(r["date"], r["quote_currency"]) for r in reader.get_prices(currency="VOO")] == [
        ("2021-01-20", "EUR"), ("2021-02-01", "EUR"), ("2021-02-03", "EUR"),
    ]


def test_a_ledger_with_no_holdings_fetches_nothing(tmp_path):
    ledger, _, reader, lm = _wire(tmp_path, """\
option "operating_currency" "USD"
2020-01-01 open Assets:Cash USD
2020-01-01 open Income:Salary USD
2021-01-10 * "pay"
  Assets:Cash  100.00 USD
  Income:Salary  -100.00 USD
""")
    source = FakeSource({})
    res = PriceFetcher(reader, lm, source=source, today=date(2021, 2, 1)).fetch_and_persist()
    assert source.calls == []
    assert res == {"added": 0, "total": 0, "as_of": None, "symbols": [], "skipped": [], "failed": []}
    assert not sidecar_path(ledger).exists()


_INR_FUND = """\
option "operating_currency" "INR"

2020-01-01 commodity PPFAS
  asset-class: "mutual-fund"
  fetch_symbol: "amfi:122639"
2020-01-01 commodity RELIANCE
  asset-class: "stock"
  fetch_symbol: "RELIANCE.NS"
2020-01-01 open Assets:Demat INR,PPFAS,RELIANCE
2020-01-01 open Assets:Bank INR

2026-10-01 * "buy fund"
  Assets:Demat  100 PPFAS {88.00 INR}
  Assets:Bank  -8800.00 INR
2026-10-01 * "buy stock"
  Assets:Demat  2 RELIANCE {1200.00 INR}
  Assets:Bank  -2400.00 INR
"""


class FakeAmfi:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def get_prices_series(self, ticker, time_begin, time_end):
        self.calls.append(ticker)
        return [SourcePrice(Decimal(v), datetime.combine(d, time.min, tzinfo=timezone.utc), "INR")
                for d, v in self.prices if time_begin.date() <= d <= time_end.date()]

    def get_latest_price(self, ticker):
        return None


def test_amfi_symbols_go_to_the_amfi_source(tmp_path):
    """`fetch_symbol: "amfi:<code>"` fetches that scheme's NAVs from AMFI, never
    from Yahoo; other holdings still go to Yahoo."""
    ledger, _, reader, lm = _wire(tmp_path, _INR_FUND)
    amfi = FakeAmfi([(date(2026, 10, 1), "88.10"), (date(2026, 10, 2), "88.40")])
    yahoo = FakeSource({"RELIANCE.NS": [(date(2026, 10, 2), "1216.40")]}, currency={"RELIANCE.NS": "INR"})
    res = PriceFetcher(reader, lm, source=yahoo, amfi_source=amfi, today=date(2026, 10, 2)).fetch_and_persist()
    assert amfi.calls == ["122639"]
    assert [c[0] for c in yahoo.calls] == ["RELIANCE.NS"]
    assert res["failed"] == []
    assert sorted((p.currency, p.date, p.amount.number, p.amount.currency) for p in _sidecar_prices(ledger)) == [
        ("PPFAS", date(2026, 10, 1), Decimal("88.10"), "INR"),
        ("PPFAS", date(2026, 10, 2), Decimal("88.40"), "INR"),
        ("RELIANCE", date(2026, 10, 2), Decimal("1216.40"), "INR"),
    ]


def test_a_latest_quote_without_a_currency_uses_the_holdings_currency(tmp_path):
    """Beanprice's Yahoo latest-quote has no currency for NSE/BSE tickers. When it
    is the only data, it is kept in the currency the holding's prices are in."""
    ledger, _, reader, lm = _wire(tmp_path, _INR_FUND)

    class NoCurrencyLatest(FakeSource):
        def get_latest_price(self, ticker):
            p = super().get_latest_price(ticker)
            return p._replace(quote_currency=None) if p else None

    yahoo = NoCurrencyLatest({"RELIANCE.NS": []}, latest={"RELIANCE.NS": (date(2026, 10, 2), "1216.40")})
    amfi = FakeAmfi([(date(2026, 10, 2), "88.40")])
    res = PriceFetcher(reader, lm, source=yahoo, amfi_source=amfi, today=date(2026, 10, 2)).fetch_and_persist()
    assert res["failed"] == []
    assert [(r["date"], r["quote_number"], r["quote_currency"]) for r in reader.get_prices(currency="RELIANCE")] == [
        ("2026-10-02", "1216.40", "INR"),
    ]
