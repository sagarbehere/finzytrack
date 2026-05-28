"""Extract transaction fields from email text using an LLM API.

LLM routing goes through ``ai.client.complete_chat_sync`` so this site
benefits from the canonical provider abstraction: Finzytrack AI proxy
support, ``extra_request_body`` filtering, prompt caching, and the same
timeout/retry policy as the streaming assistant.
"""
import json
import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from app.ai.client import complete_chat_sync
from app.config import LLMConfig

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[2] / "resources" / "prompts"


@lru_cache(maxsize=1)
def _load_system_prompt() -> str:
    path = _PROMPTS_DIR / "email_extractor.md"
    if not path.is_file():
        raise FileNotFoundError(f"Email extractor prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class LLMExtractionError(Exception):
    pass


def extract_fields_llm(
    body_text: str,
    subject: str,
    llm_config: LLMConfig,
    explicit_sign_rule: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Extract transaction fields using an LLM.

    Uses the shared ai.llm config from the main backend, routed through
    the canonical ``complete_chat_sync`` — so this site supports the
    Finzytrack AI proxy and inherits the assistant's request shape.

    explicit_sign_rule: if provided, overrides LLM's is_debit inference.
    Format: {"field": "fixed", "value": "negative"} or {"field": "transaction_type",
             "negative_values": [...], "positive_values": [...]}
    """
    if not llm_config.is_configured:
        raise LLMExtractionError(
            "AI is not configured. Enable Finzytrack AI or set a model under "
            "Settings → AI."
        )

    user_message = f"Subject: {subject}\n\nBody:\n{body_text}"
    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": user_message},
    ]

    try:
        content = complete_chat_sync(llm_config, messages)
    except Exception as e:
        raise LLMExtractionError(f"AI request failed: {e}")

    if not content:
        raise LLMExtractionError("AI returned empty response")

    # Strip markdown fences
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        content = content.rstrip("`").strip()

    try:
        extracted = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMExtractionError(f"AI returned invalid JSON: {e}. Content: {content[:200]}")

    logger.debug("LLM extracted: %s", json.dumps(extracted, default=str))

    # Apply explicit sign rule if provided (overrides LLM's is_debit)
    if explicit_sign_rule:
        field_name = explicit_sign_rule.get("field")
        if field_name == "fixed":
            extracted["is_debit"] = (explicit_sign_rule.get("value") == "negative")

    # Convert amount to signed Decimal
    amount = Decimal(str(extracted.get("amount", 0)))
    is_debit = extracted.get("is_debit")
    if is_debit is True:
        amount = -abs(amount)
    elif is_debit is False:
        amount = abs(amount)

    return {
        "amount": amount,
        "payee": extracted.get("payee"),
        "date": extracted.get("date"),
        "reference": extracted.get("reference"),
        "masked_account": extracted.get("masked_account"),
    }
