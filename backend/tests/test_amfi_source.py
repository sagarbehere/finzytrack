"""AMFI (mfapi.in) price source for Indian mutual funds, and how the fetcher
routes `amfi:<scheme code>` symbols to it. Offline: the HTTP session is faked."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import requests

from app.services.amfi_source import (
    AmfiFormatError, AmfiSource, AmfiUnavailable, AmfiUnknownScheme, scheme_code,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, by_code):
        self.by_code = by_code
        self.urls = []

    def get(self, url, timeout, params=None):
        self.urls.append(url)
        # mfapi answers an unknown scheme with 200 and an empty record.
        return FakeResponse(self.by_code.get(url.rsplit("/", 1)[-1], {"meta": {"scheme_name": ""}, "data": []}))


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
    with pytest.raises(AmfiUnknownScheme):
        src.get_prices_series("999999", _dt(date(2026, 1, 1)), _dt(date(2026, 12, 31)))


def test_search_and_latest_nav():
    class Session(FakeSession):
        def get(self, url, timeout, params=None):
            self.urls.append((url, params))
            if url.endswith("/search"):
                return FakeResponse([{"schemeCode": 122639, "schemeName": "PPFAS Direct Growth"}, {"schemeCode": None}])
            return FakeResponse({"data": [{"date": "19-10-2026", "nav": "90.35460"}]})

    src = AmfiSource(Session({}))
    assert src.search("parag") == [("122639", "PPFAS Direct Growth")]
    nav = src.latest_nav("122639")
    assert (nav.time.date(), nav.price, nav.quote_currency) == (date(2026, 10, 19), Decimal("90.35460"), "INR")



# ── Failure modes: each is an AmfiError with a message for the user, never an
#    empty result that would read as "no such fund" ──


class OneResponse:
    """A session whose every request gets the same response (or raises)."""

    def __init__(self, response):
        self.response = response

    def get(self, url, timeout, params=None):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize("response,error", [
    (requests.ConnectionError("no route"), AmfiUnavailable),        # offline
    (requests.Timeout("slow"), AmfiUnavailable),
    (FakeResponse("Cannot GET", status_code=404), AmfiFormatError),  # the address is gone
    (FakeResponse("busy", status_code=503), AmfiUnavailable),
    (FakeResponse(ValueError("not JSON")), AmfiFormatError),         # e.g. an HTML page
    (FakeResponse({"results": []}), AmfiFormatError),                # wrong shape
    (FakeResponse([{"code": 1, "title": "x"}]), AmfiFormatError),    # renamed fields
])
def test_search_failures_are_explained(response, error):
    with pytest.raises(error) as exc:
        AmfiSource(OneResponse(response)).search("parag")
    assert "mfapi.in" in str(exc.value)


def test_an_empty_search_is_just_empty():
    assert AmfiSource(OneResponse(FakeResponse([]))).search("zzqqxx") == []


@pytest.mark.parametrize("response,error", [
    (requests.ConnectionError("no route"), AmfiUnavailable),
    (FakeResponse({"meta": {}, "rows": []}), AmfiFormatError),                              # no data list
    (FakeResponse({"meta": {"scheme_name": "X"}, "data": [{"d": 1}, {"d": 2}]}), AmfiFormatError),  # unreadable rows
])
def test_history_failures_are_explained(response, error):
    with pytest.raises(error):
        AmfiSource(OneResponse(response)).get_prices_series("122639", _dt(date(2026, 1, 1)), _dt(date(2026, 12, 31)))
