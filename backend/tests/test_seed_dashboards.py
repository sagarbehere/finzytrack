"""Acceptance gate for EVERY seeded dashboard (generalized from the budget-only
gate). Each seed dashboard must:

  - validate under the steps/DAG validator (structure/refs/acyclicity/schemaVersion);
  - have every `query` step parse against the REAL mirror schema (column typos and
    syntax errors surface exactly as they would in production);
  - reference a real `compute` function with schema-valid args; and
  - use only known `transform` fns, whose {{steps.x}} / {{dashboard.steps.x}} inputs
    resolve to a declared step.

The schema comes from an empty `SQLiteExporter` export — the same table/column
definitions prod uses, with no rows — so the dry-run is faithful without depending
on ledger data. This is the step-aware dry-run (§4.11) applied to the committed
demos, dashboard shared steps included.
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
from app.ai.tools.write_recipe import known_transforms
from app.services.sqlite_exporter import SQLiteExporter

DASHBOARDS_DIR = "resources/seed_config/recipes/dashboards"
SEED_DASHBOARDS = sorted(glob.glob(f"{DASHBOARDS_DIR}/*.json"))
BUDGET_DASHBOARDS = [p for p in SEED_DASHBOARDS if "/budget-" in p]


@pytest.fixture(scope="module")
def mirror_conn(tmp_path_factory):
    """A read-only connection to an empty mirror carrying the full real schema."""
    db = tmp_path_factory.mktemp("mirror") / "ledger.db"
    SQLiteExporter(str(db)).export_full_sync([], [], {})  # all tables, no rows
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA query_only = ON")
    yield con
    con.close()


def _dry_run_steps(steps, path, con, registry, dashboard_step_ids=frozenset()):
    """Dry-run a step list: query steps parse against the mirror schema, compute
    steps reference a real fn with schema-valid args, transform steps use a known
    fn whose {{steps.x}} / {{dashboard.steps.x}} inputs resolve to a declared step."""
    step_ids = {s["id"] for s in steps}
    for s in steps:
        kind = s["kind"]
        if kind == "query":
            params = {n: "x" for n in re.findall(r":(\w+)", s["query"])}
            # Raises on column typos / syntax errors; LIMIT 0 fetches nothing.
            con.execute(f"SELECT * FROM ({s['query']}) LIMIT 0", params)
        elif kind == "compute":
            fn = registry.get(s["fn"])
            assert fn is not None, f"{path}: unknown compute fn {s['fn']}"
            # Args satisfy the fn schema ({{...}} templates are plain strings).
            jsonschema.validate(instance=s.get("args", {}), schema=fn.parameters_schema)
        elif kind == "transform":
            assert s["fn"] in known_transforms(), f"{path}: unknown transform {s['fn']}"
            for inp in s["inputs"]:
                m = re.match(r"^\{\{\s*steps\.([a-z0-9-]+)", inp)
                md = re.match(r"^\{\{\s*dashboard\.steps\.([a-z0-9-]+)", inp)
                if m:
                    assert m.group(1) in step_ids, f"{path}: input {inp} → unknown step"
                elif md:
                    assert md.group(1) in dashboard_step_ids, f"{path}: input {inp} → unknown shared step"


def test_there_are_seed_dashboards():
    assert len(SEED_DASHBOARDS) >= 6, SEED_DASHBOARDS
    # The budget demos are a required subset.
    assert len(BUDGET_DASHBOARDS) >= 4, BUDGET_DASHBOARDS


@pytest.mark.parametrize("path", SEED_DASHBOARDS, ids=lambda p: p.split("/")[-1])
def test_seed_dashboard_is_valid_and_dry_runs(path, mirror_conn):
    d = json.loads(open(path).read())

    # 1. Structural validation (steps/output/refs/acyclicity/schemaVersion).
    assert validate_dashboard(d) == [], validate_dashboard(d)

    registry = build_registry(None)

    # 2. Dashboard shared steps (run once, feed many widgets) — dry-run first.
    shared = d.get("steps", [])
    _dry_run_steps(shared, path, mirror_conn, registry)
    shared_ids = {s["id"] for s in shared}

    # 3. Each widget's own steps, which may also reference the shared steps.
    for widget in d["widgets"]:
        _dry_run_steps(widget["steps"], path, mirror_conn, registry, dashboard_step_ids=shared_ids)
