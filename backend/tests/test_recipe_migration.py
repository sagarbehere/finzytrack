"""Recipe v1→v2 migration tests (§7.2 / §7.2b).

Verifies the conversion core and the filesystem driver: query+transform →
steps+output, standalone inlining, orphan removal, idempotency, malformed-skip,
and that migrated output validates under the new validator.
"""

import json
from pathlib import Path

import pytest

from app.migrations.recipe_migration import (
    SCHEMA_VERSION,
    migrate_dashboard,
    migrate_recipes_dir,
    migrate_widget,
)
from app.migrations.runner import apply_recipe_migration
from app.helpers.recipe_validation import validate_dashboard


# ── Widget conversion ────────────────────────────────────────────────────────


def test_query_only_widget_migrates_to_query_plus_transform_none():
    w = {"id": "w", "title": "W", "query": "SELECT 1",
         "visualization": {"type": "kpi"}}
    out = migrate_widget(w)
    assert out["steps"] == [
        {"id": "main", "kind": "query", "query": "SELECT 1"},
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.main}}"]},
    ]
    assert out["output"] == "out"
    assert "query" not in out and "transform" not in out


def test_pivot_widget_migrates_to_sql_plus_transform_pivot():
    w = {"id": "w", "title": "W", "query": "SELECT a, ym, amt FROM postings",
         "transform": {"type": "pivot", "rowField": "a", "columnField": "ym", "valueField": "amt"},
         "visualization": {"type": "pivot"}}
    out = migrate_widget(w)
    transform_step = out["steps"][1]
    assert transform_step["fn"] == "pivot"
    assert transform_step["config"]["rowField"] == "a"
    assert transform_step["inputs"] == ["{{steps.main}}"]


def test_string_transform_migrates_without_config():
    w = {"id": "w", "title": "W", "query": "SELECT 1", "transform": "firstRow",
         "visualization": {"type": "kpi"}}
    out = migrate_widget(w)
    assert out["steps"][1] == {"id": "out", "kind": "transform", "fn": "firstRow", "inputs": ["{{steps.main}}"]}


def test_dbtype_moves_to_query_step_engine():
    w = {"id": "w", "title": "W", "query": "SELECT 1", "dbType": "beanquery",
         "visualization": {"type": "kpi"}}
    out = migrate_widget(w)
    assert out["steps"][0]["engine"] == "beanquery"
    assert "dbType" not in out["steps"][0]
    assert "dbType" not in out


def test_invalid_dbtype_is_rejected():
    w = {"id": "w", "title": "W", "query": "SELECT 1", "dbType": "duckdb",
         "visualization": {"type": "kpi"}}
    with pytest.raises(ValueError):
        migrate_widget(w)


def test_migrate_widget_preserves_other_fields():
    w = {"id": "w", "title": "W", "description": "d", "helpText": "h",
         "parameters": [{"name": "year", "label": "Year", "type": "select"}],
         "query": "SELECT 1",
         "visualization": {"type": "kpi", "clickLink": {"name": "transactions", "query": {}}}}
    out = migrate_widget(w)
    assert out["description"] == "d" and out["helpText"] == "h"
    assert out["parameters"] == w["parameters"]
    assert out["visualization"]["clickLink"]["name"] == "transactions"


def test_migrate_widget_idempotent():
    w = migrate_widget({"id": "w", "title": "W", "query": "SELECT 1", "visualization": {"type": "kpi"}})
    assert migrate_widget(w) == w


# ── Dashboard conversion ─────────────────────────────────────────────────────


def _legacy_dashboard():
    return {
        "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": [
            {"widgetId": "inline-w", "gridArea": "1 / 1 / 2 / 2"},
            {"widgetId": "standalone-w", "gridArea": "1 / 2 / 2 / 3"},
        ]},
        "widgets": [{"id": "inline-w", "title": "I", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }


def test_migrate_dashboard_inlines_standalone_and_stamps_version():
    standalone = {"standalone-w": {"id": "standalone-w", "title": "S", "query": "SELECT 2", "visualization": {"type": "kpi"}}}
    inlined: set[str] = set()
    out = migrate_dashboard(_legacy_dashboard(), standalone, inlined)
    assert out["schemaVersion"] == SCHEMA_VERSION
    ids = {w["id"] for w in out["widgets"]}
    assert ids == {"inline-w", "standalone-w"}
    assert "standalone-w" in inlined
    assert validate_dashboard(out) == []


def test_migrate_dashboard_idempotent_on_v2():
    v2 = {"schemaVersion": 2, "id": "d", "title": "D",
          "layout": {"columns": 12, "widgets": []}, "widgets": []}
    assert migrate_dashboard(v2, {}, set()) == v2


# ── Filesystem driver ────────────────────────────────────────────────────────


def _write(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _legacy_tree(root: Path):
    _write(root / "dashboards" / "d.json", _legacy_dashboard())
    _write(root / "widgets" / "standalone-w.json",
           {"id": "standalone-w", "title": "S", "query": "SELECT 2", "visualization": {"type": "kpi"}})
    _write(root / "widgets" / "orphan.json",
           {"id": "orphan", "title": "O", "query": "SELECT 3", "visualization": {"type": "kpi"}})


def test_migrate_dir_inlines_rehomes_orphans_and_drops_widgets_dir(tmp_path: Path):
    root = tmp_path / "recipes"
    _legacy_tree(root)
    report = migrate_recipes_dir(root)

    assert report.migrated_dashboards == ["d.json"]
    assert report.inlined_widgets == ["standalone-w"]
    # The orphan is rehomed into a new one-widget dashboard, not deleted.
    assert report.rehomed_orphans == [("orphan", "orphan")]
    assert not (root / "widgets").exists()  # dir dropped after inlining + rehoming

    migrated = json.loads((root / "dashboards" / "d.json").read_text())
    assert migrated["schemaVersion"] == 2
    assert validate_dashboard(migrated) == []

    # The rehomed orphan is now a valid, viewable dashboard.
    rehomed = json.loads((root / "dashboards" / "orphan.json").read_text())
    assert rehomed["schemaVersion"] == 2
    assert validate_dashboard(rehomed) == []
    assert rehomed["widgets"][0]["id"] == "orphan"


def test_orphan_rehome_avoids_dashboard_id_collision(tmp_path: Path):
    root = tmp_path / "recipes"
    # An orphan widget whose id collides with an existing dashboard id.
    _write(root / "dashboards" / "report.json", {
        "schemaVersion": 2, "id": "report", "title": "Report",
        "layout": {"columns": 12, "widgets": []}, "widgets": [
            {"id": "w", "title": "W", "steps": [{"id": "s", "kind": "query", "query": "SELECT 1"}], "output": "s", "visualization": {"type": "kpi"}}
        ],
    })
    _write(root / "widgets" / "report.json",
           {"id": "report", "title": "Report Widget", "query": "SELECT 1", "visualization": {"type": "kpi"}})
    report = migrate_recipes_dir(root)
    # The wrapper got a non-colliding id; both dashboards exist.
    oid, dash_id = report.rehomed_orphans[0]
    assert oid == "report" and dash_id != "report"
    assert (root / "dashboards" / "report.json").exists()
    assert (root / "dashboards" / f"{dash_id}.json").exists()


def test_migrate_dir_idempotent(tmp_path: Path):
    root = tmp_path / "recipes"
    _legacy_tree(root)
    migrate_recipes_dir(root)
    before = (root / "dashboards" / "d.json").read_text()
    report2 = migrate_recipes_dir(root)
    assert report2.migrated_dashboards == []
    # Both the migrated dashboard and the rehomed orphan are already v2 → skipped.
    assert "d.json" in report2.skipped_already_v2
    assert "orphan.json" in report2.skipped_already_v2
    assert (root / "dashboards" / "d.json").read_text() == before  # byte-identical


def test_migrate_dir_skips_malformed_file_and_reports(tmp_path: Path):
    root = tmp_path / "recipes"
    (root / "dashboards").mkdir(parents=True)
    (root / "dashboards" / "broken.json").write_text("{ not valid json", encoding="utf-8")
    _write(root / "dashboards" / "good.json",
           {"id": "g", "title": "G", "layout": {"columns": 12, "widgets": []},
            "widgets": [{"id": "g", "title": "G", "query": "SELECT 1", "visualization": {"type": "kpi"}}]})
    # The legacy 'good' dashboard has no layout ref to its widget; give it one.
    good = json.loads((root / "dashboards" / "good.json").read_text())
    good["layout"]["widgets"] = [{"widgetId": "g", "gridArea": "1 / 1 / 2 / 2"}]
    (root / "dashboards" / "good.json").write_text(json.dumps(good), encoding="utf-8")

    report = migrate_recipes_dir(root)
    assert any("broken.json" in e["path"] for e in report.errors)  # errors are {path, reason}
    assert "good.json" in report.migrated_dashboards  # the good file still migrates


# ── apply_recipe_migration: nothing deleted without a backup ─────────────────


def test_apply_backs_up_dashboards_and_removed_widgets(tmp_path: Path):
    root = tmp_path / "config" / "recipes"
    _legacy_tree(root)  # 1 dashboard, 1 referenced widget, 1 orphan widget

    apply_recipe_migration(root)

    # Dashboard migrated in place, with a timestamped .backup beside it.
    migrated = json.loads((root / "dashboards" / "d.json").read_text())
    assert migrated["schemaVersion"] == 2
    assert list((root / "dashboards").glob("d.json.*.backup")), "dashboard not backed up"

    # The widgets/ dir is gone, but every removed widget left a .backup first.
    assert not (root / "widgets").exists()
    names = {b.name.split(".json")[0] for b in tmp_path.rglob("*.json.*.backup")}
    assert "standalone-w" in names  # inlined widget backed up before removal
    assert "orphan" in names        # rehomed orphan's source backed up before removal

    # The orphan survives as a viewable dashboard (rehomed, not lost).
    assert (root / "dashboards" / "orphan.json").exists()


def test_apply_idempotent_and_safe_on_already_v2(tmp_path: Path):
    root = tmp_path / "config" / "recipes"
    _legacy_tree(root)
    apply_recipe_migration(root)
    before = (root / "dashboards" / "d.json").read_text()
    bak_count = len(list(tmp_path.rglob("*.backup")))

    apply_recipe_migration(root)  # second launch
    assert (root / "dashboards" / "d.json").read_text() == before
    # No new backups churned on the no-op second run.
    assert len(list(tmp_path.rglob("*.backup"))) == bak_count
