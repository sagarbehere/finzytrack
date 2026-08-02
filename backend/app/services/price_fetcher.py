"""price_fetcher — fetch market prices into the price sidecar.

One keyless provider (Beancount's own ``beanprice`` Yahoo source) plus manual
editing — no adapter framework (dev-docs/valuations.md §4). For each investment
holding (``is_currency = 0``) it reads the ticker from ``fetch_symbol`` metadata
(falling back to the commodity code), gap-fills only the dates since the last
persisted price (historical closes are immutable), and writes the results to
``prices.beancount`` through the ledger manager's dedicated sidecar writer. It is
idempotent and never talks to a transaction file.

Cadence is derived from the commodity's ``asset-class``: ETFs/stocks/funds are
daily, bonds/CDs are monthly (they only move monthly), and money-market /
cash / currency are never fetched (a money-market fund sits at ~1.00, valued via
a manual sidecar price). The provider network call is the only impure part; the
source is injectable so tests run fully offline.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Optional

from beancount.core import data
from beancount.core.amount import Amount

logger = logging.getLogger(__name__)

# asset-class → fetch cadence. Anything unlisted defaults to daily.
_NEVER = {"money-market", "cash", "currency"}
_MONTHLY = {"bond", "cd"}

# How far back to reach on the very first fetch of a holding with no price yet.
_DEFAULT_LOOKBACK_DAYS = 5 * 365


class PriceFetcher:
    def __init__(self, reader, ledger_manager, source: Any = None,
                 today: Optional[date] = None,
                 default_lookback_days: int = _DEFAULT_LOOKBACK_DAYS) -> None:
        self._reader = reader
        self._lm = ledger_manager
        self._source = source            # injectable; lazily defaults to Yahoo
        self._today = today
        self._lookback = default_lookback_days

    def _get_source(self):
        if self._source is None:
            from beanprice.sources import yahoo  # imported lazily (network dep)
            self._source = yahoo.Source()
        return self._source

    @staticmethod
    def _cadence(asset_class: Optional[str]) -> str:
        ac = (asset_class or "").lower()
        if ac in _NEVER:
            return "never"
        if ac in _MONTHLY:
            return "monthly"
        return "daily"

    @staticmethod
    def _thin_monthly(prices: list[data.Price]) -> list[data.Price]:
        """Keep only the last price per (base, quote, year-month)."""
        keep: dict[tuple, data.Price] = {}
        for p in sorted(prices, key=lambda e: e.date):
            keep[(p.currency, p.amount.currency, p.date.year, p.date.month)] = p
        return list(keep.values())

    def fetch_and_persist(self) -> dict:
        """Fetch and persist prices for every fetchable holding. Returns
        ``{added, total, as_of, symbols, skipped}``."""
        today = self._today or date.today()
        commodities = [c for c in self._reader.get_commodities() if not c.is_currency]

        new_prices: list[data.Price] = []
        symbols: list[str] = []
        skipped: list[str] = []

        for c in commodities:
            cadence = self._cadence(c.asset_class)
            if cadence == "never":
                skipped.append(c.code)
                continue
            symbol = (c.metadata or {}).get("fetch_symbol") or c.code

            existing = self._reader.get_prices(currency=c.code)
            last = max((date.fromisoformat(r["date"]) for r in existing), default=None)
            begin = (last + timedelta(days=1)) if last else (today - timedelta(days=self._lookback))
            if begin > today:
                continue  # already up to date

            try:
                series = self._get_source().get_prices_series(
                    symbol, datetime.combine(begin, time.min), datetime.combine(today, time.max)
                )
            except Exception as e:
                logger.warning("Price fetch failed for %s (%s): %s", c.code, symbol, e)
                continue

            fetched: list[data.Price] = []
            for sp in series or []:
                if sp.price is None or sp.quote_currency is None:
                    continue
                d = sp.time.date() if isinstance(sp.time, datetime) else sp.time
                if d < begin or d > today:
                    continue
                fetched.append(data.Price({}, d, c.code, Amount(Decimal(str(sp.price)), sp.quote_currency)))

            if cadence == "monthly":
                fetched = self._thin_monthly(fetched)
            if fetched:
                new_prices.extend(fetched)
                symbols.append(symbol)

        if not new_prices:
            return {"added": 0, "total": 0, "as_of": None, "symbols": [], "skipped": skipped}

        result = self._lm.write_price_directives(new_prices)
        result["symbols"] = symbols
        result["skipped"] = skipped
        return result
