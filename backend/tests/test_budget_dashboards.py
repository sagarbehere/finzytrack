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
# Single source of truth for the transform catalog — don't re-declare it here.
from app.ai.tools.write_recipe import KNOWN_TRANSFORMS

DASHBOARDS_DIR = "resources/seed_config/recipes/dashboards"
BUDGET_DASHBOARDS = sorted(glob.glob(f"{DASHBOARDS_DIR}/budget-*.json"))


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


def _dry_run_steps(steps, path, con, registry, dashboard_step_ids=frozenset()):
    """Dry-run a step list: query steps parse against postings, compute steps
    reference a real fn with schema-valid args, transform steps use a known fn
    whose {{steps.x}} / {{dashboard.steps.x}} inputs resolve to a declared step."""
    step_ids = {s["id"] for s in steps}
    for s in steps:
        kind = s["kind"]
        if kind == "query":
            params = {n: "x" for n in re.findall(r":(\w+)", s["query"])}
            # Raises on column typos / syntax errors.
            con.execute(f"SELECT * FROM ({s['query']}) LIMIT 0", params)
        elif kind == "compute":
            fn = registry.get(s["fn"])
            assert fn is not None, f"{path}: unknown compute fn {s['fn']}"
            # Args satisfy the fn schema ({{...}} templates are plain strings).
            jsonschema.validate(instance=s.get("args", {}), schema=fn.parameters_schema)
        elif kind == "transform":
            assert s["fn"] in KNOWN_TRANSFORMS, f"{path}: unknown transform {s['fn']}"
            for inp in s["inputs"]:
                m = re.match(r"^\{\{\s*steps\.([a-z0-9-]+)", inp)
                md = re.match(r"^\{\{\s*dashboard\.steps\.([a-z0-9-]+)", inp)
                if m:
                    assert m.group(1) in step_ids, f"{path}: input {inp} → unknown step"
                elif md:
                    assert md.group(1) in dashboard_step_ids, f"{path}: input {inp} → unknown shared step"


@pytest.mark.parametrize("path", BUDGET_DASHBOARDS, ids=lambda p: p.split("/")[-1])
def test_budget_dashboard_is_valid_and_dry_runs(path):
    d = json.loads(open(path).read())

    # 1. Structural validation (steps/output/refs/acyclicity/schemaVersion).
    assert validate_dashboard(d) == [], validate_dashboard(d)

    registry = build_registry(None)
    con = _postings_conn()

    # 2. Dashboard shared steps (run once, feed many widgets) — dry-run first.
    shared = d.get("steps", [])
    _dry_run_steps(shared, path, con, registry)
    shared_ids = {s["id"] for s in shared}

    # 3. Each widget's own steps, which may also reference the shared steps.
    for widget in d["widgets"]:
        _dry_run_steps(widget["steps"], path, con, registry, dashboard_step_ids=shared_ids)
