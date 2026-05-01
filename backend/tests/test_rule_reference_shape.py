"""Tests for reference_shape() — the canonical example a write_*_rule tool
attaches to a failure response so a weaker model has a target to copy."""

from __future__ import annotations

from app.helpers.rule_validation import reference_shape


def test_csv_reference_shape_has_required_fields():
    shape = reference_shape("csv")
    assert isinstance(shape, dict)
    for field in ("name", "default_account", "default_currency", "columns"):
        assert field in shape
    # Sanity: amount columns are configured (either single or split).
    cols = shape.get("columns") or {}
    assert "date" in cols
    assert "amount" in cols or ("amount_debit" in cols and "amount_credit" in cols)


def test_xls_reference_shape_includes_sheet_handle():
    """An XLS rule needs at least one of sheet_index / sheet_name. The
    reference shape should demonstrate this — recipes without it fail at
    parse time with a confusing 'sheet not found' error."""
    shape = reference_shape("xls")
    assert isinstance(shape, dict)
    assert "sheet_index" in shape or "sheet_name" in shape
    assert "default_account" in shape
    assert "columns" in shape


def test_email_reference_shape_includes_metadata_and_transaction_types():
    shape = reference_shape("email")
    assert isinstance(shape, dict)
    assert "metadata" in shape
    assert "transaction_types" in shape
    assert isinstance(shape["transaction_types"], list)
    assert len(shape["transaction_types"]) > 0


def test_unknown_rule_type_returns_none():
    assert reference_shape("foobar") is None  # type: ignore[arg-type]
