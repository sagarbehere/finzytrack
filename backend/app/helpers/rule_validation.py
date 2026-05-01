"""Shared validation helpers for rule file CRUD endpoints."""

import io
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal, Type

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from app.exceptions import APIError


# ── Reference shapes for rule write failures ────────────────────────────────

# Resolve seed_config the same way as elsewhere in the codebase: dev mode
# reads from the repo, frozen mode reads from the PyInstaller bundle.
_SEED_DIR_DEV = Path(__file__).parents[2] / "resources" / "seed_config"
_SEED_DIR_FROZEN = Path(getattr(sys, "_MEIPASS", "")) / "backend" / "seed_config"
_SEED_DIR = _SEED_DIR_FROZEN if getattr(sys, "frozen", False) else _SEED_DIR_DEV

# Per rule type, which seed file is the canonical "minimal valid example"
# the AI should use as a target shape when its draft fails validation. Pick
# small ones that exercise common patterns.
_REFERENCE_RULE_FILES: dict[str, str] = {
    "csv":   "csv_rules/bofa-checking.yaml",      # single-amount column, common header skip
    "xls":   "xls_rules/icici-current.yaml",      # split debit/credit, sheet_index, date format
    "email": "email_rules/icici-bank-savings.yaml",
}


@lru_cache(maxsize=8)
def _load_reference_rule(rule_type: Literal["csv", "xls", "email"]) -> dict | None:
    """Load and parse a known-working reference rule from seed_config. Returns
    None if the file is missing (e.g. seed_config not bundled in some
    environments) — callers fall back to no reference shape."""
    rel = _REFERENCE_RULE_FILES.get(rule_type)
    if not rel:
        return None
    path = _SEED_DIR / rel
    if not path.is_file():
        return None
    try:
        yaml = YAML(typ="safe")
        return yaml.load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def reference_shape(rule_type: Literal["csv", "xls", "email"]) -> dict | None:
    """Public entry point: a minimal valid rule of the given type, suitable for
    inclusion in a write-tool's failure response so a weaker model has a
    concrete target to converge on."""
    return _load_reference_rule(rule_type)


# Common AI-author typos for rule fields. Key = correct field name,
# value = list of likely-typo sibling keys the AI might have written instead.
# Used by format_pydantic_errors() to suggest a rename when a required field
# is missing AND a similar-named key exists at the same level.
_RULE_TYPO_HINTS: dict[str, list[str]] = {
    # CSV / XLS top-level
    "default_account":      ["account", "beancount_account", "target_account"],
    "default_currency":     ["currency", "currency_code"],
    "skip_lines_start":     ["skip_start", "skip_lines", "header_rows",
                              "skip_header", "skip_top"],
    "skip_lines_end":       ["skip_end", "footer_rows", "skip_bottom"],
    "date_format":          ["format", "dateformat", "date_fmt"],
    "decimal_separator":    ["decimal", "decimal_sep", "separator_decimal"],
    "negate_amounts":       ["negate", "negate_amount", "invert_amounts"],
    "sheet_name":           ["sheet", "worksheet"],
    "sheet_index":          ["sheet_idx", "sheet_no"],
    # CsvColumnMapping nested (parent is the `columns` dict)
    "amount":               ["value", "amt", "money"],
    "payee":                ["recipient", "merchant", "name", "payee_name"],
    "narration":            ["description", "desc", "details", "remark"],
    "memo":                 ["reference", "ref", "note", "ref_no"],
    # Email rule top-level (less common)
    "beancount_account":    ["account"],
    "body_keyword":         ["keyword", "body_key"],
    "bank_emails":          ["sender_emails", "senders", "from_emails"],
}


def _typo_hint_for(missing_field: str, parent_obj: object) -> str | None:
    """If the AI's input is missing a required field AND a similar-named
    sibling is present, suggest the rename. Returns a hint string or None."""
    if not isinstance(parent_obj, dict):
        return None
    candidates = _RULE_TYPO_HINTS.get(missing_field, [])
    for c in candidates:
        if c in parent_obj:
            return f"the parent object has a '{c}' field — rename it to '{missing_field}'"
    return None


def format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Convert a ValidationError into one human-readable string per problem.

    Each line names the offending field path, the problem, and (when available)
    the input value the user supplied — enough information for an AI assistant
    to identify and fix the mistake without re-reading the schema.

    For 'missing' errors, this also detects likely typos (e.g. the input has
    'account' but the schema requires 'default_account') and appends a hint.
    """
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) if err.get("loc") else "(root)"
        msg = err.get("msg", "invalid")
        input_value = err.get("input", None)
        err_type = err.get("type", "")

        if input_value is None or (isinstance(input_value, str) and not input_value):
            line = f"{loc}: {msg}"
        else:
            # Truncate long inputs so the message stays readable
            shown = input_value
            if isinstance(shown, str) and len(shown) > 60:
                shown = shown[:57] + "..."
            line = f"{loc}: {msg} (got {shown!r})"

        # Typo detection: for missing-field errors, Pydantic sets
        # input=<parent dict>. Check the parent for likely-typo siblings.
        if err_type == "missing":
            missing = err["loc"][-1] if err.get("loc") else None
            hint = _typo_hint_for(str(missing), input_value) if missing else None
            if hint:
                line += f". Hint: {hint}"

        lines.append(line)
    return lines


def parse_yaml(content: str) -> dict:
    """Parse a YAML string, raising APIError on failure."""
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(io.StringIO(content))
    except Exception as e:
        raise APIError(
            message=f"YAML parse error: {e}",
            code="YAML_PARSE_ERROR",
            status_code=400,
        )
    if not isinstance(data, dict):
        raise APIError(
            message="YAML content must be a mapping (key-value pairs), not a scalar or list.",
            code="YAML_PARSE_ERROR",
            status_code=400,
        )
    return data


def validate_rule_schema(data: dict, schema_class: Type[BaseModel], rule_type: str) -> None:
    """Validate parsed YAML data against a Pydantic model, raising APIError on failure."""
    try:
        schema_class.model_validate(data)
    except Exception as e:
        raise APIError(
            message=f"{rule_type} rule validation failed: {e}",
            code="VALIDATION_ERROR",
            status_code=400,
        )


def resolve_rule_path(rules_dir: Path, filename: str) -> Path:
    """Resolve filename against rules_dir with path traversal protection.

    Ensures the filename has a .yaml/.yml extension and resolves within
    the target directory.
    """
    if not filename.endswith((".yaml", ".yml")):
        filename += ".yaml"

    target = (rules_dir / filename).resolve()
    if not target.is_relative_to(rules_dir.resolve()):
        raise APIError(
            message="Invalid filename — path traversal not allowed.",
            code="INVALID_PATH",
            status_code=400,
            details={"filename": filename},
        )
    return target
