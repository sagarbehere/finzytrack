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


def test_partial_migration_downgrades_gate_to_dismissible_notice(tmp_path: Path):
    """A migration that can't convert every file (e.g. a dashboard referencing a
    widget that no longer exists) must NOT wedge the app: after the user consents
    once, the still-pending task downgrades from an action-required gate to a
    non-blocking, dismissible notice."""
    config = tmp_path / "config"
    recipes = config / "recipes"
    (recipes / "dashboards").mkdir(parents=True)
    # Legacy dashboard whose layout references a widget that isn't inline and has
    # no standalone file → the migration reports an error and leaves it as-is.
    (recipes / "dashboards" / "broken.json").write_text(json.dumps({
        "id": "broken", "title": "B",
        "layout": {"columns": 12, "widgets": [{"widgetId": "ghost", "gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [],
    }), encoding="utf-8")

    # First launch: a hard gate.
    before = build_startup_registry(config, recipes).detect()
    assert len(before) == 1
    assert before[0].requires_consent is True
    assert before[0].severity == SEVERITY_ACTION_REQUIRED

    # Consent + apply → the file can't be migrated (best-effort per file).
    result = build_startup_registry(config, recipes).apply("recipes-upgrade")
    assert result["errors"]  # partial failure recorded

    # A fresh registry (reads persisted consent) now surfaces a NON-gating notice
    # instead of re-blocking — the no-permanent-wedge guarantee.
    after = build_startup_registry(config, recipes).detect()
    assert len(after) == 1
    assert after[0].requires_consent is False
    assert after[0].severity == SEVERITY_INFO


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

    def detect(self, consented: bool = False):
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


class _FailingOneShotTask(_OneShotInfoTask):
    id = "flaky-notice"

    def apply(self):
        super().apply()
        return {"errors": ["something went wrong"]}


def test_failed_one_shot_apply_is_not_marked_completed(tmp_path: Path):
    """A one-shot task that applies with errors must NOT retire — it stays
    pending so it re-surfaces rather than being silently marked done."""
    state = UpgradeState(tmp_path / "config")
    registry = StartupTaskRegistry(state)
    registry.register(_FailingOneShotTask())

    assert len(registry.detect()) == 1
    result = registry.apply("flaky-notice")
    assert result["errors"]                       # apply reported failure
    assert not state.is_completed("flaky-notice")  # NOT retired
    assert len(registry.detect()) == 1            # still pending


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


# ── Lifecycle logging (audit trail: present → executed → succeeded/failed) ────


def test_detect_logs_pending_task_ids_at_info(tmp_path: Path, caplog):
    import logging
    registry = StartupTaskRegistry(UpgradeState(tmp_path / "config"))
    registry.register(_OneShotInfoTask())

    with caplog.at_level(logging.INFO):
        registry.detect()

    assert any(
        "Startup tasks pending" in r.getMessage() and "demo-notice" in r.getMessage()
        for r in caplog.records
    )


def test_detect_is_quiet_at_info_when_nothing_pending(tmp_path: Path, caplog):
    import logging
    registry = StartupTaskRegistry(UpgradeState(tmp_path / "config"))  # no tasks

    with caplog.at_level(logging.INFO):
        assert registry.detect() == []

    # Detection runs on every load — a "nothing pending" line must not spam INFO.
    assert not any("Startup tasks pending" in r.getMessage() for r in caplog.records)


def test_endpoint_apply_logs_applying_then_applied(test_client, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        resp = test_client.post("/api/startup/tasks/recipes-upgrade/apply")

    assert resp.status_code == 200
    messages = [r.getMessage() for r in caplog.records]
    assert any("Applying startup task 'recipes-upgrade'" in m for m in messages)
    assert any("Startup task 'recipes-upgrade' applied" in m for m in messages)


# ── One-shot completion persistence (durable, logged, merge-safe) ─────────────


def test_completion_is_persisted_durably_and_readable(tmp_path: Path):
    config = tmp_path / "config"
    state = UpgradeState(config)
    state.mark_completed("demo-notice")

    # Written and reloadable by a fresh instance.
    assert UpgradeState(config).is_completed("demo-notice")
    # Durable write leaves no stray temp files beside the state file.
    leftovers = [p.name for p in config.iterdir() if p.name != ".upgrade-state.json"]
    assert leftovers == []


def test_mark_completed_logs_the_persisted_task(tmp_path: Path, caplog):
    import logging
    state = UpgradeState(tmp_path / "config")
    with caplog.at_level(logging.INFO):
        state.mark_completed("demo-notice")
    assert any(
        "demo-notice" in r.getMessage() and "completed" in r.getMessage()
        for r in caplog.records
    )


def test_save_failure_warns_and_does_not_raise(tmp_path: Path, caplog):
    import logging
    # config_dir's parent is a FILE, so mkdir(parents=True) raises NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    state = UpgradeState(blocker / "config")  # _read_disk on a bad path → empty

    with caplog.at_level(logging.WARNING):
        state.mark_completed("demo-notice")  # must not raise

    assert any(
        "demo-notice" in r.getMessage() and "re-surface" in r.getMessage()
        for r in caplog.records
    )


def test_concurrent_instances_do_not_clobber_each_others_completions(tmp_path: Path):
    config = tmp_path / "config"
    # Two instances that both loaded before either wrote (the lost-update window).
    a = UpgradeState(config)
    b = UpgradeState(config)

    a.mark_completed("task-a")
    b.mark_completed("task-b")  # _save re-reads disk and merges, keeping task-a

    fresh = UpgradeState(config)
    assert fresh.is_completed("task-a")
    assert fresh.is_completed("task-b")
