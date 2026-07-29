"""
Shared categorization core.

Turns a list of transactions into per-id account suggestions, independent of
duplicate detection. Both callers use it:

- POST /api/import/categorize — categorize *new* (imported) transactions, then
  additionally run duplicate detection (in the import router).
- POST /api/ledger/categorize — categorize *existing* ledger transactions
  (resolve Expenses:Unknown), with NO duplicate detection.

Splitting categorization from duplicate detection keeps each responsibility
honest and lets the bulk/existing caller opt out of dedup. The import path is
behaviour-preserving: it passes one source_account for the whole batch, which
collapses to a single AI group — identical to the pre-refactor single call.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from app.config import CategorizationEngine, LLMConfig
from app.services.categorizer import initialize_classifier, categorize_transaction
from app.services.ai_categorizer import categorize_transactions_ai

logger = logging.getLogger(__name__)

# id -> (suggested_account, confidence)
CategorizationMap = Dict[str, Tuple[str, Optional[float]]]


class CategorizationInput:
    """One transaction to categorize. source_account is AI prompt context only."""

    __slots__ = ("id", "payee", "memo", "narration", "source_account")

    def __init__(self, id: str, payee: str, memo: Optional[str], narration: str, source_account: str):
        self.id = id
        self.payee = payee
        self.memo = memo
        self.narration = narration
        self.source_account = source_account


def _description(txn: CategorizationInput) -> str:
    parts = [txn.payee]
    if txn.memo:
        parts.append(txn.memo)
    if txn.narration:
        parts.append(txn.narration)
    return " ".join(parts)


def run_categorization(
    transactions: List[CategorizationInput],
    engine: CategorizationEngine,
    account_names: Set[str],
    training_data: list,
    default_account: str,
    llm_config: LLMConfig,
) -> Tuple[CategorizationMap, str, List[str], Optional[str]]:
    """
    Categorize `transactions` with the resolved `engine`.

    The caller is responsible for engine resolution (force_engine vs config), the
    "categorization disabled" short-circuit, and the AI-not-configured check —
    this function assumes `engine` is AI or CLASSIFIER.

    Returns (map, engine_used, warnings, ml_training_info). May raise
    AICategorizeError (from the AI engine); callers translate it to an APIError.
    """
    warnings: List[str] = []
    categorization_map: CategorizationMap = {}

    if engine == CategorizationEngine.AI:
        # Group by source_account so each AI batch carries the right "money came
        # from X" context. Import sends one source for all → one group.
        groups: Dict[str, List[CategorizationInput]] = {}
        for txn in transactions:
            groups.setdefault(txn.source_account, []).append(txn)

        for source_account, group in groups.items():
            txn_dicts = [
                {"id": t.id, "payee": t.payee, "memo": t.memo or "", "narration": t.narration or ""}
                for t in group
            ]
            ai_results, ai_warnings = categorize_transactions_ai(
                transactions=txn_dicts,
                account_names=account_names,
                default_account=default_account,
                source_account=source_account,
                llm_config=llm_config,
            )
            warnings.extend(ai_warnings)
            for t in group:
                categorization_map[t.id] = (ai_results.get(t.id, default_account), None)

        return categorization_map, "ai", warnings, None

    # Classifier engine (source_account is irrelevant here).
    classifier, ml_warning = initialize_classifier(training_data=training_data, ml_enabled=True)
    if classifier:
        for txn in transactions:
            suggested_category, confidence = categorize_transaction(
                _description(txn), classifier, default_account
            )
            categorization_map[txn.id] = (suggested_category, confidence)
        return categorization_map, "classifier", warnings, None

    # Insufficient training data → everything gets the default account.
    if ml_warning:
        warnings.append(ml_warning)
    for txn in transactions:
        categorization_map[txn.id] = (default_account, None)
    return categorization_map, "default", warnings, ml_warning
