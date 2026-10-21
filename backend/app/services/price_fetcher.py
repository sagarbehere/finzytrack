"""price_fetcher — fetch market prices into the price sidecar.

One keyless provider (Beancount's own ``beanprice`` Yahoo source) plus manual
editing — no adapter framework (dev-docs/valuations.md §4). For each investment
holding (``is_currency = 0``) it reads the ticker from ``fetch_symbol`` metadata
(falling back to the commodity code), gap-fills only the dates since the last
persisted price, and writes the results to ``prices.beancount`` through the
ledger manager's dedicated sidecar writer. It is idempotent and never talks to a
transaction file.

For a holding still held, the window starts **at** the newest stored day and the
fetched value replaces it, so the latest price is always the one from the last
fetch and a mid-day value is corrected to its close; older days stay a fixed
history (dev-docs/investment-view.md §10.2 P-Q6).

Cadence and held-units come from ``price_cadence`` (shared with the Prices tab):
ETFs/stocks/funds are daily, bonds/CDs are monthly (one point per month), I-Bonds
are computed, and money-market / cash / currency are never fetched (a
money-market fund sits at ~1.00, valued via a manual sidecar price). The provider
network call is the only impure part; the source is injectable so tests run fully
offline.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Optional

from beancount.core import data
from beancount.core.amount import Amount

from app.core.constants import COMMODITY_CODE_PATTERN
from app.core.ledger_loader import load_ledger_checked, sidecar_path
from app.services.price_cadence import (
    CADENCE_COMPUTED,
    CADENCE_MONTHLY,
    CADENCE_NEVER,
    cadence_for,
    fetch_quote,
    holdings_activity,
    issue_month,
)

logger = logging.getLogger(__name__)


def clean_price(value: Any) -> Decimal:
    """A provider's price as a short decimal. Yahoo returns binary floats, so
    ``str(709.43)`` can arrive as ``709.4299926757812``. Round to 4 decimal
    places, or to 6 significant digits when that keeps more (tiny prices);
    a value already that short is kept as sent. Written plainly (no exponent)."""
    n = Decimal(str(value))
    if n == 0:
        return n
    places = max(4, 5 - n.adjusted())
    if -n.as_tuple().exponent <= places:
        return n  # already short: keep it as sent (e.g. 120.00)
    text = format(n.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), "f")
    return Decimal(text.rstrip("0").rstrip("."))

# Yahoo quotes some exchanges in the currency's minor unit: London in pence
# (GBp / GBX), Johannesburg in cents (ZAc), Tel Aviv in agorot (ILA). Convert to
# the main unit — "GBp" isn't even a valid Beancount currency, and a price in
# pence would value the holding 100x too high.
_MINOR_UNITS = {
    "GBp": ("GBP", Decimal(100)),
    "GBX": ("GBP", Decimal(100)),
    "ZAc": ("ZAR", Decimal(100)),
    "ILA": ("ILS", Decimal(100)),
}

_CURRENCY = re.compile(COMMODITY_CODE_PATTERN)


def to_main_unit(number: Decimal, currency: str) -> tuple[Decimal, str]:
    """A provider price in its main currency unit (``72.5 GBp`` → ``0.725 GBP``)."""
    main = _MINOR_UNITS.get(currency)
    return (number / main[1], main[0]) if main else (number, currency)


# How far back to reach on the very first fetch of a holding with no price yet.
_DEFAULT_LOOKBACK_DAYS = 5 * 365


def _has_month_start(begin: date, end: date) -> bool:
    """Whether ``[begin, end]`` contains the 1st of a month (an I-Bond value point)."""
    first = begin if begin.day == 1 else date(
        begin.year + (begin.month == 12), begin.month % 12 + 1, 1,
    )
    return first <= end


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
        issue = issue_month(meta.get("issue_date"))
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

    def _stored_prices(self) -> list[data.Price]:
        """The prices in the price file now (none when it doesn't exist)."""
        path = sidecar_path(self._lm.ledger_file)
        if not path.exists():
            return []
        entries, _errors, _options = load_ledger_checked(str(path))
        return [e for e in entries if isinstance(e, data.Price)]

    def fetch_and_persist(self) -> dict:
        """Fetch and persist prices for every fetchable holding. Returns
        ``{added, total, as_of, symbols, skipped, failed}`` — ``failed`` lists
        ``"CODE (SYMBOL): reason"`` for holdings the provider gave nothing for."""
        today = self._today or date.today()
        commodities = [c for c in self._reader.get_commodities() if not c.is_currency]
        activity = holdings_activity(self._reader, [c.code for c in commodities])

        new_prices: list[data.Price] = []
        remove_keys: list[tuple] = []
        symbols: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for c in commodities:
            cadence = cadence_for(c.asset_class)
            if cadence == CADENCE_NEVER:
                skipped.append(c.code)
                continue

            act = activity.get(c.code)
            if act is None:
                continue  # declared but never actually held

            existing = self._reader.get_prices(currency=c.code)
            # Only the series the fetcher keeps sets the window: a price typed in
            # another currency neither stops nor starts a fetch.
            counts: dict[str, int] = {}
            for r in existing:
                counts[r["quote_currency"]] = counts.get(r["quote_currency"], 0) + 1
            quote = fetch_quote(act.cost_currency, counts)
            last_priced = max(
                (date.fromisoformat(r["date"]) for r in existing if r["quote_currency"] == quote),
                default=None,
            )
            if last_priced is not None and last_priced > today:
                # Newest price is dated after today (a typo, most likely): say so
                # rather than silently never fetching this holding again.
                failed.append(f"{c.code}: newest price is dated after today ({last_priced.isoformat()})")
                continue
            # While held, start AT the newest stored day so this fetch replaces it
            # (P-Q6); a sold-out holding's last price is final, so start after it.
            # With no price yet, start at first purchase — so the first fetch
            # covers the whole ownership history, not a fixed 5-year window.
            if last_priced is None:
                begin = act.first
            elif act.held:
                begin = last_priced
            else:
                begin = last_priced + timedelta(days=1)
            # Stop at today while still held; a fully-sold holding needs no prices
            # past its last transaction (its value is 0 after that).
            end = today if act.held else min(act.last, today)
            if begin > end:
                continue  # sold out and already priced to its last transaction

            # I-Bonds are valued formulaically (offline), not fetched.
            if cadence == CADENCE_COMPUTED:
                if issue_month((c.metadata or {}).get("issue_date")) is None:
                    skipped.append(c.code)  # opt-out: no issue_date (leave to manual pricing)
                    continue
                ib = self._ibond_series(c, begin, end)
                if ib:
                    new_prices.extend(ib)
                    symbols.append(c.code)
                elif _has_month_start(begin, end):
                    failed.append(f"{c.code}: could not compute the I-Bond value (check issue month and face value)")
                # else: no new month since the last value — up to date
                continue

            symbol = (c.metadata or {}).get("fetch_symbol") or c.code
            series, reason = self._fetch_series(self._get_source(), symbol, begin, end)

            fetched: list[data.Price] = []
            for sp in series:
                if sp.price is None or sp.quote_currency is None:
                    continue
                d = sp.time.date() if isinstance(sp.time, datetime) else sp.time
                if d < begin or d > end:
                    reason = reason or "no price in the date range"
                    continue
                number, currency = to_main_unit(Decimal(str(sp.price)), sp.quote_currency)
                if not _CURRENCY.match(currency):
                    # One unreadable currency would make the whole price file
                    # fail its read-back check; drop just this holding.
                    reason = f"the provider's currency '{currency}' is not a valid currency code"
                    continue
                fetched.append(data.Price({}, d, c.code, Amount(clean_price(number), currency)))

            if cadence == CADENCE_MONTHLY:
                fetched = self._thin_monthly(fetched)
                # One point per month: a newer point replaces an older one stored
                # in the same month (e.g. last fetch's mid-month value).
                newest = {(p.amount.currency, p.date.year, p.date.month): p.date for p in fetched}
                for r in existing:
                    d = date.fromisoformat(r["date"])
                    cut = newest.get((r["quote_currency"], d.year, d.month))
                    if cut is not None and d < cut:
                        remove_keys.append((d, c.code, r["quote_currency"]))
            if fetched:
                new_prices.extend(fetched)
                symbols.append(symbol)
            else:
                failed.append(f"{c.code} ({symbol}): {reason}")

        if failed:
            logger.warning("Price fetch got no data for: %s", "; ".join(failed))

        if not new_prices:
            # Nothing to write: report the price file as it stands, so "no new
            # prices" isn't mistaken for "no prices at all".
            stored = self._stored_prices()
            as_of = max((p.date for p in stored), default=None)
            return {"added": 0, "total": len(stored), "as_of": as_of.isoformat() if as_of else None,
                    "symbols": [], "skipped": skipped, "failed": failed}

        result = self._lm.write_price_directives(new_prices, remove_keys)
        result["symbols"] = symbols
        result["skipped"] = skipped
        result["failed"] = failed
        return result
