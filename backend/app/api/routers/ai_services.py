"""
Stateless AI service endpoints.

POST /ai/parse-nl-transaction  — natural language → one or more Beancount transactions
POST /ai/generate-query        — natural language → SQL or BQL query string

These are simple one-shot request/response calls (no streaming, no tool-use).
The LLM API key stays server-side — the frontend never sees it.
"""

import json
import logging
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError, TypeAdapter

from app.ai.client import complete_chat
from app.config import LLMConfig
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager, get_sqlite_reader
from app.services.sqlite_reader import SqliteReader
from app.services.investment_assembler import InvestmentAssembler
from app.schemas.investment_schemas import InvestmentEvent, DraftTransaction
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
    amount: Decimal | None = None
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
    transactions: list[ParsedTransaction] = []
    drafts: list[DraftTransaction] = []
    warnings: list[str] = []


_EVENT_ADAPTER = TypeAdapter(InvestmentEvent)


def _parse_investment_events(raw_events: list[Any]) -> tuple[list[InvestmentEvent], list[str]]:
    """Validate raw event dicts from the model into typed InvestmentEvents.

    Salvages the good ones and warns on the rest, rather than failing the whole
    request — an unusable event becomes a warning the user sees, rather than a 500.
    """
    events: list[InvestmentEvent] = []
    warnings: list[str] = []
    for i, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            warnings.append(f"Event {i + 1}: expected an object, got {type(raw).__name__}.")
            continue
        try:
            events.append(_EVENT_ADAPTER.validate_python(raw))
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            loc = ".".join(str(x) for x in first.get("loc", [])) or "(root)"
            warnings.append(f"Event {i + 1}: could not be understood ({loc}: {first.get('msg', 'invalid')}).")
    return events, warnings


class GenerateQueryData(BaseModel):
    query: str


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce_to_transaction_list(parsed: Any) -> tuple[list[Any], list[str]]:
    """Normalise the model's top-level JSON into a list of transaction candidates.

    The prompt asks for a JSON array, but models also emit a bare object (single
    transaction) or an envelope like {"transactions": [...]}. Accept all three
    rather than failing the whole request on a shape the user can't control.
    """
    warnings: list[str] = []

    if isinstance(parsed, list):
        return parsed, warnings

    if isinstance(parsed, dict):
        for key in ("transactions", "entries", "results"):
            inner = parsed.get(key)
            if isinstance(inner, list):
                return inner, warnings
        # A bare transaction object.
        return [parsed], warnings

    warnings.append(
        f"Unexpected response structure: expected a list of transactions, "
        f"got {type(parsed).__name__}."
    )
    return [], warnings


def _salvage_transaction(raw: dict[str, Any]) -> ParsedTransaction:
    """Build a ParsedTransaction from whatever fields in `raw` are usable.

    Used when strict validation fails: keep the fields the model got right so the
    user has something to edit, instead of handing the frontend an unusable shape.
    """
    txn = ParsedTransaction()
    for field in ParsedTransaction.model_fields:
        if field not in raw:
            continue
        try:
            one = ParsedTransaction.model_validate({field: raw[field]})
        except ValidationError:
            continue
        setattr(txn, field, getattr(one, field))
    return txn


def _validate_parsed_transaction(
    raw: dict[str, Any], valid_accounts: set[str], valid_currencies: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and return (cleaned_data, warnings)."""
    warnings: list[str] = []

    # Structural validation
    try:
        txn = ParsedTransaction.model_validate(raw)
    except ValidationError as e:
        # Salvage the usable fields and warn rather than rejecting outright
        field_errors = ", ".join(
            f"{'.'.join(str(loc) for loc in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        warnings.append(f"Unexpected response structure: {field_errors}")
        txn = _salvage_transaction(raw) if isinstance(raw, dict) else ParsedTransaction()

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
        balance = sum(amounts, start=Decimal("0"))
        if abs(balance) > Decimal("0.005"):
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

    # Investment activity comes back as a typed-event envelope ({"events": [...]});
    # regular activity is a plain transaction array (or {"transactions": [...]}).
    raw_events = parsed.get("events") if isinstance(parsed, dict) else None
    raw_events = raw_events if isinstance(raw_events, list) else []

    warnings: list[str] = []
    if isinstance(parsed, dict) and "transactions" in parsed:
        candidates, coerce_warnings = _coerce_to_transaction_list(parsed)
    elif raw_events:
        candidates, coerce_warnings = [], []  # events-only response
    else:
        candidates, coerce_warnings = _coerce_to_transaction_list(parsed)
    warnings.extend(coerce_warnings)

    # Plain transactions.
    transactions: list[dict[str, Any]] = []
    multiple = len(candidates) > 1
    for i, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            warnings.append(
                f"Transaction {i + 1}: expected an object, got {type(candidate).__name__}."
            )
            continue
        txn, txn_warnings = _validate_parsed_transaction(
            candidate, account_set, currency_set
        )
        transactions.append(txn)
        prefix = f"Transaction {i + 1}: " if multiple else ""
        warnings.extend(f"{prefix}{w}" for w in txn_warnings)

    # Investment events → the shared assembler builds balanced drafts (cost basis,
    # lots, gains resolved from the mirror). The model never writes Beancount geometry.
    drafts: list[dict[str, Any]] = []
    if raw_events:
        events, event_warnings = _parse_investment_events(raw_events)
        warnings.extend(event_warnings)
        if events:
            result = InvestmentAssembler(sqlite_reader).assemble(events)
            drafts = [d.model_dump(mode="json") for d in result.drafts]
            warnings.extend(iss.message for iss in result.errors)
            warnings.extend(iss.message for iss in result.warnings)

    return success_json_response(
        data={"transactions": transactions, "drafts": drafts, "warnings": warnings}
    )


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
