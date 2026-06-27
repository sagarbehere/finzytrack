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
from app.helpers.recipe_validation import validate_dashboard


# ── Widget conversion ────────────────────────────────────────────────────────


def test_query_only_widget_migrates_to_sql_plus_transform_none():
    w = {"id": "w", "title": "W", "query": "SELECT 1",
         "visualization": {"type": "kpi"}}
    out = migrate_widget(w)
    assert out["steps"] == [
        {"id": "main", "kind": "sql", "query": "SELECT 1"},
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


def test_dbtype_moves_to_sql_step():
    w = {"id": "w", "title": "W", "query": "SELECT 1", "dbType": "beanquery",
         "visualization": {"type": "kpi"}}
    out = migrate_widget(w)
    assert out["steps"][0]["dbType"] == "beanquery"
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


def test_migrate_dir_inlines_removes_orphans_and_drops_widgets_dir(tmp_path: Path):
    root = tmp_path / "recipes"
    _legacy_tree(root)
    report = migrate_recipes_dir(root)

    assert report.migrated_dashboards == ["d.json"]
    assert report.inlined_widgets == ["standalone-w"]
    assert report.deleted_orphans == ["orphan"]
    assert not (root / "widgets").exists()  # dir dropped after inlining + orphan removal

    migrated = json.loads((root / "dashboards" / "d.json").read_text())
    assert migrated["schemaVersion"] == 2
    assert validate_dashboard(migrated) == []


def test_migrate_dir_idempotent(tmp_path: Path):
    root = tmp_path / "recipes"
    _legacy_tree(root)
    migrate_recipes_dir(root)
    before = (root / "dashboards" / "d.json").read_text()
    report2 = migrate_recipes_dir(root)
    assert report2.migrated_dashboards == []
    assert report2.skipped_already_v2 == ["d.json"]
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
    assert any("broken.json" in e for e in report.errors)
    assert "good.json" in report.migrated_dashboards  # the good file still migrates
