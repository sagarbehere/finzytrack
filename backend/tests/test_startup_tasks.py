"""Startup-task framework: read-only detection, gated apply, retirement, and
the one-shot upgrade-state (dev-docs/upgrades.md)."""

import json
from pathlib import Path

from app.startup_tasks.registry import StartupTaskRegistry, build_startup_registry
from app.startup_tasks.upgrade_state import UpgradeState
from app.startup_tasks.base import StartupTask
from app.schemas.startup_schemas import StartupTaskInfo, SEVERITY_ACTION_REQUIRED, SEVERITY_INFO


def _legacy_tree(root: Path):
    (root / "dashboards").mkdir(parents=True)
    (root / "dashboards" / "d.json").write_text(json.dumps({
        "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": [{"widgetId": "w", "gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [{"id": "w", "title": "W", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }), encoding="utf-8")


# ── Recipe migration task ────────────────────────────────────────────────────


def test_detect_is_read_only_and_surfaces_action_required(tmp_path: Path):
    config = tmp_path / "config"
    recipes = config / "recipes"
    _legacy_tree(recipes)
    before = (recipes / "dashboards" / "d.json").read_text()

    registry = build_startup_registry(config, recipes)
    tasks = registry.detect()

    # Detection must not mutate anything.
    assert (recipes / "dashboards" / "d.json").read_text() == before
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "recipes-upgrade"
    assert t.severity == SEVERITY_ACTION_REQUIRED
    assert t.requires_consent is True
    assert t.docs_path
    assert t.details["legacy_dashboards"] == 1


def test_no_task_when_recipes_already_v2(tmp_path: Path):
    config = tmp_path / "config"
    recipes = config / "recipes"
    (recipes / "dashboards").mkdir(parents=True)
    (recipes / "dashboards" / "d.json").write_text(json.dumps({
        "schemaVersion": 2, "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": []},
        "widgets": [{"id": "w", "title": "W", "steps": [{"id": "s", "kind": "query", "query": "SELECT 1"}], "output": "s", "visualization": {"type": "kpi"}}],
    }), encoding="utf-8")
    assert build_startup_registry(config, recipes).detect() == []


def test_apply_runs_migration_and_then_self_retires(tmp_path: Path):
    config = tmp_path / "config"
    recipes = config / "recipes"
    _legacy_tree(recipes)
    registry = build_startup_registry(config, recipes)

    assert len(registry.detect()) == 1  # pending before
    result = registry.apply("recipes-upgrade")
    assert "d.json" in result["migrated_dashboards"]

    # Data-driven task self-retires once everything is at the target version.
    assert build_startup_registry(config, recipes).detect() == []


def test_apply_unknown_task_raises(tmp_path: Path):
    reg = build_startup_registry(tmp_path / "config", tmp_path / "config" / "recipes")
    try:
        reg.apply("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass


# ── One-shot retirement via upgrade-state ────────────────────────────────────


class _OneShotInfoTask(StartupTask):
    id = "demo-notice"
    one_shot = True

    def __init__(self):
        self.applied = 0

    def detect(self):
        return StartupTaskInfo(id=self.id, title="Notice", summary="hi",
                               severity=SEVERITY_INFO, requires_consent=False)

    def apply(self):
        self.applied += 1
        return {"ok": True}


def test_one_shot_task_retires_after_apply(tmp_path: Path):
    state = UpgradeState(tmp_path / "config")
    registry = StartupTaskRegistry(state)
    task = _OneShotInfoTask()
    registry.register(task)

    assert len(registry.detect()) == 1   # surfaces first time
    registry.apply("demo-notice")
    assert registry.detect() == []       # retired after apply

    # Persisted across a fresh state load (same config dir).
    fresh = StartupTaskRegistry(UpgradeState(tmp_path / "config"))
    fresh.register(_OneShotInfoTask())
    assert fresh.detect() == []


# ── Endpoint round-trip ──────────────────────────────────────────────────────


def test_endpoint_lists_tasks(test_client):
    resp = test_client.get("/api/startup/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"]["tasks"], list)


def test_endpoint_apply_unknown_task_is_404(test_client):
    from app import error_codes as ec
    resp = test_client.post("/api/startup/tasks/does-not-exist/apply")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == ec.STARTUP_TASK_NOT_FOUND
