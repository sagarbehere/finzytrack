"""portfolio_series — investment value-over-time, valued against the price map.

For each sampled date it reports, per quote currency, the **market value** of the
holdings held as-of that date (units-as-of × price-on-or-before) and their
**cost basis** (what was paid for the units still held). This is the honest
"how has my portfolio grown, and how much did I put in" series behind the
investment dashboards (dev-docs/investment-dashboards.md §4.1).

Design points it honours (dev-docs/valuations.md §5):
- **As-of pricing**: the latest price on or before the date, never a future one
  (Beancount's `build_price_map` / `get_price` — reused, not reimplemented).
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
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.prices import build_price_map, get_price

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
        "[{date: 'YYYY-MM-DD', group, currency, market_value, cost_basis, degraded}] "
        "— market_value/cost_basis are decimal strings; degraded is a bool; one row "
        "per (date, group, currency)."
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

    async def execute(self, **args: Any) -> list[dict]:
        try:
            date_to = date.fromisoformat(str(args["to"]))
            raw_from = args.get("from")
            date_from = date.fromisoformat(str(raw_from)) if raw_from else None
        except (ValueError, KeyError) as e:
            raise ValueError(f"'to' (and 'from' if given) must be YYYY-MM-DD dates: {e}")

        scope = args.get("scope") or "overall"
        cadence = args.get("cadence") or "monthly"
        want_currency = args.get("currency")

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

        # Price map from the mirror's prices (main-ledger + sidecar already merged
        # at export). Reused Beancount as-of lookup.
        price_entries = [
            data.Price({}, date.fromisoformat(r["date"]), r["base_currency"],
                       Amount(Decimal(r["quote_number"]), r["quote_currency"]))
            for r in self._reader.get_prices()
        ]
        price_map = build_price_map(price_entries)

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
                _, pnum = get_price(price_map, (code, quote), d)
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
                result.append({
                    "date": d.isoformat(),
                    "group": group,
                    "currency": quote,
                    "market_value": str(b["market"]),
                    "cost_basis": str(b["basis"]),
                    "degraded": b["degraded"],
                })

        return result
