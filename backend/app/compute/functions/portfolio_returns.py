"""portfolio_returns — money-weighted return (XIRR) + simple gain.

XIRR is the return metric a serious investor actually cites: it accounts for
*when* money went in. Scope is deliberately limited to **portfolio and
asset-class** (dev-docs/investment-dashboards.md §5) — the aggregates people
quote — where the cash-flow series is dense enough for the solver to be trusted;
per-holding / per-year use simple gain instead.

Cash-flow model (dev-docs/valuations.md §5): the external cash exchanged with the
portfolio — a buy's cash leg (negative, money in), a sale's proceeds and a cash
dividend (positive, money out), while a reinvested-income DRIP has no cash leg
and nets to zero — plus the terminal market value as a final positive flow.

The bracketed bisection solver is the **one sanctioned float** use (valuations.md
invariant); it never touches stored money, and the rate is returned as a decimal
string. Degenerate inputs (fewer than two flows, no sign change, non-convergence)
return ``xirr: null`` rather than a fabricated number.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.compute.base import ComputeFunction
from app.compute.functions.portfolio_series import PortfolioSeriesFunction
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)

_CASH_ACCOUNT_TYPES = ("Assets", "Liabilities")


def _xnpv(rate: float, flows: list[tuple[date, float]], d0: date) -> float:
    return sum(cf / (1.0 + rate) ** ((d - d0).days / 365.0) for d, cf in flows)


def _xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    """Bracketed-bisection XIRR. Returns None for degenerate inputs (fewer than
    two flows, or no sign change → no root) rather than guessing."""
    if len(flows) < 2:
        return None
    amounts = [cf for _, cf in flows]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    flows = sorted(flows, key=lambda f: f[0])
    d0 = flows[0][0]
    lo, hi = -0.9999, 1.0
    f_lo = _xnpv(lo, flows, d0)
    f_hi = _xnpv(hi, flows, d0)
    tries = 0
    while f_lo * f_hi > 0 and tries < 200:
        hi *= 2.0
        f_hi = _xnpv(hi, flows, d0)
        tries += 1
        if hi > 1e7:
            return None  # no sign change in a sane range → non-convergent
    if f_lo * f_hi > 0:
        return None

    for _ in range(300):
        mid = (lo + hi) / 2.0
        f_mid = _xnpv(mid, flows, d0)
        if abs(f_mid) < 1e-7 or (hi - lo) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


class PortfolioReturnsFunction(ComputeFunction):
    name = "portfolio_returns"
    description = (
        "Money-weighted return (XIRR) and simple gain for the investment "
        "portfolio, at scope='portfolio' (one row per currency) or "
        "scope='asset-class' (one row per asset-class per currency). XIRR uses the "
        "external cash flows (buys negative, sales and cash dividends positive; "
        "reinvested DRIP nets to zero) plus the terminal market value at 'to'. "
        "XIRR is null when the series is degenerate (a single flow, all one sign, "
        "or non-convergent) — never a fabricated number. simple_gain = market "
        "value - cost basis; simple_gain_pct is a ratio. No cross-currency "
        "conversion. All money and the rate are decimal strings."
    )
    output_shape = (
        "[{group, currency, xirr, simple_gain, simple_gain_pct, market_value, "
        "cost_basis}] — xirr is a decimal-string annual rate (e.g. '0.184000') or "
        "null; the money fields and simple_gain_pct are decimal strings."
    )
    parameters_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["to"],
        "properties": {
            "from": {"type": "string", "description": "Inclusive start, YYYY-MM-DD. Omit to include all history up to 'to'."},
            "to": {"type": "string", "description": "Inclusive end / valuation date, YYYY-MM-DD."},
            "scope": {"type": "string", "enum": ["portfolio", "asset-class"], "description": "Grouping. Default 'portfolio'."},
            "assetClass": {"type": "string", "description": "With scope='asset-class', restrict to this class."},
            "currency": {"type": "string", "description": "Restrict to one quote currency; omit for all."},
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

        scope = args.get("scope") or "portfolio"
        want_currency = args.get("currency")
        # '*' is the dashboard currency-filter's "All" sentinel — treat it as
        # "no restriction" (dev-docs/dashboard-multi-currency.md).
        if want_currency == "*":
            want_currency = None
        want_class = args.get("assetClass")

        asset_class_of = {
            c.code: (c.asset_class or "unclassified")
            for c in self._reader.get_commodities() if not c.is_currency
        }
        holdings = set(asset_class_of)
        if not holdings:
            return []

        # Terminal market value + cost basis at `to`, per group, from the tested
        # value-over-time function (its last sample is exactly `to`).
        series = await PortfolioSeriesFunction(self._reader).execute(
            to=date_to.isoformat(),
            scope=("overall" if scope == "portfolio" else "asset-class"),
            currency=want_currency,
        )
        terminal: dict[tuple[str, str], dict[str, Decimal]] = {}
        for row in series:
            if row["date"] != date_to.isoformat():
                continue
            group = "Portfolio" if scope == "portfolio" else row["group"]
            terminal[(group, row["currency"])] = {
                "market": Decimal(row["market_value"]),
                "cost": Decimal(row["cost_basis"]),
            }

        # External cash flows, grouped by (group, currency).
        legs = self._reader.get_investment_cashflow_postings(list(holdings))
        by_txn: dict[str, list[dict]] = {}
        for leg in legs:
            by_txn.setdefault(leg["transaction_id"], []).append(leg)

        flows: dict[tuple[str, str], list[tuple[date, float]]] = {}
        for tid, tlegs in by_txn.items():
            tdate = date.fromisoformat(tlegs[0]["transaction_date"])
            if tdate > date_to or (date_from and tdate < date_from):
                continue
            # Which asset-classes this transaction touches.
            classes: set[str] = set()
            for leg in tlegs:
                if leg["currency"] in holdings:
                    classes.add(asset_class_of[leg["currency"]])
                elif leg["account"].startswith("Income:Dividends:"):
                    sym = leg["account"].rsplit(":", 1)[-1]
                    if sym in holdings:
                        classes.add(asset_class_of[sym])
            # Net cash (currency) legs in Asset/Liability accounts, per currency.
            cash: dict[str, Decimal] = {}
            for leg in tlegs:
                if (leg["account_type"] in _CASH_ACCOUNT_TYPES
                        and leg["currency"] not in holdings
                        and leg["amount"] is not None):
                    cash[leg["currency"]] = cash.get(leg["currency"], Decimal(0)) + Decimal(leg["amount"])
            for ccy, amt in cash.items():
                if amt == 0:
                    continue
                if want_currency and ccy != want_currency:
                    continue
                groups = (["Portfolio"] if scope == "portfolio"
                          else [c for c in classes] or [])
                for group in groups:
                    flows.setdefault((group, ccy), []).append((tdate, float(amt)))

        # Emit one row per (group, currency) that has a terminal value.
        result: list[dict] = []
        for (group, ccy), term in sorted(terminal.items()):
            if want_class and scope == "asset-class" and group != want_class:
                continue
            market = term["market"]
            cost = term["cost"]
            group_flows = list(flows.get((group, ccy), []))
            # Close the series with the terminal market value as a final inflow.
            if market != 0:
                group_flows.append((date_to, float(market)))
            rate = _xirr(group_flows)
            simple_gain = market - cost
            simple_pct = (simple_gain / cost) if cost != 0 else None
            result.append({
                "group": group,
                "currency": ccy,
                "xirr": (format(rate, ".6f") if rate is not None else None),
                "simple_gain": str(simple_gain),
                "simple_gain_pct": (str(simple_pct) if simple_pct is not None else None),
                "market_value": str(market),
                "cost_basis": str(cost),
            })
        return result
