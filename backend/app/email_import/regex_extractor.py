"""Extract fields from email text using regex patterns defined in rule YAML."""
import re
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from app.email_import.rule_schemas import ExtractionFieldDef

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when a required field cannot be extracted."""
    pass


def _apply_cleanup(value: str, cleanup: Optional[str]) -> str:
    if cleanup == 'remove_commas':
        return value.replace(',', '')
    if cleanup == 'strip_whitespace':
        return value.strip()
    return value


def _convert_type(raw: str, field_def: ExtractionFieldDef) -> Any:
    cleaned = _apply_cleanup(raw, field_def.cleanup)
    if field_def.type == 'string':
        return cleaned
    if field_def.type == 'float':
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            raise ExtractionError(f"Cannot convert '{cleaned}' to float")
    if field_def.type == 'integer':
        try:
            return int(cleaned)
        except ValueError:
            raise ExtractionError(f"Cannot convert '{cleaned}' to integer")
    if field_def.type == 'datetime':
        if not field_def.format:
            raise ExtractionError("datetime field with body source requires a 'format' key")
        try:
            dt = datetime.strptime(cleaned, field_def.format)
            if field_def.timezone:
                # Parse "+05:30" style offset
                sign = 1 if field_def.timezone[0] != '-' else -1
                parts = field_def.timezone.lstrip('+-').split(':')
                hours, minutes = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                offset = timedelta(hours=sign * hours, minutes=sign * minutes)
                dt = dt.replace(tzinfo=timezone(offset))
            return dt
        except ValueError as e:
            raise ExtractionError(f"Cannot parse datetime '{cleaned}' with format '{field_def.format}': {e}")
    raise ExtractionError(f"Unknown field type: {field_def.type}")


def extract_fields(
    extraction_defs: Dict[str, ExtractionFieldDef],
    body_text: str,
    subject: str,
    email_header_date: datetime,
    required_fields: list,
) -> Dict[str, Any]:
    """
    Extract all defined fields from the email.

    Returns dict of {field_name: value}. Raises ExtractionError listing
    all missing required fields.
    """
    results: Dict[str, Any] = {}
    errors = []

    for field_name, field_def in extraction_defs.items():
        try:
            value = _extract_one_field(field_name, field_def, body_text, subject, email_header_date)
            if value is not None:
                results[field_name] = value
        except ExtractionError as e:
            if not field_def.optional:
                errors.append(f"Field '{field_name}': {e}")
            # Optional fields silently skipped

    # Check required fields
    missing_required = [f for f in required_fields if f not in results]
    if missing_required:
        all_errors = errors + [f"Required field '{f}' not extracted" for f in missing_required]
        raise ExtractionError("; ".join(all_errors))

    if errors:  # Non-required errors that weren't fatal
        logger.warning(f"Non-fatal extraction issues: {'; '.join(errors)}")

    return results


def _extract_one_field(
    field_name: str,
    field_def: ExtractionFieldDef,
    body_text: str,
    subject: str,
    email_header_date: datetime,
) -> Any:
    # Special case: email header date
    if field_def.source == 'email_header_date':
        if field_def.type != 'datetime':
            raise ExtractionError("email_header_date source only valid with type=datetime")
        return email_header_date

    # Choose text source
    text = subject if field_def.source == 'subject' else body_text

    # Must have a pattern for non-header sources
    if not field_def.pattern:
        raise ExtractionError("No pattern defined and source is not email_header_date")

    flags = re.DOTALL if field_def.multiline else 0
    try:
        compiled = re.compile(field_def.pattern, re.IGNORECASE | flags)
    except re.error as e:
        raise ExtractionError(f"Invalid regex pattern '{field_def.pattern}': {e}")

    m = compiled.search(text)
    if not m:
        if field_def.optional:
            return None
        raise ExtractionError(f"Pattern not found: '{field_def.pattern}'")

    raw_value = m.group(1)
    return _convert_type(raw_value, field_def)
