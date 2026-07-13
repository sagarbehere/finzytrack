"""Unit tests for the recipe validator (steps/DAG model).

These tests assert on the diagnostic shape — every error includes a field path,
an expected description, and (when applicable) the offending value — and on the
steps-semantics cross-checks the JSON Schema can't express (unique ids,
reference resolution, acyclicity, no {{...}} in sql.query, output names a step).
See dev-docs/refactored-dashboard-recipes.md §4.8 / §7.6.
"""

from app.helpers.recipe_validation import (
    validate_dashboard,
    validate_id,
    validate_visualization,
    validate_widget,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _steps():
    return [
        {"id": "main", "kind": "query", "query": "SELECT 1"},
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.main}}"]},
    ]


def _widget(**over):
    w = {"id": "w", "title": "W", "steps": _steps(), "output": "out",
         "visualization": {"type": "kpi"}}
    w.update(over)
    return w


def _dashboard(widgets=None, **over):
    widgets = widgets if widgets is not None else [_widget()]
    d = {
        "schemaVersion": 2,
        "id": "d",
        "title": "D",
        "layout": {"columns": 12, "widgets": [{"widgetId": w["id"], "gridArea": "1 / 1 / 2 / 2"} for w in widgets]},
        "widgets": widgets,
    }
    d.update(over)
    return d


# ── Widget structural validation ────────────────────────────────────────────


def test_minimal_widget_is_valid():
    assert validate_widget(_widget(), "(root)") == []


def test_widget_missing_id_reports_required_and_path():
    w = _widget()
    del w["id"]
    errors = validate_widget(w, "(root)")
    assert any("(root).id" in e and "required" in e for e in errors), errors


def test_widget_wrong_type_id_includes_got():
    errors = validate_widget(_widget(id=42), "(root)")
    assert any("(root).id" in e and "got 42" in e for e in errors), errors


def test_widget_missing_steps_reports_required():
    w = _widget()
    del w["steps"]
    errors = validate_widget(w, "(root)")
    assert any("(root).steps" in e and "required" in e for e in errors), errors


# ── Steps semantics (the cross-checks beyond the schema) ─────────────────────


def test_sql_step_with_template_reference_rejected():
    w = _widget(steps=[
        {"id": "main", "kind": "query", "query": "SELECT * WHERE x = {{steps.other}}"},
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.main}}"]},
    ])
    errors = validate_widget(w, "(root)")
    assert any(".query" in e and "{{" in e for e in errors), errors


def test_dangling_step_reference_rejected():
    w = _widget(steps=[
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.ghost}}"]},
    ], output="out")
    errors = validate_widget(w, "(root)")
    assert any("unknown step 'ghost'" in e for e in errors), errors


def test_cyclic_step_reference_rejected():
    w = _widget(steps=[
        {"id": "a", "kind": "transform", "fn": "none", "inputs": ["{{steps.b}}"]},
        {"id": "b", "kind": "transform", "fn": "none", "inputs": ["{{steps.a}}"]},
    ], output="a")
    errors = validate_widget(w, "(root)")
    assert any("cyclic" in e.lower() for e in errors), errors


def test_duplicate_step_ids_rejected():
    w = _widget(steps=[
        {"id": "main", "kind": "query", "query": "SELECT 1"},
        {"id": "main", "kind": "transform", "fn": "none", "inputs": ["{{steps.main}}"]},
    ], output="main")
    errors = validate_widget(w, "(root)")
    assert any("duplicate step id" in e for e in errors), errors


def test_missing_output_rejected():
    w = _widget()
    del w["output"]
    errors = validate_widget(w, "(root)")
    assert any(".output" in e and "required" in e for e in errors), errors


def test_output_naming_nonexistent_step_rejected():
    errors = validate_widget(_widget(output="nope"), "(root)")
    assert any(".output" in e and "unknown step 'nope'" in e for e in errors), errors


def test_transform_step_missing_inputs_rejected():
    w = _widget(steps=[
        {"id": "main", "kind": "query", "query": "SELECT 1"},
        {"id": "out", "kind": "transform", "fn": "none"},
    ])
    errors = validate_widget(w, "(root)")
    assert any("inputs" in e for e in errors), errors


def test_compute_step_missing_fn_rejected():
    w = _widget(steps=[
        {"id": "b", "kind": "compute"},
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{steps.b}}"]},
    ], output="out")
    errors = validate_widget(w, "(root)")
    assert any(".fn" in e for e in errors), errors


def test_legacy_single_query_widget_rejected():
    """A pre-migration widget (query + transform, no steps) must fail."""
    legacy = {"id": "w", "title": "W", "query": "SELECT 1", "transform": "none",
              "visualization": {"type": "kpi"}}
    errors = validate_widget(legacy, "(root)")
    assert errors  # missing steps + output


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


def test_pivot_value_link_malformed_reports_path():
    # A link is a route ({name, query}) OR a select ({select}); a link with
    # neither (here: only query) matches no allowed shape and is reported on the
    # valueLink path.
    errors = validate_visualization({"type": "pivot", "valueLink": {"query": {}}}, "viz")
    assert any("viz.valueLink" in e for e in errors), errors


def test_value_link_route_and_select_both_valid():
    # Route link.
    assert (
        validate_visualization(
            {"type": "pivot", "valueLink": {"name": "transactions", "query": {"a": "{{row.label}}"}}},
            "viz",
        )
        == []
    )
    # Select link (master-detail drill-down).
    assert (
        validate_visualization(
            {"type": "pivot", "valueLink": {"select": {"account": "{{row.label}}"}}},
            "viz",
        )
        == []
    )


def test_value_link_cannot_mix_route_and_select():
    # additionalProperties:false on each branch → a link carrying both a route
    # and a select matches neither closed shape and is rejected.
    errors = validate_visualization(
        {"type": "pivot", "valueLink": {"name": "transactions", "query": {}, "select": {"account": "x"}}},
        "viz",
    )
    assert any("viz.valueLink" in e for e in errors), errors


# ── Dashboard cross-checks ──────────────────────────────────────────────────


def test_layout_widget_id_with_no_match_lists_available_ids():
    dashboard = _dashboard(widgets=[_widget(id="real")])
    dashboard["layout"]["widgets"] = [{"widgetId": "ghost", "gridArea": "1 / 1 / 2 / 2"}]
    errors = validate_dashboard(dashboard)
    msg = next((e for e in errors if "ghost" in e), None)
    assert msg is not None
    assert "Available widget ids" in msg
    assert "'real'" in msg


def test_dashboard_missing_schema_version_rejected():
    d = _dashboard()
    del d["schemaVersion"]
    errors = validate_dashboard(d)
    assert any("schemaVersion" in e for e in errors), errors


def test_dashboard_layout_missing_describes_shape():
    d = _dashboard()
    del d["layout"]
    errors = validate_dashboard(d)
    assert any("layout" in e for e in errors), errors


def test_grid_area_wrong_type_includes_format_hint():
    dashboard = _dashboard()
    dashboard["layout"]["widgets"] = [{"widgetId": "w", "gridArea": 1}]
    errors = validate_dashboard(dashboard)
    assert any("gridArea" in e and "row-start" in e for e in errors), errors


def test_widget_step_may_reference_dashboard_shared_step():
    """A widget step referencing {{dashboard.steps.x}} validates when x is a
    declared dashboard shared step, and fails when it isn't."""
    shared = {"id": "shared", "kind": "compute", "fn": "noop"}
    w = _widget(steps=[
        {"id": "out", "kind": "transform", "fn": "none", "inputs": ["{{dashboard.steps.shared}}"]},
    ], output="out")
    d = _dashboard(widgets=[w], steps=[shared])
    # 'shared' is declared → no unknown-shared-step error.
    errors = validate_dashboard(d)
    assert not any("unknown dashboard shared step" in e for e in errors), errors
    # Remove the shared step → the reference becomes dangling.
    d2 = _dashboard(widgets=[w])
    errors2 = validate_dashboard(d2)
    assert any("unknown dashboard shared step 'shared'" in e for e in errors2), errors2


# ── ID format ───────────────────────────────────────────────────────────────


def test_invalid_id_includes_example():
    errors = validate_id("My_Dashboard")
    assert errors and "lowercase" in errors[0] and "my-dashboard-name" in errors[0]


def test_valid_id_passes():
    assert validate_id("my-dashboard-1") == []


# ── Typo hints ──────────────────────────────────────────────────────────────


def test_widgetId_typo_hint_when_id_present_instead():
    d = _dashboard()
    d["layout"]["widgets"] = [{"id": "w", "gridArea": "1 / 1 / 2 / 2"}]
    errors = validate_dashboard(d)
    msg = next((e for e in errors if "widgetId" in e), None)
    assert msg is not None and "rename it to 'widgetId'" in msg


# ── Reference shape ─────────────────────────────────────────────────────────


def test_reference_shape_for_widget_has_all_required_fields():
    from app.helpers.recipe_validation import reference_shape
    shape = reference_shape("JsonWidgetRecipe")
    for field in ("id", "title", "steps", "output", "visualization"):
        assert field in shape, shape
    assert shape["visualization"].get("type") in ("chart", "kpi", "table", "pivot")


def test_reference_shape_for_dashboard_includes_layout_and_version():
    from app.helpers.recipe_validation import reference_shape
    shape = reference_shape("JsonDashboardRecipe")
    assert shape.get("schemaVersion") == 2
    assert "layout" in shape
    assert "columns" in shape["layout"]
    assert "widgets" in shape["layout"]


# ── Risky tooltip formatter ─────────────────────────────────────────────────


def test_risky_tooltip_formatter_with_c_placeholder_flagged():
    bad_widget = _widget(visualization={
        "type": "chart", "chartType": "bar",
        "options": {"tooltip": {"trigger": "axis", "formatter": "{b}: {c}"}},
    })
    errors = validate_widget(bad_widget, "(root)")
    msg = next((e for e in errors if "tooltip.formatter" in e), None)
    assert msg is not None
    assert "[object Object]" in msg
    assert "trigger" in msg


def test_safe_tooltip_with_only_trigger_passes():
    good_widget = _widget(visualization={
        "type": "chart", "chartType": "bar",
        "options": {"tooltip": {"trigger": "axis"}},
    })
    errors = validate_widget(good_widget, "(root)")
    assert not any("tooltip" in e for e in errors)


def test_series_label_formatter_with_b_is_not_flagged():
    """Series-label string formatters are fine — only tooltip.formatter with
    {c} is dangerous on dataset-driven charts."""
    good_widget = _widget(visualization={
        "type": "chart", "chartType": "pie",
        "options": {
            "tooltip": {"trigger": "item"},
            "series": [{"type": "pie", "label": {"show": True, "formatter": "{b}: {d}%"}}],
        },
    })
    errors = validate_widget(good_widget, "(root)")
    assert not any("formatter" in e and "tooltip" in e for e in errors), errors
