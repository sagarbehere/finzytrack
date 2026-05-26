"""
LLM-based transaction parsing endpoint.

POST /api/import/llm-parse — accepts a file upload (CSV, XLS, PDF, or image)
along with account and currency, sends the file to the configured LLM for
transaction extraction, validates the response, and returns parsed transactions.
"""

import base64
import io
import json
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.ai.client import DoneEvent, TokenEvent, stream_chat
from app.ai.file_processor import process_file as process_eml_file
from app.config import LLMConfig
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.schemas.llm_parse_schemas import LlmParsedTransaction, LlmParseResult
from app import error_codes as ec

logger = logging.getLogger(__name__)
router = APIRouter()

_PROMPTS_DIR = Path(__file__).parents[4] / "resources" / "prompts"

_CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}
_XLS_EXTENSIONS = {".xls", ".xlsx", ".xlsm", ".xlsb"}
_PDF_EXTENSIONS = {".pdf"}
_EML_EXTENSIONS = {".eml"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}

_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


def _load_system_prompt() -> str:
    path = _PROMPTS_DIR / "llm_parse_transactions.md"
    if not path.is_file():
        raise FileNotFoundError(f"LLM parse prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _detect_file_type(ext: str) -> str:
    if ext in _CSV_EXTENSIONS:
        return "csv"
    if ext in _XLS_EXTENSIONS:
        return "xls"
    if ext in _PDF_EXTENSIONS:
        return "pdf"
    if ext in _EML_EXTENSIONS:
        return "eml"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    raise APIError(
        f"Unsupported file type: {ext}",
        code=ec.UNSUPPORTED_FILE_TYPE,
        status_code=400,
        details={"supported": "csv, tsv, txt, xls, xlsx, pdf, eml, jpg, png, gif, webp"},
    )


def _xls_to_text(file_bytes: bytes, filename: str) -> str:
    """Convert XLS/XLSX bytes to a tab-separated text table."""
    import pandas as pd

    try:
        xls = pd.ExcelFile(io.BytesIO(file_bytes))
    except Exception as e:
        raise APIError(
            f"Failed to read Excel file: {e}",
            code=ec.FILE_PARSE_ERROR,
            status_code=400,
        )

    parts = []
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=str)
        df = df.fillna("")
        if len(xls.sheet_names) > 1:
            parts.append(f"--- Sheet: {sheet_name} ---")
        rows = df.apply(lambda row: "\t".join(str(v) for v in row), axis=1)
        parts.append("\n".join(rows))

    return "\n\n".join(parts)


def _pdf_to_text(file_bytes: bytes) -> str:
    """Extract text from a PDF for providers that don't support native PDF input."""
    try:
        import pdfplumber
    except ImportError:
        raise APIError(
            "PDF text extraction requires the 'pdfplumber' package. "
            "Install it with: pip install pdfplumber. "
            "Alternatively, use an Anthropic provider which supports native PDF input.",
            code=ec.MISSING_DEPENDENCY,
            status_code=500,
        )

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"--- Page {i + 1} ---\n{text}")
            if not pages:
                raise APIError(
                    "Could not extract any text from the PDF. "
                    "The PDF may be image-only. Try using an Anthropic provider "
                    "which can read image-based PDFs natively, or upload a screenshot instead.",
                    code=ec.PDF_NO_TEXT,
                    status_code=400,
                )
            return "\n\n".join(pages)
    except APIError:
        raise
    except Exception as e:
        raise APIError(f"Failed to read PDF: {e}", code=ec.FILE_PARSE_ERROR, status_code=400)


def _build_user_content(
    file_bytes: bytes,
    filename: str,
    file_ext: str,
    file_type: str,
    account: str,
    currency: str,
    provider: str,
) -> str | list:
    """Build the user message content, handling multimodal formats per provider."""

    context_line = (
        f"Source account: {account}\n"
        f"Currency: {currency}\n\n"
        "Extract all transactions from the attached file."
    )

    # --- Text-based files (CSV, XLS) — works with any provider ---
    if file_type == "csv":
        # Try common encodings
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text = file_bytes.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = file_bytes.decode("utf-8", errors="replace")
        return f"{context_line}\n\n```\n{text}\n```"

    if file_type == "xls":
        text = _xls_to_text(file_bytes, filename)
        return f"{context_line}\n\n```\n{text}\n```"

    # --- EML (email) — extract text, strip HTML ---
    if file_type == "eml":
        text, _ = process_eml_file(file_bytes, filename)
        return f"{context_line}\n\n{text}"

    # --- PDF ---
    if file_type == "pdf":
        b64 = base64.b64encode(file_bytes).decode("ascii")
        if provider == "anthropic":
            return [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                },
                {"type": "text", "text": context_line},
            ]
        else:
            # OpenAI-compatible: try native PDF via file content block
            return [
                {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{b64}",
                    },
                },
                {"type": "text", "text": context_line},
            ]

    # --- Images ---
    if file_type == "image":
        b64 = base64.b64encode(file_bytes).decode("ascii")
        mime = _MIME_TYPES.get(file_ext, "image/png")
        if provider == "anthropic":
            return [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                },
                {"type": "text", "text": context_line},
            ]
        else:
            # OpenAI-compatible format
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
                {"type": "text", "text": context_line},
            ]

    raise APIError(f"Unsupported file type: {file_type}", code=ec.UNSUPPORTED_FILE_TYPE, status_code=400)


def _build_pdf_text_fallback(file_bytes: bytes, account: str, currency: str) -> str:
    """Build text-extracted PDF content as a fallback when native PDF is not supported."""
    context_line = (
        f"Source account: {account}\n"
        f"Currency: {currency}\n\n"
        "Extract all transactions from the attached file."
    )
    text = _pdf_to_text(file_bytes)
    return f"{context_line}\n\n{text}"


async def _call_llm(llm_config: LLMConfig, system_prompt: str, user_content: str | list) -> str:
    """Call the LLM and collect the full text response."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    full_response = ""
    async for event in stream_chat(llm_config, messages, tools=[]):
        if isinstance(event, TokenEvent):
            full_response += event.content
        elif isinstance(event, DoneEvent):
            break

    return full_response


def _extract_json(raw: str) -> dict:
    """Extract a JSON object from the LLM response, handling markdown fences."""
    text = raw.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise APIError(
            f"The AI returned a response that could not be parsed as JSON. "
            f"This usually means the file format was not recognized. Error: {e}",
            code=ec.LLM_INVALID_JSON,
            status_code=422,
        )

    if not isinstance(parsed, dict):
        raise APIError(
            "The AI returned an unexpected format. Expected a JSON object with a 'transactions' array.",
            code=ec.LLM_INVALID_FORMAT,
            status_code=422,
        )

    return parsed


def _validate_transactions(raw_transactions: list[dict]) -> tuple[list[LlmParsedTransaction], Optional[str]]:
    """
    Validate and clean LLM-parsed transactions.

    Returns (validated_transactions, warning_message).
    """
    if not raw_transactions:
        return [], None

    validated: list[LlmParsedTransaction] = []
    warnings: list[str] = []
    skipped = 0
    today = datetime.now().date()
    future_cutoff = today + timedelta(days=7)

    for i, raw in enumerate(raw_transactions):
        row_label = f"Row {i + 1}"

        # --- Required: date ---
        date_str = raw.get("date", "")
        if not date_str or not isinstance(date_str, str):
            skipped += 1
            continue

        # Try to parse date
        parsed_date = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                parsed_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if parsed_date is None:
            skipped += 1
            continue

        # Normalize to YYYY-MM-DD
        date_normalized = parsed_date.isoformat()

        # Date sanity checks
        if parsed_date.year < 1990:
            skipped += 1
            continue
        if parsed_date > future_cutoff:
            warnings.append(f"{row_label}: date {date_normalized} is in the future")

        # --- Required: amount ---
        amount = raw.get("amount")
        if amount is None:
            skipped += 1
            continue

        try:
            amount_decimal = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            skipped += 1
            continue

        if not amount_decimal.is_finite():
            skipped += 1
            continue

        # --- Optional fields ---
        payee = str(raw.get("payee", "") or "")
        narration = str(raw.get("narration", "") or "")
        memo = str(raw.get("memo", "") or "")

        validated.append(LlmParsedTransaction(
            date=date_normalized,
            payee=payee,
            narration=narration,
            amount=amount_decimal,
            memo=memo,
        ))

    # Build warning summary
    warning_parts: list[str] = []
    if skipped > 0:
        warning_parts.append(f"{skipped} row(s) skipped due to invalid date or amount")
    if warnings:
        warning_parts.extend(warnings)

    warning_msg = "; ".join(warning_parts) if warning_parts else None

    return validated, warning_msg


@router.post("/llm-parse")
async def llm_parse(
    file: UploadFile = File(...),
    account: str = Form(...),
    currency: str = Form(...),
    text_extraction: str = Form(default="false"),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Parse transactions from a file using the configured LLM."""
    config = config_manager.get_config()
    llm_config = config.ai.llm
    force_text_extraction = text_extraction.lower() == "true"

    if not llm_config.is_configured:
        raise APIError(
            "AI is not configured. Enable Finzytrack AI or configure a model under Settings → AI.",
            code=ec.LLM_NOT_CONFIGURED,
            status_code=400,
        )

    # Read and validate file
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise APIError("Uploaded file is empty.", code=ec.EMPTY_FILE, status_code=400)
    if len(file_bytes) > _MAX_FILE_SIZE:
        raise APIError(
            f"File is too large ({len(file_bytes) / 1024 / 1024:.1f} MB). Maximum size is {_MAX_FILE_SIZE / 1024 / 1024:.0f} MB.",
            code=ec.FILE_TOO_LARGE,
            status_code=400,
        )

    filename = file.filename or "unknown"
    file_ext = Path(filename).suffix.lower()
    file_type = _detect_file_type(file_ext)

    # Load system prompt
    try:
        system_prompt = _load_system_prompt()
    except FileNotFoundError as e:
        raise APIError(str(e), code=ec.CONFIG_ERROR, status_code=500)

    # For PDFs, allow the frontend to force text extraction mode
    if file_type == "pdf" and force_text_extraction:
        user_content = _build_pdf_text_fallback(file_bytes, account, currency)
    else:
        user_content = _build_user_content(
            file_bytes, filename, file_ext, file_type, account, currency, llm_config.provider,
        )

    # Call LLM
    try:
        raw_response = await _call_llm(llm_config, system_prompt, user_content)
    except Exception as e:
        if file_type == "pdf" and not force_text_extraction:
            # Native PDF not supported — tell the frontend to retry with text extraction
            logger.info(
                "Native PDF failed for provider %s (%s), signalling frontend to retry",
                llm_config.provider, e,
            )
            raise APIError(
                "Your model provider does not support native PDF input. "
                "Retrying with local text extraction...",
                code=ec.PDF_NATIVE_NOT_SUPPORTED,
                status_code=422,
            )
        logger.error("LLM call failed: %s", e, exc_info=True)
        raise APIError(
            f"Failed to get a response from the AI model: {e}",
            code=ec.LLM_CALL_FAILED,
            status_code=502,
        )

    if not raw_response.strip():
        raise APIError(
            "The AI model returned an empty response. Please try again.",
            code=ec.LLM_EMPTY_RESPONSE,
            status_code=422,
        )

    # Parse JSON from LLM response
    parsed = _extract_json(raw_response)

    raw_transactions = parsed.get("transactions")
    if not isinstance(raw_transactions, list):
        raise APIError(
            "The AI response did not contain a 'transactions' array.",
            code=ec.LLM_INVALID_FORMAT,
            status_code=422,
        )

    if len(raw_transactions) == 0:
        raise APIError(
            "The AI could not find any transactions in this file. "
            "The file may not contain financial transaction data, "
            "or the format may not be recognized.",
            code=ec.NO_TRANSACTIONS_FOUND,
            status_code=422,
        )

    # Validate and clean
    transactions, warning = _validate_transactions(raw_transactions)

    if len(transactions) == 0:
        raise APIError(
            "All transactions extracted by the AI failed validation "
            "(invalid dates or amounts). The file may not contain "
            "recognizable transaction data.",
            code=ec.ALL_TRANSACTIONS_INVALID,
            status_code=422,
        )

    result = LlmParseResult(
        transactions=transactions,
        file_type=file_type,
        transaction_count=len(transactions),
        warning=warning,
    )

    return success_json_response(result.model_dump())
