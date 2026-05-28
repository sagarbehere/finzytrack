"""
AI-based transaction categorization service.

Uses the configured LLM to categorize transactions by matching payees
to the user's chart of accounts. Intended for cold-start scenarios where
the ML classifier has insufficient training data.

LLM routing goes through ``ai.client.complete_chat_sync`` so this site
benefits from the canonical provider abstraction: Finzytrack AI proxy
support, ``extra_request_body`` filtering, prompt caching, and the same
timeout/retry policy as the streaming assistant.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set, Tuple

from app.ai.client import complete_chat_sync
from app.config import LLMConfig

logger = logging.getLogger(__name__)

# Max transactions per LLM batch call
BATCH_SIZE = 20

# Max validation retries when LLM returns non-existent accounts
MAX_VALIDATION_RETRIES = 2

_PROMPTS_DIR = Path(__file__).parents[2] / "resources" / "prompts"


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    path = _PROMPTS_DIR / "categorize_transactions.md"
    if not path.is_file():
        raise FileNotFoundError(f"Categorization prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class AICategorizeError(Exception):
    """Raised when AI categorization fails."""
    pass


def _build_user_message(
    transactions: List[Dict[str, str]],
    accounts: List[str],
    default_account: str,
    source_account: str,
) -> str:
    """Build the user message for the LLM with transactions and account list."""
    accounts_text = "\n".join(f"- {a}" for a in sorted(accounts))
    txn_lines = []
    for txn in transactions:
        parts = [txn["payee"]]
        if txn.get("memo"):
            parts.append(txn["memo"])
        if txn.get("narration"):
            parts.append(txn["narration"])
        description = " | ".join(parts)
        txn_lines.append(f'  {{"id": "{txn["id"]}", "description": "{description}"}}')

    txn_text = "[\n" + ",\n".join(txn_lines) + "\n]"

    return (
        f"Source account: {source_account}\n\n"
        f"Available category accounts:\n{accounts_text}\n\n"
        f"Default account (use when no good match): {default_account}\n\n"
        f"Transactions to categorize:\n{txn_text}"
    )


def _call_llm(llm_config: LLMConfig, user_message: str) -> str:
    """Send the categorisation prompt through the canonical sync LLM client."""
    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": user_message},
    ]
    try:
        content = complete_chat_sync(llm_config, messages)
    except Exception as e:
        raise AICategorizeError(f"LLM request failed: {e}")
    if not content:
        raise AICategorizeError("LLM returned empty response")
    return content


def _parse_llm_response(content: str) -> List[Dict[str, str]]:
    """Parse the LLM JSON response, stripping markdown fences if present."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        content = content.rstrip("`").strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        raise AICategorizeError(f"LLM returned invalid JSON: {e}. Content: {content[:300]}")

    if not isinstance(result, list):
        raise AICategorizeError(f"LLM response is not a JSON array: {type(result)}")

    return result


def _validate_accounts(
    results: List[Dict[str, str]],
    valid_accounts: Set[str],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """
    Split results into valid and invalid based on account existence.

    Returns:
        (valid_results, invalid_results)
    """
    valid = []
    invalid = []
    for item in results:
        if item.get("account") in valid_accounts:
            valid.append(item)
        else:
            invalid.append(item)
    return valid, invalid


def _build_retry_message(
    invalid_results: List[Dict[str, str]],
    accounts: List[str],
    default_account: str,
) -> str:
    """Build a retry prompt for transactions with invalid accounts."""
    bad_lines = []
    for item in invalid_results:
        bad_lines.append(f'  id={item.get("id")}: you assigned "{item.get("account")}" which does not exist')

    accounts_text = "\n".join(f"- {a}" for a in sorted(accounts))

    return (
        "Some accounts you assigned do NOT exist in the user's ledger:\n"
        + "\n".join(bad_lines) + "\n\n"
        "Here are the ONLY valid accounts:\n" + accounts_text + "\n\n"
        f"Default account (use when no good match): {default_account}\n\n"
        "Please re-categorize ONLY the transactions listed above. "
        "Return a JSON array of objects with \"id\" and \"account\" fields. "
        "No markdown. No explanation."
    )


def categorize_batch(
    transactions: List[Dict[str, str]],
    accounts: List[str],
    default_account: str,
    source_account: str,
    llm_config: LLMConfig,
    valid_accounts: Set[str],
) -> Tuple[Dict[str, str], List[str]]:
    """
    Categorize a batch of transactions using the LLM.

    Deduplicates by payee to minimize tokens, then fans out results.

    Args:
        transactions: List of dicts with id, payee, memo, narration
        accounts: List of valid account names for the prompt
        default_account: Fallback account
        source_account: Source account name for context
        llm_config: LLM configuration
        valid_accounts: Set of all valid account names for validation

    Returns:
        Tuple of:
        - Dict mapping transaction id -> account
        - List of warning messages
    """
    warnings: List[str] = []

    # Deduplicate by payee+memo+narration to reduce LLM tokens
    seen_descriptions: Dict[str, str] = {}  # description -> synthetic_id
    deduped_txns: List[Dict[str, str]] = []
    txn_to_dedup_id: Dict[str, str] = {}  # original txn id -> synthetic_id

    for txn in transactions:
        parts = [txn["payee"]]
        if txn.get("memo"):
            parts.append(txn["memo"])
        if txn.get("narration"):
            parts.append(txn["narration"])
        desc_key = " | ".join(parts)

        if desc_key in seen_descriptions:
            txn_to_dedup_id[txn["id"]] = seen_descriptions[desc_key]
        else:
            synthetic_id = f"txn_{len(deduped_txns)}"
            seen_descriptions[desc_key] = synthetic_id
            txn_to_dedup_id[txn["id"]] = synthetic_id
            deduped_txns.append({
                "id": synthetic_id,
                "payee": txn["payee"],
                "memo": txn.get("memo", ""),
                "narration": txn.get("narration", ""),
            })

    if not deduped_txns:
        return {}, warnings

    # Filter accounts to Expenses:* and Income:* for the prompt (keep full set for validation)
    from app.core.constants import INCOME_STATEMENT_PREFIXES
    categorization_accounts = [a for a in accounts if a.startswith(INCOME_STATEMENT_PREFIXES)]
    if not categorization_accounts:
        categorization_accounts = accounts

    logger.info(
        f"AI categorize batch: {len(transactions)} transactions "
        f"({len(deduped_txns)} unique), {len(categorization_accounts)} accounts in prompt"
    )

    # Call LLM
    user_message = _build_user_message(deduped_txns, categorization_accounts, default_account, source_account)
    logger.debug(f"AI categorize prompt length: {len(user_message)} chars")
    content = _call_llm(llm_config, user_message)
    results = _parse_llm_response(content)
    logger.info(f"AI categorize batch complete: {len(results)} results returned")

    # Validate accounts and retry if needed
    valid_results, invalid_results = _validate_accounts(results, valid_accounts)

    retry_count = 0
    while invalid_results and retry_count < MAX_VALIDATION_RETRIES:
        retry_count += 1
        logger.warning(
            f"AI categorization retry {retry_count}: {len(invalid_results)} invalid accounts"
        )

        retry_message = _build_retry_message(invalid_results, categorization_accounts, default_account)
        retry_content = _call_llm(llm_config, retry_message)
        retry_results = _parse_llm_response(retry_content)

        newly_valid, still_invalid = _validate_accounts(retry_results, valid_accounts)
        valid_results.extend(newly_valid)
        invalid_results = still_invalid

    # If still invalid after retries, assign default and warn
    if invalid_results:
        bad_accounts = list({item.get("account", "???") for item in invalid_results})
        warnings.append(
            f"AI suggested {len(invalid_results)} non-existent account(s) "
            f"({', '.join(bad_accounts[:5])}). These were assigned to {default_account}."
        )
        for item in invalid_results:
            item["account"] = default_account
            valid_results.append(item)

    # Build dedup_id -> account mapping
    dedup_mapping: Dict[str, str] = {}
    for item in valid_results:
        dedup_mapping[item["id"]] = item["account"]

    # Fan out to original transaction ids
    result_mapping: Dict[str, str] = {}
    for txn_id, dedup_id in txn_to_dedup_id.items():
        result_mapping[txn_id] = dedup_mapping.get(dedup_id, default_account)

    return result_mapping, warnings


def categorize_transactions_ai(
    transactions: List[Dict[str, str]],
    account_names: Set[str],
    default_account: str,
    source_account: str,
    llm_config: LLMConfig,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Categorize transactions using AI, processing in batches.

    Args:
        transactions: List of dicts with id, payee, memo, narration
        account_names: Set of all valid account names from the ledger
        default_account: Fallback account for unknown transactions
        source_account: Source account name for context
        llm_config: LLM configuration

    Returns:
        Tuple of:
        - Dict mapping transaction id -> suggested account
        - List of warning messages
    """
    if not llm_config.is_configured:
        raise AICategorizeError(
            "AI is not configured. Enable Finzytrack AI or set a model under Settings → AI."
        )

    accounts_list = sorted(account_names)
    all_results: Dict[str, str] = {}
    all_warnings: List[str] = []

    # Process in batches
    for i in range(0, len(transactions), BATCH_SIZE):
        batch = transactions[i:i + BATCH_SIZE]
        batch_results, batch_warnings = categorize_batch(
            transactions=batch,
            accounts=accounts_list,
            default_account=default_account,
            source_account=source_account,
            llm_config=llm_config,
            valid_accounts=account_names,
        )
        all_results.update(batch_results)
        all_warnings.extend(batch_warnings)

    return all_results, all_warnings
