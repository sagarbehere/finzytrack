"""Plan → Beancount entries (the commit's new entries; dev-docs/cas-import.md §5, §7).

Every entry is stamped with the import's target file, so the existing per-file write
path places it there (§13.5), and carries ``stmt_import: <id>`` so the import can be
undone as a unit (§9a.2). Commodity metadata is merged into existing directives by the
caller (``LedgerManager``), never overwritten.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost

from app.services.statement_import.plan import PlanResult
from app.services.statement_import.snapshot import META_STMT_IMPORT, RECORD_TYPE


def _posting(p) -> data.Posting:
    cost = Cost(p.cost, p.cost_currency, p.cost_date, None) if p.cost is not None else None
    price = Amount(p.price, p.price_currency) if p.price is not None else None
    return data.Posting(p.account, Amount(p.units, p.currency), cost, price, None, None)


def build_entries(
    plan: PlanResult,
    *,
    import_id: str,
    today: date,
    make_transaction: Callable[..., data.Transaction],
    source: str,
    period_start: date,
    period_end: date,
    choices: Dict[str, str],
) -> List[Any]:
    """New entries for a plan: transactions, balance assertions and the import record.

    ``make_transaction(date, payee, narration, postings, source_account, additional_meta)``
    is the ledger's own constructor (ids + content hash). New commodities are created by
    the caller so existing directives can be merged in place.
    """
    target = plan.target_file
    entries: List[Any] = []
    for t in plan.transactions:
        postings = [_posting(p) for p in t.postings]
        source_account = next((p.account for p in t.postings if p.cost is not None), t.postings[0].account)
        txn = make_transaction(t.date, t.payee, t.narration, postings, source_account,
                               {**t.meta, META_STMT_IMPORT: import_id})
        entries.append(txn._replace(meta={**txn.meta, "filename": target, "lineno": 0}))
    for b in plan.balances:
        meta = {"filename": target, "lineno": 0, META_STMT_IMPORT: import_id}
        entries.append(data.Balance(meta, b.date, b.account, Amount(b.units, b.commodity), b.tolerance, None))
    record_meta: Dict[str, Any] = {
        "filename": target, "lineno": 0,
        "source": source,
        "period_start": period_start,
        "period_end": period_end,
        "transactions": str(len(plan.transactions)),
        **choices,
    }
    entries.append(data.Custom(record_meta, today, RECORD_TYPE, [(import_id, str)]))
    return entries


def record_choices(mapping) -> Dict[str, str]:
    """The account choices to remember on the import record, so the next import can
    pre-select the user's own earlier choice for each role (§5.1)."""
    out: Dict[str, str] = {}
    # One key per distributor and one per account: an account name that is a whole
    # metadata value is updated when the account is renamed.
    for i, (distributor, account) in enumerate(sorted(mapping.holding_accounts.items()), start=1):
        out[f"holding-{i}-distributor"] = distributor
        out[f"holding-{i}-account"] = account
    for key in ("cash_fallback_account", "cash_matching_account", "dividend_account", "taxes_account", "gains_account"):
        value = getattr(mapping, key)
        if value:
            out[key] = value
    return out
