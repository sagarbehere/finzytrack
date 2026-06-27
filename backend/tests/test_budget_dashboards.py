"""Acceptance gate for the seeded budget demo dashboards (§15).

Each demo must: validate under the steps/DAG validator; have every sql step parse
against the postings schema; reference a real compute function with schema-valid
args; and reference only known transforms wired to declared steps. This is the
step-aware dry-run (§4.11) applied to the committed demos.
"""

import glob
import json
import re
import sqlite3

import jsonschema
import pytest

from app.helpers.recipe_validation import validate_dashboard
from app.api.routers.compute import build_registry

DASHBOARDS_DIR = "resources/seed_config/recipes/dashboards"
BUDGET_DASHBOARDS = sorted(glob.glob(f"{DASHBOARDS_DIR}/budget-*.json"))

# The client transform catalog (kept in sync with useRecipeTransforms.ts).
KNOWN_TRANSFORMS = {
    "none", "firstRow", "firstValue", "sortBy", "limit", "pluck", "pivot",
    "joinBudgetActual", "joinByPeriod", "runningSum", "envelopeRollover",
}


def _postings_conn():
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE postings (transaction_id TEXT, transaction_date TEXT, "
        "transaction_payee TEXT, transaction_narration TEXT, account TEXT, "
        "account_type TEXT, amount REAL, currency TEXT, year INTEGER, "
        "year_month TEXT, quarter INTEGER);"
    )
    return con


def test_there_are_budget_demo_dashboards():
    assert len(BUDGET_DASHBOARDS) >= 4


@pytest.mark.parametrize("path", BUDGET_DASHBOARDS, ids=lambda p: p.split("/")[-1])
def test_budget_dashboard_is_valid_and_dry_runs(path):
    d = json.loads(open(path).read())

    # 1. Structural validation (steps/output/refs/acyclicity/schemaVersion).
    assert validate_dashboard(d) == [], validate_dashboard(d)

    registry = build_registry(None)
    con = _postings_conn()

    for widget in d["widgets"]:
        step_ids = {s["id"] for s in widget["steps"]}
        for s in widget["steps"]:
            if s["kind"] == "sql":
                params = {n: "x" for n in re.findall(r":(\w+)", s["query"])}
                # Raises on column typos / syntax errors.
                con.execute(f"SELECT * FROM ({s['query']}) LIMIT 0", params)
            elif s["kind"] == "compute":
                fn = registry.get(s["fn"])
                assert fn is not None, f"{path}: unknown compute fn {s['fn']}"
                # Args satisfy the fn schema ({{...}} templates are plain strings).
                jsonschema.validate(instance=s.get("args", {}), schema=fn.parameters_schema)
            elif s["kind"] == "transform":
                assert s["fn"] in KNOWN_TRANSFORMS, f"{path}: unknown transform {s['fn']}"
                for inp in s["inputs"]:
                    m = re.match(r"^\{\{\s*steps\.([a-z0-9-]+)", inp)
                    if m:
                        assert m.group(1) in step_ids, f"{path}: input {inp} → unknown step"
