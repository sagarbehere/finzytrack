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
_IBOND = "i-bond"  # valued formulaically via the ibonds library, not fetched

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
        if ac == _IBOND:
            return "ibond"
        if ac in _NEVER:
            return "never"
        if ac in _MONTHLY:
            return "monthly"
        return "daily"

    @staticmethod
    def _issue_ym(raw: Any) -> Optional[str]:
        """Normalise an ``issue_date`` metadata value to ibonds' ``MM/YYYY``.
        Accepts ``MM/YYYY``, ``YYYY-MM-DD``, or ``YYYY-MM``."""
        if not raw:
            return None
        s = str(raw).strip()
        if "/" in s:
            return s
        try:
            return date.fromisoformat(s).strftime("%m/%Y")
        except ValueError:
            parts = s.split("-")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                return f"{int(parts[1]):02d}/{parts[0]}"
        return None

    def _ibond_series(self, commodity, begin: date, today: date) -> list[data.Price]:
        """Monthly accrued per-unit prices for an I-Bond commodity, computed
        formulaically via the ``ibonds`` library from its ``issue_date`` /
        ``denomination`` metadata. Units are face dollars, so the per-unit price
        is ``value / denom`` (denomination-invariant). Opt-in: a commodity with
        no ``issue_date`` is skipped (left to manual pricing). Degrades
        gracefully: dates the bundled rate table can't cover yield ``None`` from
        ibonds and are skipped rather than raising (the semiannual rate refresh
        keeps this current — see dev-docs/valuations.md)."""
        meta = commodity.metadata or {}
        issue = self._issue_ym(meta.get("issue_date"))
        if issue is None:
            return []
        try:
            denom = int(str(meta.get("denomination") or "1000").replace(",", ""))
        except ValueError:
            denom = 1000

        try:
            from ibonds import IBond
            bond = IBond(issue_date=issue, denom=denom)
        except Exception as e:
            logger.warning("I-Bond %s: cannot init ibonds (%s): %s", commodity.code, issue, e)
            return []

        out: list[data.Price] = []
        # Month-start points from `begin` to `today` (I-Bond value steps monthly).
        y, m = begin.year, begin.month
        while date(y, m, 1) <= today:
            d = date(y, m, 1)
            if d >= begin:
                try:
                    v = bond.value(d)
                except Exception:
                    v = None
                if v is not None:
                    out.append(data.Price(
                        {}, d, commodity.code,
                        Amount((Decimal(str(v)) / denom).quantize(Decimal("0.0001")), "USD"),
                    ))
            idx = (y * 12 + (m - 1)) + 1
            y, m = idx // 12, idx % 12 + 1
        return out

    @staticmethod
    def _thin_monthly(prices: list[data.Price]) -> list[data.Price]:
        """Keep only the last price per (base, quote, year-month)."""
        keep: dict[tuple, data.Price] = {}
        for p in sorted(prices, key=lambda e: e.date):
            keep[(p.currency, p.amount.currency, p.date.year, p.date.month)] = p
        return list(keep.values())

    def _holdings_activity(self, codes: list[str]) -> dict:
        """Per-commodity ``{code: (first_date, last_date, net_units)}`` from
        postings — used to bound the fetch window to the ownership period."""
        out: dict[str, list] = {}
        for row in self._reader.get_postings_by_currency(codes):
            code = row["currency"]
            d = date.fromisoformat(row["transaction_date"])
            u = Decimal(row["amount"]) if row["amount"] is not None else Decimal(0)
            m = out.get(code)
            if m is None:
                out[code] = [d, d, u]
            else:
                m[0] = min(m[0], d)
                m[1] = max(m[1], d)
                m[2] += u
        return out

    def _fetch_series(self, source, symbol: str, begin: date, end: date):
        """Historical daily prices for ``[begin, end]``, provider-agnostic:
        try the source's ``get_daily_prices`` (Yahoo's real historical method)
        then ``get_prices_series`` (other sources implement this one), and if
        neither yields a series, fall back to ``get_latest_price`` (a different,
        more reliable endpoint — enough for "current value"). Returns
        ``(list[SourcePrice], reason_if_empty)``."""
        tb = datetime.combine(begin, time.min)
        te = datetime.combine(end, time.max)
        last_err: Optional[str] = None
        for name in ("get_daily_prices", "get_prices_series"):
            fn = getattr(source, name, None)
            if fn is None:
                continue
            try:
                series = fn(symbol, tb, te)
            except Exception as e:
                last_err = str(e)
                logger.warning("%s failed for %s: %s", name, symbol, e)
                continue
            if series:
                return series, None
        try:
            latest = source.get_latest_price(symbol)
            if latest is not None:
                return [latest], None
        except Exception as e:
            last_err = str(e)
            logger.warning("get_latest_price failed for %s: %s", symbol, e)
        return [], (last_err or "provider returned no data")

    def fetch_and_persist(self) -> dict:
        """Fetch and persist prices for every fetchable holding. Returns
        ``{added, total, as_of, symbols, skipped, failed}`` — ``failed`` lists
        ``"CODE (SYMBOL): reason"`` for holdings the provider gave nothing for."""
        today = self._today or date.today()
        commodities = [c for c in self._reader.get_commodities() if not c.is_currency]
        activity = self._holdings_activity([c.code for c in commodities])

        new_prices: list[data.Price] = []
        symbols: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for c in commodities:
            cadence = self._cadence(c.asset_class)
            if cadence == "never":
                skipped.append(c.code)
                continue

            first_dt, last_dt, net_units = activity.get(c.code, (None, None, Decimal(0)))
            if first_dt is None:
                continue  # declared but never actually held

            existing = self._reader.get_prices(currency=c.code)
            last_priced = max((date.fromisoformat(r["date"]) for r in existing), default=None)
            # Start the day after the last stored price, else at first purchase —
            # so the first fetch covers the whole ownership history, not a fixed
            # 5-year window.
            begin = (last_priced + timedelta(days=1)) if last_priced else first_dt
            # Stop at today while still held; a fully-sold holding needs no prices
            # past its last transaction (its value is 0 after that).
            end = today if net_units > 0 else min(last_dt, today)
            if begin > end:
                continue  # up to date / nothing to fetch in the ownership window

            # I-Bonds are valued formulaically (offline), not fetched.
            if cadence == "ibond":
                ib = self._ibond_series(c, begin, end)
                if ib:
                    new_prices.extend(ib)
                    symbols.append(c.code)
                else:
                    skipped.append(c.code)  # opt-out: no issue_date (leave to manual pricing)
                continue

            symbol = (c.metadata or {}).get("fetch_symbol") or c.code
            series, reason = self._fetch_series(self._get_source(), symbol, begin, end)

            fetched: list[data.Price] = []
            for sp in series:
                if sp.price is None or sp.quote_currency is None:
                    continue
                d = sp.time.date() if isinstance(sp.time, datetime) else sp.time
                if d < begin or d > end:
                    continue
                fetched.append(data.Price({}, d, c.code, Amount(Decimal(str(sp.price)), sp.quote_currency)))

            if cadence == "monthly":
                fetched = self._thin_monthly(fetched)
            if fetched:
                new_prices.extend(fetched)
                symbols.append(symbol)
            else:
                failed.append(f"{c.code} ({symbol}): {reason}")

        if failed:
            logger.warning("Price fetch got no data for: %s", "; ".join(failed))

        if not new_prices:
            return {"added": 0, "total": 0, "as_of": None,
                    "symbols": [], "skipped": skipped, "failed": failed}

        result = self._lm.write_price_directives(new_prices)
        result["symbols"] = symbols
        result["skipped"] = skipped
        result["failed"] = failed
        return result
