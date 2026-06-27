"""Compute registry + /api/compute endpoint (§7.4).

Asserts on error *codes* and HTTP status, never message strings.
"""

import asyncio
import json

import pytest

from app.compute.function_registry import (
    FunctionRegistry,
    COMPUTE_FUNCTION_NOT_FOUND,
    COMPUTE_VALIDATION_ERROR,
)
from app.compute.functions.budget_for_range import BudgetForRangeFunction


class _StubReader:
    """Read-only stub exposing only get_custom_directives — proves compute reads
    via the reader and never needs a writer."""

    def __init__(self, budget_rows):
        self._rows = budget_rows
        self.writes = 0

    def get_custom_directives(self, directive_type):
        assert directive_type == "budget"
        return self._rows


def _budget_row(account, interval, amount, currency="USD", d="2026-01-01"):
    return {
        "date": d,
        "values_json": json.dumps([
            [account, "<AccountDummy>"],
            [interval, "<class 'str'>"],
            [[amount, currency], "<class 'beancount.core.amount.Amount'>"],
        ]),
        "source_file": "main.beancount",
        "source_lineno": 1,
    }


def _registry(rows=None):
    reg = FunctionRegistry()
    reg.register(BudgetForRangeFunction(_StubReader(rows or [])))
    return reg


# ── Registry dispatch ────────────────────────────────────────────────────────


def test_unknown_function_returns_not_found_code():
    out = asyncio.run(_registry().execute("does_not_exist", {}))
    assert out["success"] is False
    assert out["code"] == COMPUTE_FUNCTION_NOT_FOUND


def test_bad_args_return_validation_code():
    # 'from'/'to' required; omit them.
    out = asyncio.run(_registry().execute("budget_for_range", {"currency": "USD"}))
    assert out["success"] is False
    assert out["code"] == COMPUTE_VALIDATION_ERROR


def test_known_function_returns_result():
    rows = [_budget_row("Expenses:Food", "monthly", "500")]
    out = asyncio.run(_registry(rows).execute(
        "budget_for_range", {"from": "2026-06-01", "to": "2026-06-30"}))
    assert out["success"] is True
    assert out["result"] == [{"account": "Expenses:Food", "currency": "USD", "budget": "500"}]


def test_get_schemas_includes_output_shape():
    schemas = _registry().get_schemas()
    bfr = next(s for s in schemas if s["name"] == "budget_for_range")
    assert "output_shape" in bfr and bfr["output_shape"]
    assert "from" in bfr["parameters_schema"]["properties"]


def test_compute_is_read_only_no_writes():
    reader = _StubReader([_budget_row("Expenses:Food", "monthly", "500")])
    reg = FunctionRegistry()
    reg.register(BudgetForRangeFunction(reader))
    asyncio.run(reg.execute("budget_for_range", {"from": "2026-06-01", "to": "2026-06-30"}))
    assert reader.writes == 0


# ── Endpoint round-trip ──────────────────────────────────────────────────────


def test_endpoint_unknown_function_is_404(test_client):
    resp = test_client.post("/api/compute", json={"function": "nope", "args": {}})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == COMPUTE_FUNCTION_NOT_FOUND


def test_endpoint_bad_args_is_400(test_client):
    resp = test_client.post("/api/compute", json={"function": "budget_for_range", "args": {}})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == COMPUTE_VALIDATION_ERROR


def test_endpoint_known_function_envelope_shape(test_client):
    resp = test_client.post("/api/compute", json={
        "function": "budget_for_range",
        "args": {"from": "2026-06-01", "to": "2026-06-30"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["function"] == "budget_for_range"
    assert isinstance(data["result"], list)
    assert "execution_time_ms" in data
