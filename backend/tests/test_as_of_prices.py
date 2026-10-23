"""portfolio_series' as-of price lookup must answer exactly as Beancount does.

Specification: for any set of price directives and any (base, quote, date),
``_AsOfPrices`` returns the same number as Beancount's
``get_price(build_price_map(prices), (base, quote), date)`` — including prices
declared in the inverse direction, both directions at once (equal counts too),
several prices on one date, and zero prices. Checked exhaustively over random
price sets built to hit those cases.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.prices import build_price_map, get_price

from app.compute.functions.portfolio_series import _AsOfPrices

_CURRENCIES = ["FUND", "INR", "USD"]
_START = date(2020, 1, 1)


def _random_prices(rng: random.Random) -> list[dict]:
    rows = []
    for i in range(rng.randint(0, 25)):
        base, quote = rng.sample(_CURRENCIES, 2)
        number = rng.choice(["0", "1", "2.5", "10", "123.4567", "0.0003", str(rng.randint(1, 500))])
        rows.append({
            "id": i,
            # Narrow date range → frequent same-date collisions.
            "date": (_START + timedelta(days=rng.randint(0, 8))).isoformat(),
            "base_currency": base,
            "quote_number": number,
            "quote_currency": quote,
        })
    rows.sort(key=lambda r: (r["date"], r["id"]))  # get_prices_involving order
    return rows


def _beancount(rows):
    entries = [
        data.Price({}, date.fromisoformat(r["date"]), r["base_currency"],
                   Amount(Decimal(r["quote_number"]), r["quote_currency"]))
        for r in rows
    ]
    return build_price_map(entries)


def test_matches_beancount_on_random_price_sets():
    rng = random.Random(20261023)
    pairs = [(b, q) for b in _CURRENCIES for q in _CURRENCIES]
    lookup_dates = [_START + timedelta(days=n) for n in range(-1, 11)]
    for _ in range(3000):
        rows = _random_prices(rng)
        expected_map = _beancount(rows)
        ours = _AsOfPrices(rows, pairs)
        for base, quote in pairs:
            for d in lookup_dates:
                assert ours.get(base, quote, d) == get_price(expected_map, (base, quote), d)[1], (
                    rows, base, quote, d)


def test_both_directions_with_equal_counts_keeps_first_declared():
    # USD/INR first, then INR/USD, one point each on the same date: Beancount
    # keeps USD/INR and merges the inverted INR/USD point after it, so that
    # inverted point (last on the date) wins.
    rows = [
        {"id": 1, "date": "2020-01-01", "base_currency": "USD", "quote_number": "80", "quote_currency": "INR"},
        {"id": 2, "date": "2020-01-01", "base_currency": "INR", "quote_number": "0.0125", "quote_currency": "USD"},
    ]
    ours = _AsOfPrices(rows, [("USD", "INR")])
    assert ours.get("USD", "INR", date(2020, 1, 1)) == get_price(_beancount(rows), ("USD", "INR"), date(2020, 1, 1))[1]
    assert ours.get("USD", "INR", date(2020, 1, 1)) == Decimal(1) / Decimal("0.0125")
