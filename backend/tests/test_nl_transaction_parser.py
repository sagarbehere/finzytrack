"""Tests for POST /ai/parse-nl-transaction.

The endpoint's contract is that one natural-language description can yield
*several* transactions — a cross-date transfer with a fee is three postings on
one leg plus a second dated leg. These tests verify that:
  - a JSON array from the model becomes N transactions
  - a bare object or an envelope is still accepted (models are inconsistent)
  - a transaction may carry more than two postings
  - per-transaction warnings are attributed to the right transaction
  - a structurally broken entry salvages its usable fields instead of
    collapsing the whole response
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.routers.ai_services import (
    _coerce_to_transaction_list,
    _validate_parsed_transaction,
)

ACCOUNTS = {"Assets:Checking", "Assets:Transfer", "Assets:Savings", "Expenses:BankFees"}
CURRENCIES = {"USD"}


def _txn(date: str, postings: list[tuple[str, str]]) -> dict:
    return {
        "date": date,
        "flag": "*",
        "payee": "",
        "narration": "",
        "postings": [
            {"account": a, "amount": float(amt), "currency": "USD"} for a, amt in postings
        ],
        "tags": [],
        "links": [],
    }


# ── Top-level shape coercion ────────────────────────────────────────────────


def test_array_is_passed_through():
    items, warnings = _coerce_to_transaction_list([{"date": "2026-08-17"}, {"date": "2026-08-18"}])
    assert len(items) == 2
    assert warnings == []


def test_bare_object_becomes_single_item_list():
    items, warnings = _coerce_to_transaction_list({"date": "2026-08-17"})
    assert items == [{"date": "2026-08-17"}]
    assert warnings == []


def test_transactions_envelope_is_unwrapped():
    items, warnings = _coerce_to_transaction_list({"transactions": [{"date": "2026-08-17"}]})
    assert items == [{"date": "2026-08-17"}]
    assert warnings == []


def test_scalar_top_level_yields_no_transactions_and_a_warning():
    items, warnings = _coerce_to_transaction_list("nope")
    assert items == []
    assert len(warnings) == 1
    assert "expected a list of transactions" in warnings[0]


# ── Per-transaction validation ──────────────────────────────────────────────


def test_three_postings_are_preserved_and_balance_check_passes():
    raw = _txn("2026-08-17", [
        ("Assets:Checking", "-140000"),
        ("Expenses:BankFees", "40"),
        ("Assets:Transfer", "139960"),
    ])
    cleaned, warnings = _validate_parsed_transaction(raw, ACCOUNTS, CURRENCIES)
    assert len(cleaned["postings"]) == 3
    assert cleaned["postings"][1]["account"] == "Expenses:BankFees"
    assert warnings == []


def test_unbalanced_postings_warn():
    raw = _txn("2026-08-17", [("Assets:Checking", "-100"), ("Assets:Savings", "90")])
    _, warnings = _validate_parsed_transaction(raw, ACCOUNTS, CURRENCIES)
    assert len(warnings) == 1
    assert "do not balance" in warnings[0]


def test_broken_entry_salvages_usable_fields():
    """postings is the wrong type, but the date and payee are fine — keep them."""
    raw = {"date": "2026-08-17", "payee": "Axis Bank", "postings": "not a list"}
    cleaned, warnings = _validate_parsed_transaction(raw, ACCOUNTS, CURRENCIES)
    assert cleaned["date"] == "2026-08-17"
    assert cleaned["payee"] == "Axis Bank"
    assert cleaned["postings"] == []
    assert any("Unexpected response structure" in w for w in warnings)


# ── Endpoint ────────────────────────────────────────────────────────────────


def _post(client: TestClient, content: str, text: str = "move money") -> dict:
    """POST to the endpoint with AI configured and the LLM stubbed to return `content`."""
    from app.api.routers import ai_services

    llm = ai_services.LLMConfig(provider="anthropic", model="claude-opus-5", api_key="x")
    with (
        patch.object(ai_services, "_get_llm_config", return_value=llm),
        patch.object(ai_services, "complete_chat", AsyncMock(return_value=content)),
    ):
        resp = client.post("/api/ai/parse-nl-transaction", json={"text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _balance_warnings(data: dict) -> list[str]:
    """Only the balance warnings — the fixture ledger lacks these account names,
    so unrelated 'account not found' warnings are expected noise here."""
    return [w for w in data["warnings"] if "do not balance" in w]


def test_endpoint_returns_multiple_transactions(test_client: TestClient):
    """The reported bug: a two-leg cross-date transfer must yield two rows."""
    content = json.dumps([
        _txn("2026-08-17", [
            ("Assets:Checking", "-140000"),
            ("Expenses:BankFees", "40"),
            ("Assets:Transfer", "139960"),
        ]),
        _txn("2026-08-18", [
            ("Assets:Transfer", "-139960"),
            ("Assets:Savings", "139960"),
        ]),
    ])
    data = _post(test_client, content)
    assert len(data["transactions"]) == 2
    assert data["transactions"][0]["date"] == "2026-08-17"
    assert data["transactions"][1]["date"] == "2026-08-18"
    assert len(data["transactions"][0]["postings"]) == 3


def test_endpoint_accepts_a_bare_object(test_client: TestClient):
    content = json.dumps(_txn("2026-08-17", [("Assets:Checking", "-45"), ("Assets:Savings", "45")]))
    data = _post(test_client, content)
    assert len(data["transactions"]) == 1


def test_warnings_are_attributed_to_the_right_transaction(test_client: TestClient):
    content = json.dumps([
        _txn("2026-08-17", [("Assets:Checking", "-100"), ("Assets:Savings", "100")]),
        _txn("2026-08-18", [("Assets:Checking", "-100"), ("Assets:Savings", "90")]),
    ])
    data = _post(test_client, content)
    assert len(data["transactions"]) == 2
    assert _balance_warnings(data) == [
        "Transaction 2: Postings do not balance (sum = -10.00)."
    ]


def test_single_transaction_warnings_are_not_prefixed(test_client: TestClient):
    content = json.dumps([_txn("2026-08-17", [("Assets:Checking", "-100"), ("Assets:Savings", "90")])])
    data = _post(test_client, content)
    assert _balance_warnings(data) == ["Postings do not balance (sum = -10.00)."]


def test_one_broken_entry_does_not_drop_the_others(test_client: TestClient):
    content = json.dumps([
        _txn("2026-08-17", [("Assets:Checking", "-100"), ("Assets:Savings", "100")]),
        "garbage",
    ])
    data = _post(test_client, content)
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["date"] == "2026-08-17"
    assert any("Transaction 2" in w for w in data["warnings"])


def test_amounts_are_decimal_not_float():
    """Money stays exact — see dev-docs/money-types.md."""
    raw = {
        "date": "2026-08-17",
        "postings": [
            {"account": "Assets:Checking", "amount": "-140000.55", "currency": "USD"},
            {"account": "Assets:Savings", "amount": "140000.55", "currency": "USD"},
        ],
    }
    cleaned, warnings = _validate_parsed_transaction(raw, ACCOUNTS, CURRENCIES)
    assert cleaned["postings"][0]["amount"] == Decimal("-140000.55")
    assert warnings == []
