"""Extract transaction fields from email text using an LLM API."""
import json
import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

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


def _call_openai(llm_config: LLMConfig, user_message: str) -> str:
    """Call an OpenAI-compatible API (sync) and return the response text."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": llm_config.api_key.get_secret_value() or "not-needed"}
    if llm_config.api_url:
        base = llm_config.api_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        kwargs["base_url"] = base

    client = OpenAI(**kwargs, timeout=float(llm_config.timeout_secs))
    call_kwargs: dict[str, Any] = dict(
        model=llm_config.model or "gpt-4o",
        temperature=llm_config.temperature,
        messages=[
            {"role": "system", "content": _load_system_prompt()},
            {"role": "user", "content": user_message},
        ],
    )
    if llm_config.max_tokens > 0:
        call_kwargs["max_tokens"] = llm_config.max_tokens

    resp = client.chat.completions.create(**call_kwargs)
    return resp.choices[0].message.content or ""


def _call_anthropic(llm_config: LLMConfig, user_message: str) -> str:
    """Call the Anthropic API (sync) and return the response text."""
    import anthropic

    client = anthropic.Anthropic(api_key=llm_config.api_key.get_secret_value() or "not-needed")
    max_tokens = llm_config.max_tokens if llm_config.max_tokens > 0 else 4096

    resp = client.messages.create(
        model=llm_config.model,
        max_tokens=max_tokens,
        system=_load_system_prompt(),
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text if resp.content else ""


def extract_fields_llm(
    body_text: str,
    subject: str,
    llm_config: LLMConfig,
    explicit_sign_rule: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Extract transaction fields using an LLM.

    Uses the shared ai.llm config from the main backend.
    Supports both OpenAI-compatible and Anthropic providers.

    explicit_sign_rule: if provided, overrides LLM's is_debit inference.
    Format: {"field": "fixed", "value": "negative"} or {"field": "transaction_type",
             "negative_values": [...], "positive_values": [...]}
    """
    if not llm_config.api_url and llm_config.provider != "anthropic":
        raise LLMExtractionError("AI API URL is not configured")

    user_message = f"Subject: {subject}\n\nBody:\n{body_text}"

    try:
        if llm_config.provider == "anthropic":
            content = _call_anthropic(llm_config, user_message)
        else:
            content = _call_openai(llm_config, user_message)
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
