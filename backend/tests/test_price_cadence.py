"""The shared price rules (dev-docs/investment-view.md §10.2 P-Q3): cadence from
asset-class, and when a held holding's latest price is out of date."""

from datetime import date, timedelta

import pytest

from app.services.price_cadence import cadence_for, fetch_quote, is_out_of_date

TODAY = date(2026, 10, 20)


@pytest.mark.parametrize("asset_class,is_currency,expected", [
    ("etf", False, "daily"),
    ("stock", False, "daily"),
    (None, False, "daily"),
    ("Mutual-Fund", False, "daily"),
    ("bond", False, "monthly"),
    ("CD", False, "monthly"),
    ("i-bond", False, "computed"),
    ("money-market", False, "never"),
    ("cash", False, "never"),
    ("currency", False, "never"),
    ("etf", True, "never"),      # a currency-role commodity is never fetched
])
def test_cadence_for(asset_class, is_currency, expected):
    assert cadence_for(asset_class, is_currency=is_currency) == expected


@pytest.mark.parametrize("cadence,days,expected", [
    ("daily", 7, False), ("daily", 8, True),
    ("monthly", 45, False), ("monthly", 46, True),
    ("computed", 45, False), ("computed", 46, True),
    ("never", 1000, False),
])
def test_out_of_date_thresholds(cadence, days, expected):
    assert is_out_of_date(cadence, True, TODAY - timedelta(days=days), TODAY) is expected


def test_sold_out_is_never_out_of_date():
    assert is_out_of_date("daily", False, TODAY - timedelta(days=400), TODAY) is False
    assert is_out_of_date("daily", False, None, TODAY) is False


def test_held_with_no_price_is_out_of_date():
    assert is_out_of_date("daily", True, None, TODAY) is True
    assert is_out_of_date("never", True, None, TODAY) is False


@pytest.mark.parametrize("cost,counts,expected", [
    ("USD", {}, "USD"),                          # nothing stored yet: the bought-in currency
    ("USD", {"USD": 3, "EUR": 9}, "USD"),        # bought-in series exists: it wins
    ("USD", {"EUR": 4}, "EUR"),                  # provider quotes it elsewhere: that series
    ("USD", {"EUR": 2, "GBP": 5}, "GBP"),        # …the one with the most prices
    (None, {"EUR": 2, "GBP": 2}, "EUR"),         # tie: alphabetical
    (None, {}, None),
])
def test_fetch_quote(cost, counts, expected):
    assert fetch_quote(cost, counts) == expected
