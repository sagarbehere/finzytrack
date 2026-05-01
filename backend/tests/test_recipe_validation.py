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


# ── Shape examples on required errors ───────────────────────────────────────


def test_required_query_includes_concrete_sql_example():
    errors = validate_widget(
        {"id": "x", "title": "T", "visualization": {"type": "kpi"}}, "(root)"
    )
    msg = next(e for e in errors if ".query:" in e)
    assert "Example:" in msg
    assert "SELECT" in msg


def test_required_widgetId_example_pulls_placeholder():
    from app.helpers.recipe_validation import validate_dashboard
    d = {
        "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": [{"gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [{"id": "w", "title": "W", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }
    errors = validate_dashboard(d)
    msg = next(e for e in errors if "widgetId" in e)
    assert 'Example: "my-widget-id"' in msg


def test_clickLink_name_example_pulled_from_description_eg():
    """The schema description says 'Vue route name, e.g. 'transactions''. The
    example generator should parse 'transactions' from that description."""
    from app.helpers.recipe_validation import validate_visualization
    errors = validate_visualization(
        {"type": "chart", "chartType": "bar", "clickLink": {}}, "viz"
    )
    msg = next(e for e in errors if "clickLink.name" in e)
    assert 'Example: "transactions"' in msg


# ── Typo hints ──────────────────────────────────────────────────────────────


def test_widgetId_typo_hint_when_id_present_instead():
    from app.helpers.recipe_validation import validate_dashboard
    d = {
        "id": "d", "title": "D",
        "layout": {"columns": 12, "widgets": [{"id": "w", "gridArea": "1 / 1 / 2 / 2"}]},
        "widgets": [{"id": "w", "title": "W", "query": "SELECT 1", "visualization": {"type": "kpi"}}],
    }
    errors = validate_dashboard(d)
    msg = next(e for e in errors if "widgetId" in e)
    assert "rename it to 'widgetId'" in msg


# ── Reference shape ─────────────────────────────────────────────────────────


def test_reference_shape_for_widget_has_all_required_fields():
    from app.helpers.recipe_validation import reference_shape
    shape = reference_shape("JsonWidgetRecipe")
    for field in ("id", "title", "query", "visualization"):
        assert field in shape
    assert shape["visualization"].get("type") in ("chart", "kpi", "table", "pivot")


def test_reference_shape_for_dashboard_includes_layout():
    from app.helpers.recipe_validation import reference_shape
    shape = reference_shape("JsonDashboardRecipe")
    assert "layout" in shape
    assert "columns" in shape["layout"]
    assert "widgets" in shape["layout"]


# ── Risky tooltip formatter ─────────────────────────────────────────────────


def test_risky_tooltip_formatter_with_c_placeholder_flagged():
    bad_widget = {
        "id": "w", "title": "W", "query": "SELECT 1",
        "visualization": {
            "type": "chart", "chartType": "bar",
            "options": {"tooltip": {"trigger": "axis", "formatter": "{b}: {c}"}},
        },
    }
    errors = validate_widget(bad_widget, "(root)")
    msg = next((e for e in errors if "tooltip.formatter" in e), None)
    assert msg is not None
    assert "[object Object]" in msg
    assert "trigger" in msg


def test_safe_tooltip_with_only_trigger_passes():
    good_widget = {
        "id": "w", "title": "W", "query": "SELECT 1",
        "visualization": {
            "type": "chart", "chartType": "bar",
            "options": {"tooltip": {"trigger": "axis"}},
        },
    }
    errors = validate_widget(good_widget, "(root)")
    assert not any("tooltip" in e for e in errors)


def test_series_label_formatter_with_b_is_not_flagged():
    """Series-label string formatters are fine — only tooltip.formatter with
    {c} is dangerous on dataset-driven charts."""
    good_widget = {
        "id": "w", "title": "W", "query": "SELECT 1",
        "visualization": {
            "type": "chart", "chartType": "pie",
            "options": {
                "tooltip": {"trigger": "item"},
                "series": [{"type": "pie", "label": {"show": True, "formatter": "{b}: {d}%"}}],
            },
        },
    }
    errors = validate_widget(good_widget, "(root)")
    assert not any("formatter" in e and "tooltip" in e for e in errors), errors
