"""cash_instruments — cash & fixed-income accounts with attributed interest + yield.

For each currency-balance Asset account (checking, savings, CD, cash-modelled
bond, money-market) this returns the row the **Cash & Deposits** dashboard shows:
balance, type, rate, maturity, **interest earned**, and **effective yield** — plus
per-currency **unattributed interest** rows.

The subtle part is **interest attribution** — tying each interest amount to the
instrument that earned it (dev-docs/metadata-conventions.md, "Interest attribution").
What counts as interest is decided by ``income-type: "interest"`` metadata on the
income account (never by an ``Income:Interest`` name). Per interest posting, in order:

1. **Linked income account** (``interest_account`` on the instrument's open, 1:1) →
   that instrument, wherever the cash was deposited (handles *paid-out* interest).
2. **Structural counterpart** (no link): the investment-instrument asset legs in the
   same transaction — exactly one → it (compounding); more than one → the one being
   **reduced** (net-negative, i.e. matured/closed).
3. **Guardrail**: otherwise **unattributed** — surfaced, still in income totals,
   never dropped onto whichever account received the cash.

Everything degrades gracefully: with no metadata, compounded/matured interest still
attributes; only paid-out interest goes unattributed (blank yield).

**Effective yield** = interest ÷ **time-weighted average balance** over the
account's active life (open → min(as-of, close)), annualized. The average balance
is the one denominator that's correct across booking styles — compounded interest
(balance grows), paid-out interest (balance flat), and maturity (account closes) —
unlike a "principal" that's ambiguous once interest is paid out or the account is
closed. Money is decimal strings in and out (money-types.md); the yield ratio is
the one derived float, returned as a decimal string.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.compute.base import ComputeFunction
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)

# Transactional cash — never an interest *earner* for attribution. Interest that
# lands in one of these (a CD paying out to checking, incidental checking interest)
# is a destination/incidental, not a tracked instrument's yield → unattributed
# unless a linked `interest_account` says otherwise.
_CASH_CLASSES = {"cash", "currency", "checking"}


def _avg_balance_and_days(
    events: list[tuple[date, Decimal]], end: date
) -> tuple[Decimal, int]:
    """Time-weighted average balance and the day-span, from ``(date, delta)`` events
    over ``[first event, end]``. Each segment is weighted by the days it held, so the
    yield denominator is the capital that was actually earning — correct whether
    interest compounded into the balance, was paid out (balance flat), or the account
    matured (``end`` clamped to its close)."""
    ev = sorted((d, delta) for d, delta in events if d <= end)
    if not ev:
        return (Decimal(0), 0)
    start = ev[0][0]
    total_days = (end - start).days
    if total_days <= 0:
        return (Decimal(0), 0)
    running = Decimal(0)
    weighted = Decimal(0)
    prev = start
    for d, delta in ev:
        if d > prev:
            weighted += running * Decimal((d - prev).days)
            prev = d
        running += delta
    if end > prev:
        weighted += running * Decimal((end - prev).days)
    return (weighted / Decimal(total_days), total_days)


class CashInstrumentsFunction(ComputeFunction):
    name = "cash_instruments"
    description = (
        "Cash & fixed-income accounts (checking/savings/CD/bond/money-market) with "
        "current balance, type (account asset-class), rate, maturity, attributed "
        "interest earned, and effective yield as of 'to'. Interest is attributed to "
        "the instrument that earned it: by the instrument's interest_account link, "
        "else by the investment counterpart in the interest transaction (the "
        "reduced/closed leg at maturity); interest that can't be attributed is "
        "returned as unattributed rows keyed by the income account that booked it "
        "(degraded=true), never misattributed. "
        "Effective yield = interest / time-weighted average balance over the "
        "account's active life, annualized; null when it can't be computed. No "
        "cross-currency conversion. Money values are decimal strings."
    )
    output_shape = (
        "[{account, filter_account, type, currency, rate, maturity, balance, "
        "interest_earned, effective_yield, degraded}] — one row per currency-balance "
        "Asset account (filter_account = account), plus one row per (income account, "
        "currency) with unattributed interest (degraded=true; account/filter_account "
        "= that income account, so it drills to those postings). "
        "balance/interest_earned are decimal strings or "
        "null; effective_yield is a decimal ratio string (e.g. '0.045') or null; "
        "rate/maturity are the recorded metadata strings or null."
    )
    parameters_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["to"],
        "properties": {
            "to": {"type": "string", "description": "Inclusive as-of date, YYYY-MM-DD."},
            "currency": {"type": "string", "description": "Restrict to one currency; omit or '*' for all."},
        },
    }

    def __init__(self, reader: SqliteReader) -> None:
        self._reader = reader

    async def execute(self, **args: Any) -> list[dict]:
        try:
            to_date = date.fromisoformat(str(args["to"]))
        except (ValueError, KeyError) as e:
            raise ValueError(f"'to' must be a YYYY-MM-DD date: {e}")
        want_ccy = args.get("currency")
        if want_ccy == "*":
            want_ccy = None

        currency_codes = {c.code for c in self._reader.get_commodities() if c.is_currency}
        accounts = self._reader.get_accounts()

        # Per-account metadata + the income-account → instrument link, plus the set of
        # income accounts that record interest — identified by `income-type: "interest"`
        # metadata (dev-docs/metadata-conventions.md), NEVER by an `Income:Interest`
        # name. Untagged income is not interest and never enters attribution.
        meta: dict[str, dict] = {}
        income_link: dict[str, str] = {}
        interest_income_accounts: set[str] = set()
        for a in accounts:
            ac = str(a.metadata.get("asset-class") or "").strip().lower() or None
            if str(a.metadata.get("income-type") or "").strip().lower() == "interest":
                interest_income_accounts.add(a.name)
            link = a.metadata.get("interest_account")
            if link:
                income_link[str(link)] = a.name
            meta[a.name] = {
                "asset_class": ac,
                "open_date": a.open_date,
                "close_date": a.close_date,
                "rate": a.metadata.get("interest_rate"),
                "maturity": a.metadata.get("maturity_date"),
                "balances": {cc.currency: cc.balance for cc in a.currencies},
            }

        def is_investment(name: str) -> bool:
            m = meta.get(name)
            ac = m["asset_class"] if m else None
            return ac is not None and ac not in _CASH_CLASSES

        # ── Interest attribution ─────────────────────────────────────────────
        legs = self._reader.get_transaction_legs_touching(sorted(interest_income_accounts))
        by_txn: dict[str, list[dict]] = defaultdict(list)
        for leg in legs:
            by_txn[leg["transaction_id"]].append(leg)

        interest: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        # Unattributed interest is keyed by the (income account, currency) that
        # booked it. The earning *instrument* is unknown — that's what makes it
        # unattributed — but the income account is not, so we keep it: it lets the
        # dashboard drill into those postings and names where to add an
        # `interest_account` link.
        unattributed: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        for tlegs in by_txn.values():
            for leg in tlegs:
                if leg["account"] not in interest_income_accounts:
                    continue
                if leg["amount"] is None:
                    continue
                if date.fromisoformat(leg["transaction_date"]) > to_date:
                    continue
                earned = -Decimal(leg["amount"])  # income is negative → earned positive
                if earned == 0:
                    continue
                ccy = leg["currency"]

                instrument = income_link.get(leg["account"])
                if instrument is None:
                    inv = [
                        l for l in tlegs
                        if l["account_type"] == "Assets"
                        and l["currency"] == ccy
                        and is_investment(l["account"])
                    ]
                    names = {l["account"] for l in inv}
                    if len(names) == 1:
                        instrument = next(iter(names))
                    elif len(names) > 1:
                        reduced = {
                            l["account"] for l in inv
                            if l["amount"] is not None and Decimal(l["amount"]) < 0
                        }
                        instrument = next(iter(reduced)) if len(reduced) == 1 else None

                if instrument is not None:
                    interest[(instrument, ccy)] += earned
                else:
                    unattributed[(leg["account"], ccy)] += earned

        # ── Balance timelines for interest earners (for average-balance yield) ──
        earners = sorted({acct for (acct, _c) in interest})
        events: dict[tuple[str, str], list[tuple[date, Decimal]]] = defaultdict(list)
        if earners:
            for p in self._reader.get_postings_by_account(earners):
                if p["amount"] is None:
                    continue
                events[(p["account"], p["currency"])].append(
                    (date.fromisoformat(p["transaction_date"]), Decimal(p["amount"]))
                )

        # ── Build one row per currency-balance Asset account ─────────────────
        # Union of accounts that currently hold a currency balance and accounts that
        # earned interest — so a matured/closed instrument (balance now 0, no row in
        # account_balances) still surfaces its attributed interest and yield.
        targets: set[tuple[str, str]] = set()
        for name, m in meta.items():
            if not name.startswith("Assets:"):
                continue
            for ccy in m["balances"]:
                if ccy in currency_codes:
                    targets.add((name, ccy))
        for (name, ccy) in interest:
            if name.startswith("Assets:"):
                targets.add((name, ccy))

        result: list[dict] = []
        for name, ccy in targets:
            if want_ccy and ccy != want_ccy:
                continue
            m = meta[name]
            bal = m["balances"].get(ccy, Decimal(0))
            ie = interest.get((name, ccy))
            if bal == 0 and ie is None:
                continue

            eff_yield: Optional[Decimal] = None
            if ie is not None and ie != 0:
                end = min(to_date, m["close_date"]) if m["close_date"] else to_date
                avg, days = _avg_balance_and_days(events.get((name, ccy), []), end)
                if avg > 0 and days > 0:
                    eff_yield = (ie / avg) * Decimal(365) / Decimal(days)

            result.append({
                "account": name,
                "filter_account": name,   # drives the row's transactions drill-through
                "type": m["asset_class"] or "cash",
                "currency": ccy,
                "rate": m["rate"],
                "maturity": m["maturity"],
                "balance": str(bal),
                "interest_earned": (str(ie) if ie is not None else None),
                "effective_yield": (format(eff_yield, "f") if eff_yield is not None else None),
                "degraded": False,
            })

        result.sort(key=lambda r: (r["currency"], r["type"], r["account"]))

        # Surface unattributed interest as one row per (income account, currency):
        # `account`/`filter_account` = the income account that booked it, so the row
        # drills into those transactions and names where a link would fix the gap.
        # degraded=true marks them — the KPI sums them by currency, the "by account"
        # list shows them, and the accounts table filters them out.
        for (inc_acct, ccy), amt in sorted(unattributed.items()):
            if amt == 0 or (want_ccy and ccy != want_ccy):
                continue
            result.append({
                "account": inc_acct,
                "filter_account": inc_acct,   # drills to that income account's postings
                "type": "—",
                "currency": ccy,
                "rate": None,
                "maturity": None,
                "balance": None,
                "interest_earned": str(amt),
                "effective_yield": None,
                "degraded": True,
            })

        return result
