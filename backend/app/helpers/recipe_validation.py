"""Shared validation helpers for dashboard/widget recipe JSON files.

Extracted from app.ai.tools.write_recipe so both the AI tool and the
REST API can validate recipes with the same rules.
"""

import re

# ── Validation constants (mirrored from frontend useRecipeValidator.ts) ──────

VALID_VIZ_TYPES = {"kpi", "chart", "table", "pivot"}
SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "area", "scatter", "treemap"}
VALID_VALUE_FORMATS = {
    "currency", "percent", "number", "compact",
    "compactCurrency", "accountName", "accountName2",
}
VALID_PARAM_TYPES = {"date", "select", "number"}
VALID_SIMPLE_TRANSFORMS = {"none", "firstRow", "firstValue"}
VALID_TRANSFORM_TYPES = {"sortBy", "limit", "pluck", "pivot"}
VALID_FORMAT_COLUMNS = {"monthYear", "yearMonth"}
VALID_SORT_ROWS_BY = {"total_desc", "total_asc", "label_asc", "label_desc"}
VALID_ICON_COLORS = {"blue", "green", "red", "purple", "amber"}


# ── Validation helpers ───────────────────────────────────────────────────────


def is_non_empty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def validate_value_format(value, field: str) -> list[str]:
    if value is None:
        return []
    if value not in VALID_VALUE_FORMATS:
        return [f"{field}: must be one of {sorted(VALID_VALUE_FORMATS)}"]
    return []


def validate_parameters(params, prefix: str) -> list[str]:
    if params is None:
        return []
    errors = []
    if not isinstance(params, list):
        return [f"{prefix}: must be an array"]
    for i, p in enumerate(params):
        path = f"{prefix}[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{path}: must be an object")
            continue
        if not is_non_empty_str(p.get("name")):
            errors.append(f"{path}.name: required, must be a non-empty string")
        if not is_non_empty_str(p.get("label")):
            errors.append(f"{path}.label: required, must be a non-empty string")
        if p.get("type") not in VALID_PARAM_TYPES:
            errors.append(f"{path}.type: must be one of {sorted(VALID_PARAM_TYPES)}")
    return errors


def validate_transform(transform, prefix: str) -> list[str]:
    if transform is None:
        return []
    if isinstance(transform, str):
        if transform not in VALID_SIMPLE_TRANSFORMS:
            return [f"{prefix}: unknown transform '{transform}'; must be one of {sorted(VALID_SIMPLE_TRANSFORMS)}"]
        return []
    if not isinstance(transform, dict):
        return [f"{prefix}: must be a string or object"]
    t = transform.get("type")
    if t not in VALID_TRANSFORM_TYPES:
        return [f"{prefix}.type: must be one of {sorted(VALID_TRANSFORM_TYPES)}"]
    errors = []
    if t == "sortBy":
        if "field" in transform and not is_non_empty_str(transform["field"]):
            errors.append(f"{prefix}.field: must be a non-empty string")
        if "order" in transform and transform["order"] not in ("asc", "desc"):
            errors.append(f'{prefix}.order: must be "asc" or "desc"')
    elif t == "limit":
        if "count" in transform and (not isinstance(transform["count"], (int, float)) or transform["count"] < 1):
            errors.append(f"{prefix}.count: must be a positive number")
    elif t == "pluck":
        if not is_non_empty_str(transform.get("field")):
            errors.append(f"{prefix}.field: required for pluck transform")
    elif t == "pivot":
        for req in ("rowField", "columnField", "valueField"):
            if not is_non_empty_str(transform.get(req)):
                errors.append(f"{prefix}.{req}: required for pivot transform")
        if "formatColumn" in transform and transform["formatColumn"] not in VALID_FORMAT_COLUMNS:
            errors.append(f"{prefix}.formatColumn: must be one of {sorted(VALID_FORMAT_COLUMNS)}")
        if "sortRowsBy" in transform and transform["sortRowsBy"] not in VALID_SORT_ROWS_BY:
            errors.append(f"{prefix}.sortRowsBy: must be one of {sorted(VALID_SORT_ROWS_BY)}")
    return errors


def validate_visualization(viz, prefix: str) -> list[str]:
    errors = []
    if not isinstance(viz, dict):
        return [f"{prefix}: required, must be an object"]
    vtype = viz.get("type")
    if vtype not in VALID_VIZ_TYPES:
        return [f"{prefix}.type: must be one of {sorted(VALID_VIZ_TYPES)}"]

    if vtype == "chart":
        ct = viz.get("chartType")
        if not is_non_empty_str(ct):
            errors.append(f"{prefix}.chartType: required for chart visualization")
        elif ct not in SUPPORTED_CHART_TYPES:
            errors.append(f"{prefix}.chartType: must be one of {sorted(SUPPORTED_CHART_TYPES)}")
        errors.extend(validate_value_format(viz.get("seriesLabelFormat"), f"{prefix}.seriesLabelFormat"))
        errors.extend(validate_value_format(viz.get("yAxisLabelFormat"), f"{prefix}.yAxisLabelFormat"))
        errors.extend(validate_value_format(viz.get("xAxisLabelFormat"), f"{prefix}.xAxisLabelFormat"))
        if "options" in viz and not isinstance(viz["options"], dict):
            errors.append(f"{prefix}.options: must be an object")

    elif vtype == "kpi":
        if "format" in viz:
            errors.extend(validate_value_format(viz["format"], f"{prefix}.format"))
        if "iconColor" in viz and viz["iconColor"] not in VALID_ICON_COLORS:
            errors.append(f"{prefix}.iconColor: must be one of {sorted(VALID_ICON_COLORS)}")

    elif vtype == "pivot":
        vl = viz.get("valueLink")
        if vl is not None:
            if not isinstance(vl, dict):
                errors.append(f"{prefix}.valueLink: must be an object")
            else:
                if not is_non_empty_str(vl.get("name")):
                    errors.append(f"{prefix}.valueLink.name: required")
                if not isinstance(vl.get("query"), dict):
                    errors.append(f"{prefix}.valueLink.query: required, must be an object")

    return errors


def validate_widget(widget, prefix: str) -> list[str]:
    """Validate a single widget recipe JSON object."""
    errors = []
    if not isinstance(widget, dict):
        return [f"{prefix}: must be a JSON object"]
    if not is_non_empty_str(widget.get("id")):
        errors.append(f"{prefix}.id: required, must be a non-empty string")
    if not is_non_empty_str(widget.get("title")):
        errors.append(f"{prefix}.title: required, must be a non-empty string")
    if not is_non_empty_str(widget.get("query")):
        errors.append(f"{prefix}.query: required, must be a non-empty string")
    errors.extend(validate_visualization(widget.get("visualization"), f"{prefix}.visualization"))
    errors.extend(validate_transform(widget.get("transform"), f"{prefix}.transform"))
    errors.extend(validate_parameters(widget.get("parameters"), f"{prefix}.parameters"))
    return errors


def validate_dashboard(dashboard: dict) -> list[str]:
    """Full structural validation of a dashboard recipe."""
    errors = []
    if not isinstance(dashboard, dict):
        return ["(root): must be a JSON object"]
    if not is_non_empty_str(dashboard.get("id")):
        errors.append("id: required, must be a non-empty string")
    if not is_non_empty_str(dashboard.get("title")):
        errors.append("title: required, must be a non-empty string")

    errors.extend(validate_parameters(dashboard.get("parameters"), "parameters"))

    # Layout validation
    layout = dashboard.get("layout")
    if not isinstance(layout, dict):
        errors.append("layout: required, must be an object")
    else:
        lw = layout.get("widgets")
        if not isinstance(lw, list):
            errors.append("layout.widgets: required, must be an array")
        else:
            for i, w in enumerate(lw):
                path = f"layout.widgets[{i}]"
                if not isinstance(w, dict):
                    errors.append(f"{path}: must be an object")
                    continue
                if not is_non_empty_str(w.get("widgetId")):
                    errors.append(f"{path}.widgetId: required")
                if not is_non_empty_str(w.get("gridArea")):
                    errors.append(f"{path}.gridArea: required")

    # Inline widgets validation
    widgets = dashboard.get("widgets")
    if not isinstance(widgets, list):
        errors.append("widgets: required, must be an array")
    else:
        for i, w in enumerate(widgets):
            errors.extend(validate_widget(w, f"widgets[{i}]"))

    # Cross-check: every layout widgetId should have a matching widget definition
    if isinstance(layout, dict) and isinstance(layout.get("widgets"), list) and isinstance(widgets, list):
        widget_ids = {w["id"] for w in widgets if isinstance(w, dict) and "id" in w}
        for i, lw_item in enumerate(layout["widgets"]):
            if isinstance(lw_item, dict):
                wid = lw_item.get("widgetId")
                if wid and wid not in widget_ids:
                    errors.append(
                        f"layout.widgets[{i}].widgetId: '{wid}' has no matching "
                        f"widget definition in the widgets array"
                    )

    return errors


def validate_id(recipe_id: str) -> list[str]:
    """Validate recipe ID format: lowercase alphanumeric + hyphens."""
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", recipe_id) and len(recipe_id) > 1:
        return [
            f"id: '{recipe_id}' is invalid. Use lowercase letters, numbers, and "
            f"hyphens only (e.g. 'my-dashboard-name')"
        ]
    return []
