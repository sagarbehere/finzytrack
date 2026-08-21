"""POST /api/import/llm-parse — the investment-event channel (Slice 4b).

A brokerage statement can carry investment activity the flat cash extractor can't
express. The model may return an {"events": [...]} array; the endpoint injects the
account list into the prompt, runs the events through the shared assembler, and
returns balanced `drafts` alongside any plain `transactions`.
"""

import json
from unittest.mock import AsyncMock, patch, PropertyMock

from fastapi.testclient import TestClient

from app.api.routers.importer import llm_parse


def _post(client: TestClient, model_json: str) -> dict:
    """POST a CSV file with AI stubbed to return `model_json`."""
    with (
        patch("app.config.LLMConfig.is_configured", new_callable=PropertyMock, return_value=True),
        patch.object(llm_parse, "_call_llm", AsyncMock(return_value=model_json)),
    ):
        resp = client.post(
            "/api/import/llm-parse",
            files={"file": ("statement.csv", b"date,amount\n2024-06-03,379.09\n", "text/csv")},
            data={"account": "Assets:Checking", "currency": "USD"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_events_only_statement_yields_drafts_not_transactions(test_client: TestClient):
    content = json.dumps({"transactions": [], "events": [{
        "type": "cash_dividend", "date": "2024-06-03", "currency": "USD",
        "cash_account": "Assets:Checking", "security": "VOO", "amount": 379.09,
        "income_account": "Income:Dividends",
    }]})
    data = _post(test_client, content)
    assert data["transactions"] == []
    assert len(data["drafts"]) == 1
    d = data["drafts"][0]
    assert "dividend" in d["narration"].lower()
    cash = next(p for p in d["postings"] if p["account"] == "Assets:Checking")
    assert cash["amount"] == "379.09"  # money is a string on the wire


def test_mixed_statement_returns_both_channels(test_client: TestClient):
    content = json.dumps({
        "transactions": [{"date": "2024-06-01", "payee": "ATM", "narration": "Cash", "amount": -100, "memo": ""}],
        "events": [{
            "type": "buy", "date": "2024-06-03", "currency": "USD",
            "holding_account": "Assets:Brokerage:VOO", "security": "VOO", "units": 10,
            "unit_price": 417.45, "cash_account": "Assets:Checking",
        }],
    })
    data = _post(test_client, content)
    assert len(data["transactions"]) == 1
    assert len(data["drafts"]) == 1
    holding = next(p for p in data["drafts"][0]["postings"] if p["currency"] == "VOO")
    assert holding["cost_amount"] == "417.45"


def test_sell_without_lots_warns_and_builds_no_draft(test_client: TestClient):
    content = json.dumps({"events": [{
        "type": "sell", "date": "2024-06-03", "currency": "USD",
        "holding_account": "Assets:Brokerage:VOO", "security": "VOO", "units": 5,
        "sale_price": 560, "cash_account": "Assets:Checking",
        "gains_account": "Income:CapitalGains", "strategy": "fifo",
    }]})
    data = _post(test_client, content)
    assert data["drafts"] == []
    assert data["warning"] and "lot" in data["warning"].lower()


def test_empty_response_still_errors(test_client: TestClient):
    content = json.dumps({"transactions": [], "events": []})
    with (
        patch("app.config.LLMConfig.is_configured", new_callable=PropertyMock, return_value=True),
        patch.object(llm_parse, "_call_llm", AsyncMock(return_value=content)),
    ):
        resp = test_client.post(
            "/api/import/llm-parse",
            files={"file": ("statement.csv", b"x\n", "text/csv")},
            data={"account": "Assets:Checking", "currency": "USD"},
        )
    assert resp.status_code == 422
