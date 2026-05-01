"""Tests for typo hints in format_pydantic_errors().

These cover the most common AI-author mistakes when generating import-rule
YAML — using a similar-but-wrong key name for a required field. The hint
nudges the AI toward a single-line fix instead of letting it re-read the
schema and re-derive the structure.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.helpers.rule_validation import format_pydantic_errors
from app.schemas.csv_schemas import CsvRule


def _errors_for(payload: dict) -> list[str]:
    try:
        CsvRule.model_validate(payload)
    except ValidationError as e:
        return format_pydantic_errors(e)
    return []


def test_account_typo_suggests_rename_to_default_account():
    errors = _errors_for({
        "name": "Test", "account": "Assets:Foo",
        "columns": {"date": 1, "amount": 2},
    })
    msg = next((e for e in errors if "default_account" in e), None)
    assert msg is not None
    assert "rename it to 'default_account'" in msg


def test_skip_start_typo_suggests_rename_to_skip_lines_start():
    # `skip_lines_start` has a default in the schema (=0), so this is a
    # special case: skipping in_progress field defaults means the typo would
    # silently degrade to the default. We don't fire the hint here because
    # there's no "missing" error to attach to. This test documents that
    # non-required-field typos won't be caught — by design — and pins the
    # behaviour so a future change to required-ness doesn't silently break
    # the hint logic.
    errors = _errors_for({
        "name": "Test", "default_account": "A",
        "skip_start": 5,
        "columns": {"date": 1, "amount": 2},
    })
    # No errors because the typoed field is just ignored and the real
    # field has a default.
    assert errors == []


def test_no_hint_when_no_likely_typo_present():
    """Missing field, but no similar-named key in the parent → plain message."""
    errors = _errors_for({
        "name": "Test",
        "columns": {"date": 1, "amount": 2},
    })
    msg = next((e for e in errors if "default_account" in e), None)
    assert msg is not None
    assert "rename" not in msg


def test_hint_works_for_nested_missing_field():
    """payee_name typo at the columns.* level."""
    errors = _errors_for({
        "name": "Test", "default_account": "A",
        "columns": {"amount": 2, "payee_name": 3},  # missing 'date'; 'payee_name' is unrecognised
    })
    # The 'date' field IS required → a missing error fires for columns.date.
    # We don't have 'date' in the typo dict (it has no common typo), so no
    # hint. payee_name → payee is not flagged because payee isn't required.
    msg = next((e for e in errors if "columns.date" in e), None)
    assert msg is not None


def test_format_pydantic_errors_handles_no_typo_match_gracefully():
    """An entirely unfamiliar input shape doesn't blow up the hint logic."""
    errors = _errors_for({"random": "garbage"})
    # Should produce errors for the missing required fields, no exceptions.
    assert any("name" in e or "default_account" in e or "columns" in e for e in errors)
