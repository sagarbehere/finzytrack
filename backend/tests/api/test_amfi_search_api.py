"""GET /api/prices/amfi/search — find Indian mutual fund scheme codes by name,
with each scheme's latest NAV. Offline: the AMFI source is replaced."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from beanprice.source import SourcePrice

import app.api.routers.prices as prices_router


def _at(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


class FakeAmfi:
    today = date.today()
    matches = [
        ("111941", "Sundaram Mid Cap Fund Institutional Plan - Growth"),
        ("101539", "Sundaram Mid Cap Fund - Regular Plan - GROWTH"),
        ("119581", "Sundaram Mid Cap Fund - Direct Plan - GROWTH"),
        ("999999", "Sundaram Mid Cap Fund - Broken"),
    ]
    navs = {
        "111941": SourcePrice(Decimal("767.7664"), _at(date(2021, 12, 31)), "INR"),   # closed long ago
        "101539": SourcePrice(Decimal("1386.6013"), _at(today - timedelta(days=2)), "INR"),
        "119581": SourcePrice(Decimal("1529.8227"), _at(today - timedelta(days=2)), "INR"),
    }

    def search(self, query):
        return [m for m in self.matches if query.lower() in m[1].lower()]

    def latest_nav(self, code):
        if code == "999999":
            raise RuntimeError("timeout")
        return self.navs.get(code)


def test_search_lists_schemes_with_their_latest_nav(test_client, monkeypatch):
    monkeypatch.setattr(prices_router, "AmfiSource", FakeAmfi)
    resp = test_client.get("/api/prices/amfi/search", params={"q": "sundaram mid cap"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    # Currently priced schemes first; a closed scheme and one whose NAV failed follow.
    assert [(s["scheme_code"], s["nav"], s["nav_date"]) for s in data["schemes"]] == [
        ("101539", "1386.6013", (FakeAmfi.today - timedelta(days=2)).isoformat()),
        ("119581", "1529.8227", (FakeAmfi.today - timedelta(days=2)).isoformat()),
        ("111941", "767.7664", "2021-12-31"),
        ("999999", None, None),
    ]


def test_search_needs_at_least_three_characters(test_client, monkeypatch):
    monkeypatch.setattr(prices_router, "AmfiSource", FakeAmfi)
    assert test_client.get("/api/prices/amfi/search", params={"q": "ab"}).status_code == 422


def test_an_unreachable_provider_is_a_structured_error(test_client, monkeypatch):
    class Down(FakeAmfi):
        def search(self, query):
            raise ConnectionError("no network")

    monkeypatch.setattr(prices_router, "AmfiSource", Down)
    resp = test_client.get("/api/prices/amfi/search", params={"q": "sundaram"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PRICE_SOURCE_UNAVAILABLE"



def test_a_changed_provider_says_so(test_client, monkeypatch):
    from app.services.amfi_source import AmfiFormatError

    class Changed(FakeAmfi):
        def search(self, query):
            raise AmfiFormatError("The Indian mutual fund price service (mfapi.in) answered in an unexpected way.")

    monkeypatch.setattr(prices_router, "AmfiSource", Changed)
    resp = test_client.get("/api/prices/amfi/search", params={"q": "sundaram"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PRICE_SOURCE_CHANGED"


def test_schemes_are_still_listed_when_navs_fail(test_client, monkeypatch):
    class NoNavs(FakeAmfi):
        def latest_nav(self, code):
            raise RuntimeError("timeout")

    monkeypatch.setattr(prices_router, "AmfiSource", NoNavs)
    data = test_client.get("/api/prices/amfi/search", params={"q": "sundaram"}).json()["data"]
    assert [s["scheme_code"] for s in data["schemes"]] == ["111941", "101539", "119581", "999999"]
    assert all(s["nav"] is None for s in data["schemes"])
    assert data["nav_warning"] is not None


def test_no_warning_when_all_navs_load(test_client, monkeypatch):
    class AllNavs(FakeAmfi):
        matches = FakeAmfi.matches[:3]

    monkeypatch.setattr(prices_router, "AmfiSource", AllNavs)
    data = test_client.get("/api/prices/amfi/search", params={"q": "sundaram"}).json()["data"]
    assert data["nav_warning"] is None
