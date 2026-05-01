"""Tool: test extraction rules against an email body/subject before saving."""
import re
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.ai.diagnostics import record_validation_failure
from app.ai.tools.base import BaseTool
from app.email_import.rule_schemas import ExtractionFieldDef

logger = logging.getLogger(__name__)


def _apply_cleanup(value: str, cleanup: str | None) -> str:
    if cleanup == "remove_commas":
        return value.replace(",", "")
    if cleanup == "strip_whitespace":
        return value.strip()
    return value


def _sample_snippet(text: str, max_chars: int = 240) -> str:
    """Return a short, single-line snippet of text for diagnostic messages."""
    if not text:
        return ""
    # Collapse whitespace so the snippet is one readable line
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[:max_chars] + "..."


def _test_one_field(
    field_name: str,
    field_def: ExtractionFieldDef,
    body: str,
    subject: str,
    header_date: datetime | None,
) -> dict:
    """Run extraction for a single field, returning a result dict."""

    # email_header_date source — no regex needed
    if field_def.source == "email_header_date":
        if header_date is None:
            return {
                "matched": False,
                "error": "email_header_date not provided — pass email_header_date parameter",
            }
        return {
            "matched": True,
            "value": header_date.isoformat(),
            "note": "taken from email Date header",
        }

    # Pattern required for body/subject sources
    if not field_def.pattern:
        return {"matched": False, "error": "No pattern defined and source is not email_header_date"}

    # Validate capture group count before attempting match
    try:
        check_compiled = re.compile(field_def.pattern)
        num_groups = check_compiled.groups
        if num_groups != 1:
            return {
                "matched": False,
                "pattern_used": field_def.pattern,
                "error": (
                    f"Pattern has {num_groups} capture group(s) — exactly 1 required. "
                    f"Wrap the value you want to extract in parentheses: (...)"
                ),
            }
    except re.error as e:
        return {
            "matched": False,
            "pattern_used": field_def.pattern,
            "error": f"Invalid regex: {e}",
        }

    text = subject if field_def.source == "subject" else body
    flags = re.DOTALL if field_def.multiline else 0

    compiled = re.compile(field_def.pattern, re.IGNORECASE | flags)
    m = compiled.search(text)
    if not m:
        return {
            "matched": False,
            "pattern_used": field_def.pattern,
            "source": field_def.source,
            "sample_text": _sample_snippet(text),
            "error": (
                f"Pattern did not match anywhere in the {field_def.source}. "
                f"Inspect 'sample_text' to see what was searched and adjust the pattern. "
                f"Common causes: literal text in the email differs from the pattern (case, "
                f"whitespace, currency symbol), or the pattern requires DOTALL — set "
                f"multiline=true to let . match newlines."
            ),
        }

    raw_value = m.group(1)
    cleaned = _apply_cleanup(raw_value, field_def.cleanup)

    # Attempt type conversion to surface format errors early
    if field_def.type == "float":
        try:
            display_value = str(Decimal(cleaned))
        except InvalidOperation:
            return {
                "matched": True,
                "raw": raw_value,
                "pattern_used": field_def.pattern,
                "error": f"Extracted '{cleaned}' but cannot convert to float — check cleanup or pattern",
            }
    elif field_def.type == "integer":
        try:
            display_value = str(int(cleaned))
        except ValueError:
            return {
                "matched": True,
                "raw": raw_value,
                "pattern_used": field_def.pattern,
                "error": f"Extracted '{cleaned}' but cannot convert to integer",
            }
    elif field_def.type == "datetime":
        if not field_def.format:
            return {
                "matched": True,
                "raw": raw_value,
                "pattern_used": field_def.pattern,
                "error": "datetime field requires a 'format' key (strptime format string)",
            }
        try:
            dt = datetime.strptime(cleaned, field_def.format)
            display_value = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            return {
                "matched": True,
                "raw": raw_value,
                "pattern_used": field_def.pattern,
                "error": f"Extracted '{cleaned}' but cannot parse as datetime with format '{field_def.format}': {e}",
            }
    else:
        display_value = cleaned

    result: dict = {"matched": True, "value": display_value, "pattern_used": field_def.pattern}
    if raw_value != display_value:
        result["raw"] = raw_value
    return result


class TestEmailExtractionTool(BaseTool):
    @property
    def name(self) -> str:
        return "test_email_extraction"

    @property
    def description(self) -> str:
        return (
            "Test regex extraction rules against an email body and subject. "
            "Call this BEFORE presenting the confirmation checklist — it tells you "
            "exactly what each pattern extracts so you can show the user values, not raw regex. "
            "Also validates that each pattern compiles and has exactly one capture group. "
            "Re-call after fixing a pattern to verify the correction before presenting the updated value."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email_body": {
                    "type": "string",
                    "description": "Plain text body of the email",
                },
                "email_subject": {
                    "type": "string",
                    "description": "Subject line of the email",
                },
                "email_header_date": {
                    "type": "string",
                    "description": (
                        "ISO 8601 date string from the email Date header, "
                        "e.g. '2026-03-21T15:42:18+05:30'. "
                        "Required if any field uses source: email_header_date."
                    ),
                },
                "subject_regex": {
                    "type": "string",
                    "description": "email_filter.subject_regex to validate against the subject line",
                },
                "body_regex": {
                    "type": "string",
                    "description": "email_filter.body_regex to validate against the body",
                },
                "extraction_rules": {
                    "type": "object",
                    "description": (
                        "Dict of field_name → extraction field definition. Each definition is an object "
                        "with keys: pattern (str), type (string|float|integer|datetime), "
                        "source (body|subject|email_header_date), cleanup (remove_commas|strip_whitespace|null), "
                        "multiline (bool, default false), optional (bool, default false), "
                        "format (strptime string — required for datetime with body/subject source), "
                        "timezone (e.g. '+05:30')."
                    ),
                },
            },
            "required": ["email_body", "email_subject", "extraction_rules"],
        }

    async def execute(
        self,
        email_body: str,
        email_subject: str,
        extraction_rules: dict,
        email_header_date: str | None = None,
        subject_regex: str | None = None,
        body_regex: str | None = None,
    ) -> dict:
        # Parse header date
        header_date: datetime | None = None
        if email_header_date:
            try:
                header_date = datetime.fromisoformat(email_header_date)
            except ValueError:
                logger.warning(f"Could not parse email_header_date: {email_header_date!r}")

        # Test email filters
        filters: dict = {}
        for label, pattern, text in [
            ("subject_filter", subject_regex, email_subject),
            ("body_filter", body_regex, email_body),
        ]:
            if not pattern:
                continue
            try:
                matched = bool(re.search(pattern, text, re.IGNORECASE))
                filters[label] = {
                    "matched": matched,
                    "note": (
                        "matches — this email type will be accepted"
                        if matched
                        else "does NOT match — this email type would be skipped"
                    ),
                }
            except re.error as e:
                filters[label] = {"matched": False, "error": f"Invalid regex: {e}"}

        # Test each extraction field
        fields: dict = {}
        for field_name, field_raw in extraction_rules.items():
            try:
                field_def = ExtractionFieldDef.model_validate(field_raw)
            except Exception as e:
                fields[field_name] = {"matched": False, "error": f"Invalid field definition: {e}"}
                continue

            fields[field_name] = _test_one_field(
                field_name, field_def, email_body, email_subject, header_date
            )

        # Separate required vs optional failures so the UI badge reflects real problems
        failed = [name for name, r in fields.items() if not r.get("matched", False)]
        filter_failures = [k for k, v in filters.items() if not v.get("matched", True)]

        optional_fields: set[str] = set()
        for field_name, field_raw in extraction_rules.items():
            if isinstance(field_raw, dict) and field_raw.get("optional"):
                optional_fields.add(field_name)

        required_failures = [f for f in failed if f not in optional_fields]

        # success=False when any required field or filter fails — this turns the UI badge red
        all_ok = len(failed) == 0 and len(filter_failures) == 0
        has_required_failure = len(required_failures) > 0 or len(filter_failures) > 0

        if has_required_failure:
            # Synthesise one error string per failing field/filter for the audit log
            audit_errors = []
            for fname in required_failures:
                err = (fields.get(fname) or {}).get("error") or "did not match"
                audit_errors.append(f"fields.{fname}: {err}")
            for fkey in filter_failures:
                err = (filters.get(fkey) or {}).get("error") or "did not match"
                audit_errors.append(f"filters.{fkey}: {err}")
            record_validation_failure("test_email_extraction", audit_errors)

        return {
            "success": not has_required_failure,
            "filters": filters,
            "fields": fields,
            "summary": {
                "all_fields_matched": all_ok,
                "failed_fields": failed,
                "required_failures": required_failures,
                "filter_warnings": filter_failures,
            },
        }
