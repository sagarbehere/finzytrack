"""budget_for_range — the Fava-style budget resolver as a compute function.

The single source of budget math (dev-docs/budget.md §6.1). Reads `custom
"budget"` directives from the read-only mirror and computes the full-precision
daily-equivalent over a range (or per calendar period). The /api/budgets CRUD
read path calls the same resolver core (budget_resolver.resolve_budgets).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.compute.base import ComputeFunction
from app.compute.budget_resolver import parse_budget_directives, resolve_budgets
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)


class BudgetForRangeFunction(ComputeFunction):
    name = "budget_for_range"
    description = (
        "Resolve budgets from `custom \"budget\"` directives over a date range. "
        "Returns one row per budgeted (account, currency) with the full-precision "
        "daily-equivalent total; with groupBy='period' returns a per-calendar-month "
        "series (for envelope rollover). Money values are decimal strings."
    )
    output_shape = (
        "range mode: [{account, currency, budget}]; "
        "period mode: [{account, currency, period: 'YYYY-MM', budget}]"
    )
    parameters_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["to"],
        "properties": {
            "from": {"type": "string", "description": "Inclusive range start, YYYY-MM-DD. Omit to start each account at its own inception (its first budget directive) — the natural 'from the beginning' for envelope balances."},
            "to": {"type": "string", "description": "Inclusive range end, YYYY-MM-DD."},
            "currency": {"type": "string", "description": "Restrict to one currency; omit for all."},
            "account": {"type": "string", "description": "Restrict to one account; omit for all budgeted accounts."},
            "groupBy": {"type": "string", "enum": ["period"], "description": "'period' → per-calendar-month series."},
        },
    }

    def __init__(self, reader: SqliteReader) -> None:
        self._reader = reader

    async def execute(self, **args: Any) -> list[dict]:
        try:
            date_to = date.fromisoformat(str(args["to"]))
            raw_from = args.get("from")
            # Omitted/empty `from` → None → each account starts at its inception.
            date_from = date.fromisoformat(str(raw_from)) if raw_from else None
        except (ValueError, KeyError) as e:
            raise ValueError(f"'to' (and 'from' if given) must be YYYY-MM-DD dates: {e}")

        rows = self._reader.get_custom_directives("budget")
        directives = parse_budget_directives(rows)
        result, warnings = resolve_budgets(
            directives,
            date_from,
            date_to,
            currency=args.get("currency"),
            account=args.get("account"),
            group_by=args.get("groupBy"),
        )
        for w in warnings:
            logger.warning("budget_for_range: %s", w)
        return result
