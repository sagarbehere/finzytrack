"""
Stateless AI service endpoints.

POST /ai/parse-nl-transaction  — natural language → Beancount transaction JSON
POST /ai/generate-query        — natural language → SQL or BQL query string

These are simple one-shot request/response calls (no streaming, no tool-use).
The LLM API key stays server-side — the frontend never sees it.
"""

import json
import logging
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError

from app.ai.client import complete_chat
from app.config import LLMConfig
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager, get_sqlite_reader
from app.services.sqlite_reader import SqliteReader
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPTS_DIR = Path(__file__).parents[3] / "resources" / "prompts"


@lru_cache(maxsize=8)
def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Prompt file not found: {path}. "
            "Ensure backend/resources/prompts/ contains the expected .md files."
        )
    return path.read_text(encoding="utf-8").strip()


def _get_llm_config(config_manager: ConfigManager) -> LLMConfig:
    """Extract and validate LLM config."""
    llm = config_manager.get_config().ai.llm
    if not llm.is_configured:
        raise APIError(
            message="AI is not configured. Enable Finzytrack AI or set a model under Settings → AI.",
            code=ec.AI_NOT_CONFIGURED,
            status_code=400,
        )
    if not llm.finzytrack_ai and llm.provider != "anthropic" and not llm.api_url:
        raise APIError(
            message="AI API URL is not configured. Set api_url under Settings → AI.",
            code=ec.AI_NOT_CONFIGURED,
            status_code=400,
        )
    return llm


_FENCE_RE = re.compile(r"```(?:\w+)?\s*\n?([\s\S]*?)\n?\s*```")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.replace("```", "").strip()


# ── Parse NL Transaction ─────────────────────────────────────────────────────


class ParseNLTransactionRequest(BaseModel):
    text: str = Field(..., description="Natural language transaction description")
    default_currency: str | None = Field(None, description="Selected default currency")


def _build_nl_prompt(
    accounts: list[str], currencies: list[str], default_currency: str,
) -> str:
    """Build system prompt for NL transaction parsing."""
    template = _load_prompt("nl_transaction_parser.md")
    today = date.today().isoformat()

    # Group accounts by top-level type
    grouped: dict[str, list[str]] = {}
    for name in sorted(accounts):
        typ = name.split(":")[0]
        grouped.setdefault(typ, []).append(name)

    account_block = "\n".join(
        f"{typ}:\n  " + "\n  ".join(names)
        for typ, names in grouped.items()
    )

    currency_list = ", ".join(sorted(currencies)) if currencies else "USD"

    return (
        template
        .replace("{{TODAY}}", today)
        .replace("{{ACCOUNT_BLOCK}}", account_block)
        .replace("{{CURRENCY_LIST}}", currency_list)
        .replace("{{DEFAULT_CURRENCY}}", default_currency)
    )


class Posting(BaseModel):
    account: str = ""
    amount: float | int | None = None
    currency: str = ""


class ParsedTransaction(BaseModel):
    date: str = ""
    flag: str = "*"
    payee: str = ""
    narration: str = ""
    postings: list[Posting] = []
    tags: list[str] = []
    links: list[str] = []


class ParseNLTransactionData(BaseModel):
    transaction: ParsedTransaction
    warnings: list[str] = []


class GenerateQueryData(BaseModel):
    query: str


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_parsed_transaction(
    raw: dict[str, Any], valid_accounts: set[str], valid_currencies: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and return (cleaned_data, warnings)."""
    warnings: list[str] = []

    # Structural validation
    try:
        txn = ParsedTransaction.model_validate(raw)
    except ValidationError as e:
        # Return the raw data with a structural warning rather than rejecting
        field_errors = ", ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        warnings.append(f"Unexpected response structure: {field_errors}")
        return raw, warnings

    # Date format
    if txn.date and not _DATE_RE.match(txn.date):
        warnings.append(f"Date '{txn.date}' is not in YYYY-MM-DD format.")

    # Account existence
    for i, p in enumerate(txn.postings):
        if p.account and p.account not in valid_accounts:
            warnings.append(f"Account '{p.account}' not found in ledger.")

    # Currency existence
    for i, p in enumerate(txn.postings):
        if p.currency and p.currency not in valid_currencies:
            warnings.append(f"Currency '{p.currency}' not found in ledger.")

    # Postings balance
    amounts = [p.amount for p in txn.postings if p.amount is not None]
    if amounts:
        balance = sum(amounts)
        if abs(balance) > 0.005:
            warnings.append(f"Postings do not balance (sum = {balance:.2f}).")

    return txn.model_dump(), warnings


@router.post(
    "/ai/parse-nl-transaction",
    response_model=ApiResponse[ParseNLTransactionData],
    operation_id="parseNlTransaction",
)
async def parse_nl_transaction(
    body: ParseNLTransactionRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    llm = _get_llm_config(config_manager)

    account_set = sqlite_reader.get_account_names()
    accounts = sorted(account_set)
    currency_set = sqlite_reader.get_commodity_codes()
    currencies = sorted(currency_set)
    default_currency = body.default_currency or (currencies[0] if currencies else "USD")

    system_prompt = _build_nl_prompt(accounts, currencies, default_currency)

    try:
        content = await complete_chat(
            llm,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.text},
            ],
            temperature=0.1,
        )
    except RuntimeError as e:
        raise APIError(message=str(e), code=ec.AI_REQUEST_FAILED, status_code=502)
    except Exception as e:
        logger.exception("AI request failed")
        raise APIError(
            message=f"AI request failed: {e}", code=ec.AI_REQUEST_FAILED, status_code=502,
        )

    json_str = _strip_fences(content)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise APIError(
            message=f"AI returned invalid JSON: {e}",
            code=ec.AI_PARSE_ERROR,
            status_code=502,
            details={"raw": json_str[:300]},
        )

    transaction, warnings = _validate_parsed_transaction(parsed, account_set, currency_set)
    return success_json_response(data={"transaction": transaction, "warnings": warnings})


# ── Generate Query ───────────────────────────────────────────────────────────


class GenerateQueryRequest(BaseModel):
    question: str = Field(..., description="Natural language question")
    language: Literal["sqlite", "beanquery"] = Field(
        default="sqlite", description="Target query language"
    )


def _build_sql_prompt() -> str:
    """Build system prompt for SQLite query generation."""
    template = _load_prompt("sql_assistant.md")
    schema = _load_prompt("schema_postings.md")
    now = date.today()
    year = now.year
    month = f"{now.month:02d}"
    return (
        template
        .replace("{{YEAR_MONTH}}", f"{year}-{month}")
        .replace("{{YEAR}}", str(year))
        .replace("{{LAST_YEAR}}", str(year - 1))
        .replace("{{SCHEMA}}", schema)
    )


def _build_bql_prompt() -> str:
    """Build system prompt for BQL query generation."""
    template = _load_prompt("bql_assistant.md")
    now = date.today()
    year = now.year
    return (
        template
        .replace("{{TODAY}}", now.isoformat())
        .replace("{{YEAR}}", str(year))
        .replace("{{LAST_YEAR}}", str(year - 1))
    )


@router.post(
    "/ai/generate-query",
    response_model=ApiResponse[GenerateQueryData],
    operation_id="generateQuery",
)
async def generate_query(
    body: GenerateQueryRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    llm = _get_llm_config(config_manager)

    if body.language == "beanquery":
        system_prompt = _build_bql_prompt()
    else:
        system_prompt = _build_sql_prompt()

    try:
        content = await complete_chat(
            llm,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.question},
            ],
            temperature=0.1,
        )
    except RuntimeError as e:
        raise APIError(message=str(e), code=ec.AI_REQUEST_FAILED, status_code=502)
    except Exception as e:
        logger.exception("AI request failed")
        raise APIError(
            message=f"AI request failed: {e}", code=ec.AI_REQUEST_FAILED, status_code=502,
        )

    query = _strip_fences(content)
    return success_json_response(data={"query": query})
