"""Metadata review — which commodities and accounts the recognized vocabulary can
describe, their current metadata, and which relevant keys are not set.

Every rule here is structural or metadata-driven, never name-based beyond the
root account type (`Assets:` / `Income:`). See dev-docs/investment-view.md §10.3
(Q3) for the rules and metadata_vocabulary.py for the keys.
"""

from typing import Dict, List

from app.core.metadata_vocabulary import (
    CURRENCY_ASSET_CLASSES,
    RESERVED_KEYS,
    SCOPE_ACCOUNT,
    SCOPE_COMMODITY,
    SCOPE_INCOME_ACCOUNT,
    SCOPES,
    field_applies,
    fields_for,
)
from app.schemas.metadata_schemas import MetadataReviewData, MetadataReviewItem
from app.services.sqlite_reader import SqliteReader


def _as_text(metadata: dict) -> Dict[str, str]:
    """User metadata as text — without the parser-stamped filename/lineno the
    mirror carries."""
    return {
        k: str(v) for k, v in metadata.items()
        if v is not None and k not in RESERVED_KEYS
    }


def _unset(scope: str, metadata: Dict[str, str]) -> List[str]:
    return [
        f.key for f in fields_for(scope)
        if f.review and field_applies(f, metadata) and not metadata.get(f.key, "").strip()
    ]


# The one key per scope that the dashboards' main views need. Only these count
# toward the per-scope `summary` (which drives the dashboard banners); refinements
# like maturity_date are listed as "not set" but never prompt a banner.
_PRIMARY_KEY = {
    SCOPE_COMMODITY: "asset-class",
    SCOPE_ACCOUNT: "asset-class",
    SCOPE_INCOME_ACCOUNT: "income-type",
}


def build_review(reader: SqliteReader) -> MetadataReviewData:
    operating = set(reader.get_operating_currencies())
    cost_accounts, cost_commodities = reader.get_cost_held()
    commodities = reader.get_commodities()
    is_currency = {c.code: c.is_currency for c in commodities}
    posting_counts = reader.get_posting_counts()
    used_commodities = {c.code: c.usage.transaction_count for c in commodities}

    items: List[MetadataReviewItem] = []

    # Commodities: every one except the declared operating currencies. Only a
    # likely holding is reviewed for unset keys — one held at cost, or already
    # classed as a holding — and never one the user tagged cash/currency.
    # Without an operating_currency an untagged VOO is classed as a currency,
    # so the cost-held signal is what finds it.
    for c in sorted(commodities, key=lambda c: c.code):
        if c.code in operating:
            continue
        meta = _as_text(c.metadata)
        tagged = meta.get("asset-class", "").strip().lower()
        holding = (
            tagged not in CURRENCY_ASSET_CLASSES
            and (c.code in cost_commodities or not c.is_currency or tagged != "")
        )
        items.append(MetadataReviewItem(
            scope=SCOPE_COMMODITY,
            item=c.code,
            metadata=meta,
            unset=_unset(SCOPE_COMMODITY, meta) if holding else [],
            is_currency=c.is_currency,
        ))

    # Accounts: `Assets:` accounts that hold only currencies (account-level
    # asset-class describes a currency balance), and all `Income:` accounts.
    for a in sorted(reader.get_accounts(), key=lambda a: a.name):
        meta = _as_text(a.metadata)
        root = a.name.split(":", 1)[0]
        if root == "Assets":
            if a.name in cost_accounts:
                continue
            if any(not is_currency.get(c.currency, True) for c in a.currencies):
                continue
            scope = SCOPE_ACCOUNT
        elif root == "Income":
            scope = SCOPE_INCOME_ACCOUNT
        else:
            continue
        items.append(MetadataReviewItem(
            scope=scope,
            item=a.name,
            metadata=meta,
            unset=_unset(scope, meta),
            closed=a.close_date is not None,
        ))

    # The summary counts only items a banner should offer to enrich: open, in
    # use (a commodity or account with postings), and missing its scope's
    # primary key. Placeholder parents, closed accounts and refinements stay
    # listed in the review but don't prompt.
    summary = {s: 0 for s in SCOPES}
    for i in items:
        if _PRIMARY_KEY[i.scope] not in i.unset or i.closed:
            continue
        if i.scope == SCOPE_COMMODITY:
            if used_commodities.get(i.item, 0) == 0:
                continue
        elif posting_counts.get(i.item, 0) == 0:
            continue
        summary[i.scope] += 1

    return MetadataReviewData(
        items=items,
        summary=summary,
        has_operating_currencies=bool(operating),
    )
