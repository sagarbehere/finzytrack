"""AMFI (mfapi.in) price source for Indian mutual funds, and how the fetcher
routes `amfi:<scheme code>` symbols to it. Offline: the HTTP session is faked."""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.amfi_source import AmfiSource, scheme_code


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, by_code):
        self.by_code = by_code
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResponse(self.by_code.get(url.rsplit("/", 1)[-1], {"meta": {}, "data": []}))


_PPFAS = {"meta": {"scheme_name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"}, "data": [
    {"date": "19-10-2026", "nav": "90.35460"},
    {"date": "17-10-2026", "nav": "89.90000"},
    {"date": "bad", "nav": "1"},                 # malformed: skipped
    {"date": "16-10-2026", "nav": "89.10000"},
]}


def _dt(d):
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def test_scheme_code():
    assert scheme_code("amfi:122639") == "122639"
    assert scheme_code("AMFI: 122639 ") == "122639"
    assert scheme_code("amfi:") is None
    assert scheme_code("VOO") is None


def test_series_is_in_inr_oldest_first_and_windowed():
    session = FakeSession({"122639": _PPFAS})
    series = AmfiSource(session).get_prices_series("122639", _dt(date(2026, 10, 17)), _dt(date(2026, 10, 19)))
    assert [(p.time.date(), p.price, p.quote_currency) for p in series] == [
        (date(2026, 10, 17), Decimal("89.90000"), "INR"),
        (date(2026, 10, 19), Decimal("90.35460"), "INR"),
    ]
    assert session.urls == ["https://api.mfapi.in/mf/122639"]


def test_latest_and_unknown_scheme():
    src = AmfiSource(FakeSession({"122639": _PPFAS}))
    latest = src.get_latest_price("122639")
    assert (latest.time.date(), latest.price) == (date(2026, 10, 19), Decimal("90.35460"))
    assert src.get_latest_price("999999") is None
    assert src.get_prices_series("999999", _dt(date(2026, 1, 1)), _dt(date(2026, 12, 31))) == []
