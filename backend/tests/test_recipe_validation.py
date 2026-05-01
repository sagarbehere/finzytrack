"""Unit tests for the recipe validator.

These tests assert on the diagnostic shape — every error includes a field path,
an expected description, and (when applicable) the offending value. The goal is
to keep error messages informative for the AI assistant so it can self-correct.
"""

from app.helpers.recipe_validation import (
    validate_dashboard,
    validate_id,
    validate_transform,
    validate_visualization,
    validate_widget,
)


# ── Widget structural validation ────────────────────────────────────────────


def test_widget_missing_id_reports_required_and_path():
    errors = validate_widget({"title": "T", "query": "SELECT 1", "visualization": {"type": "kpi"}}, "(root)")
    assert any("(root).id" in e and "required" in e for e in errors), errors


def test_widget_wrong_type_id_includes_got():
    errors = validate_widget({"id": 42, "title": "T", "query": "SELECT 1", "visualization": {"type": "kpi"}}, "(root)")
    assert any("(root).id" in e and "got 42" in e for e in errors), errors


def test_widget_missing_query_describes_expected():
    errors = validate_widget({"id": "x", "title": "T", "visualization": {"type": "kpi"}}, "(root)")
    assert any("(root).query" in e and "SQL SELECT" in e for e in errors), errors


# ── Visualization ───────────────────────────────────────────────────────────


def test_unknown_chart_type_lists_valid_options_and_got():
    errors = validate_visualization({"type": "chart", "chartType": "piechart"}, "viz")
    assert len(errors) == 1
    assert "viz.chartType" in errors[0]
    assert "got 'piechart'" in errors[0]
    assert "'bar'" in errors[0] and "'pie'" in errors[0]


def test_unknown_viz_type_returns_single_error_with_got():
    errors = validate_visualization({"type": "graph"}, "viz")
    assert len(errors) == 1
    assert "viz.type" in errors[0]
    assert "got 'graph'" in errors[0]


def test_kpi_unknown_icon_color_lists_options():
    errors = validate_visualization({"type": "kpi", "iconColor": "orange"}, "viz")
    assert any("iconColor" in e and "got 'orange'" in e and "'amber'" in e for e in errors)


def test_pivot_value_link_missing_name_reports_path():
    errors = validate_visualization({"type": "pivot", "valueLink": {"query": {}}}, "viz")
    assert any("viz.valueLink.name" in e for e in errors), errors


# ── Transform ───────────────────────────────────────────────────────────────


def test_unknown_simple_transform_includes_hint_about_object_form():
    errors = validate_transform("smooshed", "transform")
    assert len(errors) == 1
    assert "got 'smooshed'" in errors[0]
    assert "object" in errors[0]  # hint about object transforms


def test_pivot_transform_missing_required_fields_lists_each():
    errors = validate_transform({"type": "pivot"}, "transform")
    paths = {e.split(":")[0] for e in errors}
    assert "transform.rowField" in paths
    assert "transform.columnField" in paths
    assert "transform.valueField" in paths


# ── Dashboard cross-checks ──────────────────────────────────────────────────


def test_layout_widget_id_with_no_match_lists_available_ids():
    dashboard = {
        "id": "d",
        "title": "D",
        "layout": {"widgets": [{"widgetId": "ghost", "gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [{"id": "real", "title": "R", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }
    errors = validate_dashboard(dashboard)
    msg = next((e for e in errors if "ghost" in e), None)
    assert msg is not None
    assert "Available widget ids" in msg
    assert "'real'" in msg


def test_dashboard_layout_missing_describes_shape():
    errors = validate_dashboard({"id": "d", "title": "D", "widgets": []})
    assert any("layout" in e and "widgets" in e for e in errors), errors


def test_grid_area_wrong_type_includes_format_hint():
    dashboard = {
        "id": "d",
        "title": "D",
        "layout": {"widgets": [{"widgetId": "w", "gridArea": 1}]},
        "widgets": [{"id": "w", "title": "W", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }
    errors = validate_dashboard(dashboard)
    assert any("gridArea" in e and "row-start" in e for e in errors), errors


# ── ID format ───────────────────────────────────────────────────────────────


def test_invalid_id_includes_example():
    errors = validate_id("My_Dashboard")
    assert errors and "lowercase" in errors[0] and "my-dashboard-name" in errors[0]


def test_valid_id_passes():
    assert validate_id("my-dashboard-1") == []
