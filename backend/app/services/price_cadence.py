"""price_cadence — the price rules shared by the fetcher and the Prices tab.

One definition of *how often* a commodity is priced (from its ``asset-class``),
*whether it is still held* (net units from postings), and *when its latest price
is out of date*, so the fetcher and ``GET /api/prices/overview`` can't disagree
(dev-docs/investment-view.md §10.2 P-Q3, dev-docs/valuations.md §4).
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping, NamedTuple, Optional

from app.core.metadata_vocabulary import CURRENCY_ASSET_CLASSES

CADENCE_DAILY = "daily"
CADENCE_MONTHLY = "monthly"
CADENCE_COMPUTED = "computed"   # I-Bonds: valued by formula, not fetched
CADENCE_NEVER = "never"

# asset-class → cadence. Anything unlisted defaults to daily.
NEVER_FETCHED_CLASSES = CURRENCY_ASSET_CLASSES | {"money-market"}
MONTHLY_CLASSES = frozenset({"bond", "cd"})
IBOND_CLASS = "i-bond"

# A held holding's latest price is out of date when it is older than this.
# Daily covers weekends and holidays; monthly/computed cover one missed month.
STALE_AFTER_DAYS = {
    CADENCE_DAILY: 7,
    CADENCE_MONTHLY: 45,
    CADENCE_COMPUTED: 45,
}


def cadence_for(asset_class: Optional[str], is_currency: bool = False) -> str:
    """The fetch cadence of a commodity. A commodity in the currency role is
    never fetched, whatever its asset-class (the fetcher prices holdings only)."""
    if is_currency:
        return CADENCE_NEVER
    ac = (asset_class or "").strip().lower()
    if ac == IBOND_CLASS:
        return CADENCE_COMPUTED
    if ac in NEVER_FETCHED_CLASSES:
        return CADENCE_NEVER
    if ac in MONTHLY_CLASSES:
        return CADENCE_MONTHLY
    return CADENCE_DAILY


def issue_month(raw: Any) -> Optional[str]:
    """Normalise an I-Bond ``issue_date`` metadata value to ibonds' ``MM/YYYY``.
    Accepts ``MM/YYYY``, ``YYYY-MM-DD``, or ``YYYY-MM``; ``None`` if unusable."""
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


class Activity(NamedTuple):
    """A commodity's ownership window from its postings, and the currency it
    was most often bought in (``None`` if never held at cost)."""
    first: date
    last: date
    net_units: Decimal
    cost_currency: Optional[str] = None

    @property
    def held(self) -> bool:
        return self.net_units > 0


def holdings_activity(reader, codes: Iterable[str]) -> Dict[str, Activity]:
    """``{code: Activity}`` for each of ``codes`` that has postings. Codes with
    no postings are absent."""
    acc: Dict[str, list] = {}
    cost: Dict[str, Counter] = {}
    for row in reader.get_postings_by_currency(list(codes)):
        code = row["currency"]
        d = date.fromisoformat(row["transaction_date"])
        u = Decimal(row["amount"]) if row["amount"] is not None else Decimal(0)
        if row.get("cost_currency"):
            cost.setdefault(code, Counter())[row["cost_currency"]] += 1
        m = acc.get(code)
        if m is None:
            acc[code] = [d, d, u]
        else:
            m[0] = min(m[0], d)
            m[1] = max(m[1], d)
            m[2] += u
    return {
        code: Activity(*v, cost[code].most_common(1)[0][0] if code in cost else None)
        for code, v in acc.items()
    }


def fetch_quote(cost_currency: Optional[str], price_counts: Mapping[str, int]) -> Optional[str]:
    """The quote currency the fetcher keeps a commodity's price series in: the
    currency it was bought in, else the quote with the most prices. Only this
    series sets the fetch window and can be out of date — a price typed in
    another currency is never fetched, so it must not stop or start a fetch."""
    if cost_currency:
        return cost_currency
    if not price_counts:
        return None
    return max(sorted(price_counts), key=lambda q: price_counts[q])


def is_out_of_date(cadence: str, held: bool, latest: Optional[date], today: date) -> bool:
    """True when a still-held, priced-by-cadence holding needs a newer price:
    its latest price is older than the cadence allows, or it has none. Sold-out
    and never-fetched holdings are never out of date."""
    limit = STALE_AFTER_DAYS.get(cadence)
    if limit is None or not held:
        return False
    if latest is None:
        return True
    return (today - latest).days > limit
