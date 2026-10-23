"""portfolio_series — investment value-over-time, valued against the price map.

For each sampled date it reports, per quote currency, the **market value** of the
holdings held as-of that date (units-as-of × price-on-or-before) and their
**cost basis** (what was paid for the units still held). This is the honest
"how has my portfolio grown, and how much did I put in" series behind the
investment dashboards (dev-docs/investment-dashboards.md §4.1).

Design points it honours (dev-docs/valuations.md §5):
- **As-of pricing**: the latest price on or before the date, never a future one.
  `_AsOfPrices` gives exactly Beancount's `build_price_map` + `get_price` answers
  but only for the pairs being valued (see its docstring for why).
- **No cross-currency conversion**: each holding is valued in its own quote
  currency; totals are per currency, never summed across (valuations.md §1).
- **Missing price → degraded**: fall back to cost basis and flag the point, so a
  surface can warn instead of showing a silently-wrong total.
- Money in and out as **decimal strings** (money-types.md). This function does no
  float arithmetic.
"""

from __future__ import annotations

import calendar
import logging
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from beancount.utils.misc_utils import sorted_uniquify

from app.compute.base import ComputeFunction
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _sample_dates(d_from: date, d_to: date, cadence: str) -> list[date]:
    """Sample dates in ``[d_from, d_to]`` at the given cadence, always ending
    exactly at ``d_to`` (so the last point is the current value)."""
    out: list[date] = []
    if cadence == "daily":
        cur = d_from
        while cur <= d_to:
            out.append(cur)
            cur += timedelta(days=1)
    elif cadence == "weekly":
        cur = d_from
        while cur <= d_to:
            out.append(cur)
            cur += timedelta(days=7)
    else:  # monthly (default) / quarterly — period-end points
        step = 3 if cadence == "quarterly" else 1
        y, m = d_from.year, d_from.month
        while date(y, m, 1) <= d_to:
            me = _month_end(y, m)
            if d_from <= me <= d_to:
                out.append(me)
            idx = (y * 12 + (m - 1)) + step
            y, m = idx // 12, idx % 12 + 1
    if not out or out[-1] != d_to:
        out.append(d_to)
    return out


class _AsOfPrices:
    """As-of price lookup for a known set of ``(base, quote)`` pairs, giving the
    same answers as Beancount's ``get_price(build_price_map(prices), ...)``.

    Building Beancount's full price map means a ``Price``/``Amount`` object per
    row plus an inverted copy of every pair — for a ledger with years of daily
    fund prices that dominated the cost of each call. This builds only the pairs
    asked for, keeps prices as decimal strings until looked up, and keeps
    nothing between calls. Beancount's rules, reproduced:

    - A pair declared in both directions keeps the direction with more points
      (first-declared on a tie); the other is inverted and merged into it.
    - Several prices on one date: the last declared wins (``sorted_uniquify``).
    - Looking up the reverse of the kept direction inverts it, skipping zeros.
    - A price is the latest on or before the date; a commodity in itself is 1.

    ``rows`` must hold every price involving the pairs' currencies, in
    declaration order (``SqliteReader.get_prices_involving``).
    """

    def __init__(self, rows: Iterable[dict], pairs: Iterable[tuple[str, str]]) -> None:
        raw: dict[tuple[str, str], list[tuple[str, Any]]] = {}
        for r in rows:
            raw.setdefault((r["base_currency"], r["quote_currency"]), []).append(
                (r["date"], r["quote_number"])
            )
        # dict insertion order = first declaration of each pair (the tie-break).
        first_seen = {pair: i for i, pair in enumerate(raw)}
        self._prices: dict[tuple[str, str], tuple[list[str], list[Any]]] = {}
        for pair in set(pairs):
            resolved = self._resolve(raw, first_seen, pair)
            if resolved is not None:
                self._prices[pair] = (
                    [d for d, _ in resolved], [n for _, n in resolved]
                )

    @staticmethod
    def _resolve(raw, first_seen, pair):
        base, quote = pair
        forward, backward = raw.get((base, quote)), raw.get((quote, base))
        if forward is None and backward is None:
            return None
        if forward is not None and backward is not None:
            first, second = sorted([(base, quote), (quote, base)], key=first_seen.__getitem__)
            removed = first if len(raw[first]) < len(raw[second]) else second
            kept = second if removed == first else first
            merged = list(raw[kept]) + [
                (d, Decimal(1) / Decimal(n)) for d, n in raw[removed] if Decimal(n) != 0
            ]
        else:
            kept = (base, quote) if forward is not None else (quote, base)
            merged = raw[kept]
        uniq = list(sorted_uniquify(merged, lambda x: x[0], last=True))
        if kept == pair:
            return uniq
        return [(d, Decimal(1) / Decimal(n)) for d, n in uniq if Decimal(n) != 0]

    def get(self, base: str, quote: str, on: date) -> Decimal | None:
        if base == quote:
            return Decimal(1)
        found = self._prices.get((base, quote))
        if found is None:
            return None
        dates, numbers = found
        i = bisect_right(dates, on.isoformat())
        return Decimal(numbers[i - 1]) if i else None


class PortfolioSeriesFunction(ComputeFunction):
    name = "portfolio_series"
    description = (
        "Value-over-time for investment holdings. For each sampled date returns, "
        "per quote currency, the market value of holdings held as-of that date "
        "(units-as-of x latest price on-or-before) and their cost basis. "
        "scope='overall' totals everything (group='Total'); 'asset-class' groups "
        "by the commodity's asset-class; 'holding' is one series per commodity "
        "(group=commodity code). No cross-currency conversion — each holding is "
        "valued in its own quote currency. If a held commodity has no price on a "
        "date, its value falls back to cost basis and the point is marked degraded. "
        "Money values are decimal strings."
    )
    output_shape = (
        "[{date: 'YYYY-MM-DD', group, currency, market_value, cost_basis, unrealized, "
        "unrealized_pct, degraded}] — market_value/cost_basis/unrealized are decimal "
        "strings; unrealized = market_value - cost_basis; unrealized_pct is a ratio "
        "string or null; degraded is a bool; one row per (date, group, currency)."
    )
    parameters_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["to"],
        "properties": {
            "from": {"type": "string", "description": "Inclusive start, YYYY-MM-DD. Omit to start at the first holding transaction."},
            "to": {"type": "string", "description": "Inclusive end, YYYY-MM-DD (the 'current' point)."},
            "scope": {"type": "string", "enum": ["overall", "asset-class", "holding"], "description": "Grouping. Default 'overall'."},
            "currency": {"type": "string", "description": "Restrict output to one quote currency; omit for all."},
            "cadence": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly"], "description": "Sampling cadence. Default 'monthly'."},
        },
    }

    def __init__(self, reader: SqliteReader) -> None:
        self._reader = reader

    def execute(self, **args: Any) -> list[dict]:
        try:
            date_to = date.fromisoformat(str(args["to"]))
            raw_from = args.get("from")
            date_from = date.fromisoformat(str(raw_from)) if raw_from else None
        except (ValueError, KeyError) as e:
            raise ValueError(f"'to' (and 'from' if given) must be YYYY-MM-DD dates: {e}")

        scope = args.get("scope") or "overall"
        cadence = args.get("cadence") or "monthly"
        want_currency = args.get("currency")
        # '*' is the dashboard currency-filter's "All" sentinel — treat it as
        # "no restriction" so a filtered dashboard step returns every currency
        # (dev-docs/dashboard-multi-currency.md).
        if want_currency == "*":
            want_currency = None

        # Holdings = non-currency commodities. Keep asset-class for grouping.
        asset_class_of = {
            c.code: (c.asset_class or "unclassified")
            for c in self._reader.get_commodities() if not c.is_currency
        }
        if not asset_class_of:
            return []

        postings = self._reader.get_postings_by_currency(list(asset_class_of))
        if not postings:
            return []

        # Per-holding: sorted (date, units, cost) events, and its quote currency
        # (the currency it is valued in — its cost currency; no conversion).
        events: dict[str, list[tuple[date, Decimal, Decimal]]] = {}
        quote_of: dict[str, str] = {}
        for p in postings:
            code = p["currency"]
            units = Decimal(p["amount"]) if p["amount"] is not None else Decimal(0)
            cost = Decimal(p["cost_amount"]) if p["cost_amount"] is not None else Decimal(0)
            events.setdefault(code, []).append((date.fromisoformat(p["transaction_date"]), units, cost))
            if code not in quote_of and p["cost_currency"]:
                quote_of[code] = p["cost_currency"]

        # As-of prices for exactly the (holding, quote) pairs being valued, from
        # the mirror's prices (main-ledger + sidecar already merged at export).
        prices = _AsOfPrices(
            self._reader.get_prices_involving(list(quote_of)),
            quote_of.items(),
        )

        if date_from is None:
            date_from = min(ev[0] for evs in events.values() for ev in evs)
        if date_to < date_from:
            return []

        samples = _sample_dates(date_from, date_to, cadence)

        # Advance a per-holding pointer as sample dates increase, accumulating
        # units and cost basis (cost basis = sum of units x cost, which nets out
        # correctly when a sale's reducing leg carries the lot's cost).
        for code in events:
            events[code].sort(key=lambda e: e[0])
        idx = {code: 0 for code in events}
        units_run = {code: Decimal(0) for code in events}
        basis_run = {code: Decimal(0) for code in events}

        result: list[dict] = []
        for d in samples:
            for code, evs in events.items():
                i = idx[code]
                while i < len(evs) and evs[i][0] <= d:
                    _, u, c = evs[i]
                    units_run[code] += u
                    basis_run[code] += u * c
                    i += 1
                idx[code] = i

            # Bucket by (group, quote currency).
            buckets: dict[tuple[str, str], dict[str, Any]] = {}
            for code, evs in events.items():
                units = units_run[code]
                if units == 0:
                    continue
                quote = quote_of.get(code)
                if quote is None:
                    continue
                if want_currency and quote != want_currency:
                    continue
                basis = basis_run[code]
                pnum = prices.get(code, quote, d)
                if pnum is not None:
                    market = units * pnum
                    degraded = False
                else:
                    market = basis   # fall back to cost; flag it
                    degraded = True

                if scope == "asset-class":
                    group = asset_class_of.get(code, "unclassified")
                elif scope == "holding":
                    group = code
                else:
                    group = "Total"

                b = buckets.setdefault(
                    (group, quote),
                    {"market": Decimal(0), "basis": Decimal(0), "degraded": False},
                )
                b["market"] += market
                b["basis"] += basis
                b["degraded"] = b["degraded"] or degraded

            for (group, quote), b in sorted(buckets.items()):
                unrealized = b["market"] - b["basis"]
                unrealized_pct = (unrealized / b["basis"]) if b["basis"] != 0 else None
                result.append({
                    "date": d.isoformat(),
                    "group": group,
                    "currency": quote,
                    "market_value": str(b["market"]),
                    "cost_basis": str(b["basis"]),
                    "unrealized": str(unrealized),
                    "unrealized_pct": (str(unrealized_pct) if unrealized_pct is not None else None),
                    "degraded": b["degraded"],
                })

        return result
