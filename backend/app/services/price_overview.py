"""Price overview and history for the Investments → Prices tab.

One row per ``(commodity, quote currency)`` that has prices or is held, with how
the fetcher prices it, its latest price and whether that price is out of date.
Cadence, held units and out-of-date come from ``price_cadence`` — the same rules
the fetcher uses — so the tab and the fetcher can't disagree. See
dev-docs/investment-view.md §10.2 (P-Q3…P-Q5).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from app.core.ledger_loader import sidecar_path
from app.core.metadata_vocabulary import CURRENCY_ASSET_CLASSES
from app.schemas.price_schemas import (
    LatestPrice,
    PriceHistoryData,
    PriceOverviewData,
    PriceOverviewRow,
    PricePoint,
)
from app.services.price_cadence import (
    CADENCE_COMPUTED,
    CADENCE_NEVER,
    cadence_for,
    fetch_quote,
    holdings_activity,
    is_out_of_date,
    issue_month,
)
from app.services.sqlite_reader import SqliteReader


def _source(filename: Optional[str], sidecar: Path) -> str:
    """'sidecar' when the price was read from the price file, else 'ledger'."""
    if filename and Path(filename).resolve() == sidecar:
        return "sidecar"
    return "ledger"


def _not_fetched_reason(
    cadence: str, asset_class: str, is_currency: bool, operating: set, cost_held: bool,
    metadata: dict,
) -> Optional[str]:
    if cadence == CADENCE_COMPUTED:
        return None if issue_month(metadata.get("issue_date")) else "Needs an issue month"
    if cadence != CADENCE_NEVER:
        return None
    if asset_class == "money-market":
        return "Never fetched (money market)"
    if asset_class in CURRENCY_ASSET_CLASSES:
        return f"Never fetched ({asset_class})"
    # Bought at cost but classed as a currency: an untagged holding in a ledger
    # with no operating_currency. A real currency (an FX price) isn't told this.
    if is_currency and cost_held and not operating and not asset_class:
        return "Counts as cash — add an asset class to fetch prices"
    return "Never fetched (currency)"


def build_overview(reader: SqliteReader, ledger_file: str, today: date) -> PriceOverviewData:
    sidecar = sidecar_path(ledger_file)
    operating = set(reader.get_operating_currencies())
    _cost_accounts, cost_commodities = reader.get_cost_held()
    commodities = {c.code: c for c in reader.get_commodities()}
    latest = reader.get_latest_prices()
    priced = {r["base_currency"] for r in latest}

    # Likely holdings (as in the metadata review): held at cost, classed as a
    # holding, or tagged with a non-currency class — never an operating currency
    # or a commodity tagged cash/currency. Without an operating_currency an
    # untagged VOO is classed as a currency, so cost-held is what finds it.
    def likely_holding(code: str) -> bool:
        c = commodities.get(code)
        if c is None or code in operating:
            return False
        tagged = (c.asset_class or "").strip().lower()
        if tagged in CURRENCY_ASSET_CLASSES:
            return False
        return code in cost_commodities or not c.is_currency or tagged != ""

    candidates = sorted(priced | {code for code in commodities if likely_holding(code)})
    activity = holdings_activity(reader, candidates)
    held = {code for code, a in activity.items() if a.held}

    rows: List[PriceOverviewRow] = []

    def add_row(code: str, quote: Optional[str], lp: Optional[dict], fetched_in: Optional[str]) -> None:
        c = commodities.get(code)
        asset_class = c.asset_class if c else None
        ac = (asset_class or "").strip().lower()
        is_currency = c.is_currency if c else True
        cadence = cadence_for(asset_class, is_currency=is_currency)
        reason = _not_fetched_reason(
            cadence, ac, is_currency, operating, code in cost_commodities,
            (c.metadata if c else {}) or {},
        )
        if c is None:
            # Only priced, never in a transaction: the fetcher has nothing to fetch.
            reason = "Not used in any transaction"
        if cadence != CADENCE_NEVER and quote != fetched_in:
            # A price typed in another currency: the fetcher never updates it.
            cadence, reason = CADENCE_NEVER, f"Fetched in {fetched_in}" if fetched_in else None
        latest_price = None
        age = None
        if lp is not None:
            d = date.fromisoformat(lp["date"])
            latest_price = LatestPrice(
                date=d, number=Decimal(lp["quote_number"]),
                source=_source(lp["filename"], sidecar),
            )
            age = (today - d).days
        is_held = code in held
        rows.append(PriceOverviewRow(
            commodity=code,
            quote_currency=quote,
            asset_class=asset_class,
            cadence=cadence,
            held=is_held,
            latest=latest_price,
            age_days=age,
            count=lp["count"] if lp else 0,
            # An I-Bond without an issue month is never computed, so (like any
            # holding that isn't fetched) it is never out of date.
            out_of_date=(cadence != CADENCE_COMPUTED or reason is None) and is_out_of_date(
                cadence, is_held, latest_price.date if latest_price else None, today,
            ),
            not_fetched_reason=reason,
        ))

    by_base: Dict[str, Dict[str, dict]] = {}
    for r in latest:
        by_base.setdefault(r["base_currency"], {})[r["quote_currency"]] = r

    for code in candidates:
        pairs = by_base.get(code, {})
        act = activity.get(code)
        fetched_in = fetch_quote(
            act.cost_currency if act else None, {q: r["count"] for q, r in pairs.items()},
        )
        for quote in sorted(pairs):
            add_row(code, quote, pairs[quote], fetched_in)
        # A held commodity with no price in the currency it is fetched in still
        # gets that row, so the missing series shows (as "No price", out of date).
        if code in held and fetched_in not in pairs:
            add_row(code, fetched_in, None, fetched_in)

    return PriceOverviewData(rows=rows, today=today)


def build_history(
    reader: SqliteReader, ledger_file: str, commodity: str, quote: str,
    limit: int, offset: int,
) -> PriceHistoryData:
    sidecar = sidecar_path(ledger_file)
    rows, total = reader.get_price_history(commodity, quote, limit, offset)
    return PriceHistoryData(
        prices=[
            PricePoint(
                date=date.fromisoformat(r["date"]),
                number=Decimal(r["quote_number"]),
                quote_currency=r["quote_currency"],
                source=_source(r["filename"], sidecar),
            )
            for r in rows
        ],
        total=total,
    )
