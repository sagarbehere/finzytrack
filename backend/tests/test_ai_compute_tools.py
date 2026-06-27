"""AI assistant compute/budget tools + step-aware dry-run (§7.8)."""

import asyncio
import json

from app.ai.tools.get_compute_functions import GetComputeFunctionsTool
from app.ai.tools.execute_compute import ExecuteComputeTool
from app.ai.tools.get_budget_guide import GetBudgetGuideTool
from app.ai.tools.write_recipe import _dry_run_queries
from app.compute.function_registry import COMPUTE_FUNCTION_NOT_FOUND


class _StubReader:
    def get_custom_directives(self, directive_type):
        return [{
            "date": "2026-01-01",
            "values_json": json.dumps([
                ["Expenses:Food", "<AccountDummy>"],
                ["monthly", "<class 'str'>"],
                [["500", "USD"], "<class 'beancount.core.amount.Amount'>"],
            ]),
            "source_file": "main.beancount", "source_lineno": 1,
        }]


# ── get_compute_functions ────────────────────────────────────────────────────


def test_get_compute_functions_lists_budget_for_range_with_output_shape():
    result = asyncio.run(GetComputeFunctionsTool().execute())
    assert result["success"]
    fns = {f["name"]: f for f in result["functions"]}
    assert "budget_for_range" in fns
    assert fns["budget_for_range"]["output_shape"]
    assert "from" in fns["budget_for_range"]["parameters_schema"]["properties"]


# ── execute_compute ──────────────────────────────────────────────────────────


def test_execute_compute_runs_budget_for_range():
    tool = ExecuteComputeTool(_StubReader())
    result = asyncio.run(tool.execute("budget_for_range", {"from": "2026-06-01", "to": "2026-06-30"}))
    assert result["success"]
    assert result["result"] == [{"account": "Expenses:Food", "currency": "USD", "budget": "500"}]


def test_execute_compute_unknown_function_errors():
    tool = ExecuteComputeTool(_StubReader())
    result = asyncio.run(tool.execute("nope", {}))
    assert result["success"] is False
    assert result["code"] == COMPUTE_FUNCTION_NOT_FOUND


# ── get_budget_guide ─────────────────────────────────────────────────────────


def test_get_budget_guide_returns_primer():
    result = asyncio.run(GetBudgetGuideTool().execute())
    assert result["success"]
    assert "budget_for_range" in result["guide"]
    assert "custom" in result["guide"]


# ── step-aware dry-run ───────────────────────────────────────────────────────


def _widget(steps, output="out"):
    return {
        "schemaVersion": 2, "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": [{"widgetId": "w", "gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [{"id": "w", "title": "W", "steps": steps, "output": output,
                     "visualization": {"type": "table", "columns": []}}],
    }


def test_dry_run_flags_unknown_compute_fn():
    dash = _widget([
        {"id": "b", "kind": "compute", "fn": "does_not_exist", "args": {}},
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.b}}"]},
    ])
    errors = _dry_run_queries(dash, None)
    assert any("unknown compute function" in e for e in errors)


def test_dry_run_flags_unknown_transform():
    dash = _widget([
        {"id": "out", "kind": "transform", "fn": "frobnicate", "inputs": ["{{steps.out}}"]},
    ])
    errors = _dry_run_queries(dash, None)
    assert any("unknown transform" in e for e in errors)


def test_dry_run_flags_bad_compute_args():
    dash = _widget([
        {"id": "b", "kind": "compute", "fn": "budget_for_range", "args": {}},  # missing from/to
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.b}}"]},
    ])
    errors = _dry_run_queries(dash, None)
    assert any("invalid args for 'budget_for_range'" in e for e in errors)


def test_dry_run_accepts_valid_compute_and_transform():
    dash = _widget([
        {"id": "b", "kind": "compute", "fn": "budget_for_range", "args": {"from": "2026-01-01", "to": "2026-01-31"}},
        {"id": "out", "kind": "transform", "fn": "joinBudgetActual", "inputs": ["{{steps.b}}"]},
    ])
    assert _dry_run_queries(dash, None) == []
