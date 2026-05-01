"""Recipe validation backed by recipe.schema.json (Draft 2020-12).

The schema (frontend/src/types/recipe.schema.json, synced to
backend/resources/schemas/recipe.schema.json) is the single source of truth
for the JSON shape of widget and dashboard recipes. This module exposes
``validate_widget`` and ``validate_dashboard`` that return a list of
``str`` error messages — same surface as the previous hand-written validator
so all callers (AI tools, REST endpoints, tests) keep working.

Each error is formatted via ``_format_error`` so the message style stays
informative for the AI assistant: field path, expected shape, the offending
value (when available), and a hint for common mistakes.
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


# ── Schema loading (dev mode reads from backend/resources/schemas/, frozen reads from bundle) ──

_SCHEMA_DIR_DEV = Path(__file__).parents[2] / "resources" / "schemas"
_SCHEMA_DIR_FROZEN = Path(getattr(sys, "_MEIPASS", "")) / "resources" / "schemas"
_SCHEMA_DIR = _SCHEMA_DIR_FROZEN if getattr(sys, "frozen", False) else _SCHEMA_DIR_DEV
_SCHEMA_PATH = _SCHEMA_DIR / "recipe.schema.json"


@lru_cache(maxsize=1)
def _load_schema() -> dict:
    if not _SCHEMA_PATH.is_file():
        raise FileNotFoundError(
            f"recipe.schema.json not found at {_SCHEMA_PATH}. "
            "Run scripts/sync_ai_reference.py to populate backend/resources/schemas/."
        )
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator_for(definition_name: str) -> Draft202012Validator:
    """Return a validator scoped to a single $defs entry (e.g. JsonWidgetRecipe)."""
    schema = _load_schema()
    return Draft202012Validator(
        {"$ref": f"#/$defs/{definition_name}", "$defs": schema["$defs"]}
    )


# ── Constants surfaced for backwards compat (some callers / tests import these) ──

VALID_VIZ_TYPES = {"kpi", "chart", "table", "pivot"}
SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "area", "scatter", "treemap"}
VALID_PARAM_TYPES = {"date", "select", "number"}
VALID_SIMPLE_TRANSFORMS = {"none", "firstRow", "firstValue"}
VALID_TRANSFORM_TYPES = {"sortBy", "limit", "pluck", "pivot"}


# ── Error formatting ────────────────────────────────────────────────────────


def _describe(value) -> str:
    """Compact representation of a value for diagnostic messages."""
    if value is None:
        return "missing (got null)"
    if isinstance(value, str):
        s = value if len(value) <= 60 else value[:57] + "..."
        return f"got '{s}'"
    if isinstance(value, (int, float, bool)):
        return f"got {value!r}"
    return f"got {type(value).__name__}"


def _path_str(path) -> str:
    """Render a jsonschema absolute_path deque as 'a.b[2].c'."""
    parts: list[str] = []
    for p in path:
        if isinstance(p, int):
            parts.append(f"[{p}]")
        else:
            parts.append(f".{p}" if parts else str(p))
    return "".join(parts) if parts else "(root)"


def _enum_message(field: str, instance, allowed) -> str:
    return f"{field}: must be one of {sorted(allowed)}, {_describe(instance)}"


def _resolve_ref(ref: str) -> dict:
    schema = _load_schema()
    if ref.startswith("#/$defs/"):
        return schema["$defs"].get(ref.split("/")[-1], {})
    return {}


def _expand_ref(node: dict) -> dict:
    return _resolve_ref(node["$ref"]) if "$ref" in node else node


def _required_shape_hint(parent_schema: dict, prop: str) -> str | None:
    """For a required field that's missing, look up its expected shape from the schema."""
    props = (parent_schema.get("properties") or {})
    sub = props.get(prop)
    if not sub:
        return None
    sub = _expand_ref(sub)
    desc = sub.get("description", "").strip()
    type_str = sub.get("type", "")
    if isinstance(sub.get("enum"), list):
        return f"one of {sub['enum']}" + (f" — {desc}" if desc else "")
    if type_str == "string" and sub.get("minLength"):
        return "a non-empty string" + (f" ({desc})" if desc else "")
    if type_str == "object":
        # If the object schema declares its own required fields, list them so the
        # message tells the model what shape to construct.
        inner_required = sub.get("required") or []
        if inner_required:
            shape = f"an object with required fields {sorted(inner_required)}"
            return f"{shape} ({desc})" if desc else shape
        return f"an object ({desc})" if desc else "an object"
    if type_str == "array":
        items = _expand_ref(sub.get("items") or {})
        item_desc = items.get("description") or items.get("type") or "items"
        base = f"an array of {item_desc}"
        return f"{base} ({desc})" if desc else base
    if type_str:
        return f"a {type_str} ({desc})" if desc else f"a {type_str}"
    return desc or None


def _hint_for(field: str, err: ValidationError) -> str | None:
    """Custom hints layered on top of jsonschema's machine-generated messages."""
    is_transform = field == "transform" or field.endswith(".transform")
    if is_transform and err.validator == "oneOf" and isinstance(err.instance, str):
        return (
            "for object transforms (sortBy, limit, pivot, pluck) pass an object "
            "with a 'type' field instead of a string"
        )
    return None


def _join_path(prefix: str, rel: str) -> str:
    """Combine a caller prefix with a jsonschema-derived relative path."""
    if not prefix:
        return rel
    if rel == "(root)":
        return prefix
    # rel is e.g. 'id', 'layout.widgets[0].gridArea' — never starts with a dot here
    sep = "" if rel.startswith("[") else "."
    return f"{prefix}{sep}{rel}"


def _select_branch(err: ValidationError) -> list[ValidationError]:
    """For a oneOf, return sub-errors from the closest-matching branch.

    Two strategies, in order:
      1. Discriminator: instance has a 'type' field that matches one branch's
         `properties.type.const` — return that branch's sub-errors.
      2. Best object match: instance is a dict and exactly one branch is an
         object schema — return its sub-errors. If multiple, pick the branch
         whose own 'required' is most satisfied by the instance (cheap heuristic
         for cases like Transform = string | TransformConfig).
    """
    inst = err.instance
    branches = err.validator_value or []
    sub_errors_by_branch: dict[int, list[ValidationError]] = {}
    for sub in err.context or []:
        if sub.schema_path:
            sub_errors_by_branch.setdefault(sub.schema_path[0], []).append(sub)

    # Strategy 1: type discriminator
    if isinstance(inst, dict) and "type" in inst:
        for i, branch in enumerate(branches):
            target = _expand_ref(branch)
            type_const = ((target.get("properties") or {}).get("type") or {}).get("const")
            if type_const == inst["type"] and i in sub_errors_by_branch:
                return sub_errors_by_branch[i]

    # Strategy 2: object-vs-non-object disambiguation
    if isinstance(inst, dict):
        object_branches = []
        for i, branch in enumerate(branches):
            target = _expand_ref(branch)
            if target.get("type") == "object":
                object_branches.append(i)
        if len(object_branches) == 1 and object_branches[0] in sub_errors_by_branch:
            return sub_errors_by_branch[object_branches[0]]

    return []


def _format_error(prefix: str, err: ValidationError) -> str:
    """Map a jsonschema ValidationError to our diagnostic message format."""
    rel = _path_str(err.absolute_path)
    field = _join_path(prefix, rel)
    v = err.validator
    inst = err.instance

    if v == "required":
        m = re.match(r"'([^']+)' is a required property", err.message)
        missing = m.group(1) if m else err.validator_value
        # Look up the expected shape from the parent schema
        parent_schema = err.schema or {}
        parent_schema = _expand_ref(parent_schema)
        shape = _required_shape_hint(parent_schema, missing)
        path = f"{field}.{missing}" if field else missing
        return f"{path}: required, must be {shape}" if shape else f"{path}: required"

    if v == "type":
        expected = err.validator_value
        if isinstance(expected, list):
            expected = " or ".join(expected)
        msg = f"{field}: must be {expected}, {_describe(inst)}"
        # Hint for gridArea-shaped strings
        if rel.endswith("gridArea") and expected == "string":
            msg += ". Hint: expected 'row-start / col-start / row-end / col-end' (1-based, e.g. '1 / 1 / 2 / 4')"
        return msg

    if v == "enum":
        return _enum_message(field, inst, set(err.validator_value))

    if v == "const":
        return f"{field}: must be {err.validator_value!r}, {_describe(inst)}"

    if v == "minLength":
        return f"{field}: must be a non-empty string, {_describe(inst)}"

    if v == "minimum":
        return f"{field}: must be >= {err.validator_value}, {_describe(inst)}"

    if v == "pattern":
        return (
            f"{field}: '{inst}' is invalid. "
            f"Use lowercase letters, numbers, and hyphens only — "
            f"must start and end alphanumeric (e.g. 'my-dashboard-name')"
        )

    if v == "oneOf":
        # If a branch can be selected (by discriminator or object match), surface
        # its first sub-error rather than the generic 'no shape matches'.
        subs = _select_branch(err)
        if subs:
            return _format_error(prefix, subs[0])
        if isinstance(inst, dict) and "type" in inst:
            allowed = _oneof_allowed_types(err)
            if allowed and inst["type"] not in allowed:
                return _enum_message(f"{field}.type", inst["type"], allowed)
        msg = f"{field}: does not match any allowed shape, {_describe(inst)}"
        h = _hint_for(field, err)
        return f"{msg}. Hint: {h}" if h else msg

    if v == "if":
        # Conditional schemas (e.g. 'if type==pivot then required rowField,...').
        # Surface every sub-error from the failing then-branch.
        if err.context:
            return _format_error(prefix, err.context[0])
        return f"{field}: {err.message}"

    msg = f"{field}: {err.message}"
    h = _hint_for(field, err)
    return f"{msg}. Hint: {h}" if h else msg


def _expand_errors(errors_iter):
    """Flatten conditional/oneOf errors so each problem becomes its own line.

    jsonschema reports `if/then` and `oneOf` failures as a single top-level
    error with sub-errors in `.context`. Recursing into context lets us emit
    one line per missing field rather than a single 'does not match'."""
    for err in errors_iter:
        if err.validator == "if" and err.context:
            yield from _expand_errors(err.context)
        elif err.validator == "oneOf":
            subs = _select_branch(err)
            if subs:
                yield from _expand_errors(subs)
            else:
                yield err
        elif err.validator == "allOf" and err.context:
            yield from _expand_errors(err.context)
        else:
            yield err


def _oneof_allowed_types(err: ValidationError) -> set[str]:
    """For a oneOf where each branch is `{type: const}`, return the set of allowed types."""
    branches = err.validator_value or []
    result: set[str] = set()
    for branch in branches:
        # Resolve $ref by reading from $defs of the schema
        if "$ref" in branch:
            ref = branch["$ref"]
            if ref.startswith("#/$defs/"):
                schema = _load_schema()
                target = schema["$defs"].get(ref.split("/")[-1], {})
                t = (target.get("properties") or {}).get("type", {})
                if "const" in t:
                    result.add(t["const"])
        else:
            t = (branch.get("properties") or {}).get("type", {})
            if "const" in t:
                result.add(t["const"])
    return result


# ── Public API ──────────────────────────────────────────────────────────────


def _validate_against(definition_name: str, instance, prefix: str) -> list[str]:
    validator = _validator_for(definition_name)
    expanded = list(_expand_errors(validator.iter_errors(instance)))
    expanded.sort(key=lambda e: list(e.absolute_path))
    return [_format_error(prefix, e) for e in expanded]


def validate_widget(widget, prefix: str) -> list[str]:
    """Validate a single widget recipe. Returns a list of error strings."""
    if not isinstance(widget, dict):
        return [f"{prefix}: must be a JSON object, {_describe(widget)}"]
    return _validate_against("JsonWidgetRecipe", widget, prefix)


def validate_visualization(viz, prefix: str) -> list[str]:
    """Validate a visualization config (chart / kpi / table / pivot)."""
    if not isinstance(viz, dict):
        return [f"{prefix}: must be an object with at minimum a 'type' field, {_describe(viz)}"]
    return _validate_against("JsonRecipeVisualization", viz, prefix)


def validate_transform(transform, prefix: str) -> list[str]:
    """Validate a transform (simple string or object form)."""
    if transform is None:
        return []
    return _validate_against("Transform", transform, prefix)


def validate_dashboard(dashboard) -> list[str]:
    """Validate a dashboard recipe and run the cross-check pass."""
    if not isinstance(dashboard, dict):
        return [f"(root): must be a JSON object, {_describe(dashboard)}"]
    validator = _validator_for("JsonDashboardRecipe")
    expanded = list(_expand_errors(validator.iter_errors(dashboard)))
    expanded.sort(key=lambda e: list(e.absolute_path))
    errors = [_format_error("", e) for e in expanded]

    # Cross-check: every layout widgetId must reference *something* — either an
    # inline widget definition in this dashboard's `widgets` array, or a widget
    # registered separately and looked up by id at runtime. We can only see the
    # inline list from here, so we use this heuristic: only flag missing
    # widgetIds when the dashboard *appears* to be self-contained (every other
    # layout widgetId resolves inline). If any widgetId doesn't resolve, we
    # assume the dashboard is using the registry and skip the check — this
    # avoids false positives on mixed inline+registry dashboards while still
    # catching typos in single-widget AI-authored recipes.
    layout = dashboard.get("layout") or {}
    layout_widgets = layout.get("widgets") or []
    inline_widgets = dashboard.get("widgets") or []
    if isinstance(layout_widgets, list) and isinstance(inline_widgets, list):
        widget_ids = {w["id"] for w in inline_widgets if isinstance(w, dict) and "id" in w}
        layout_ids = [
            lw.get("widgetId") for lw in layout_widgets
            if isinstance(lw, dict) and lw.get("widgetId")
        ]
        # Only run the cross-check in two clearly-self-contained shapes:
        #   1. All layout widgetIds resolve inline (no registry lookups expected)
        #   2. The widgets array is non-empty AND every inline id is referenced
        #      (catches typos like a renamed widget)
        all_resolve = layout_ids and all(wid in widget_ids for wid in layout_ids)
        if widget_ids and all_resolve is False and any(wid in widget_ids for wid in layout_ids):
            # Mixed: some resolve, some don't. Skip — assume registry mode for
            # the unresolved ones rather than emit false positives.
            pass
        elif widget_ids and not all_resolve:
            for i, lw_item in enumerate(layout_widgets):
                if isinstance(lw_item, dict):
                    wid = lw_item.get("widgetId")
                    if wid and wid not in widget_ids:
                        available = sorted(widget_ids)
                        errors.append(
                            f"layout.widgets[{i}].widgetId: '{wid}' has no matching widget "
                            f"definition. Available widget ids in this dashboard's 'widgets' "
                            f"array: {available}. Hint: if the widget is saved separately, "
                            f"leave 'widgets: []' and ensure the widgetId matches a registered widget."
                        )

    return errors


def validate_id(recipe_id: str) -> list[str]:
    """Validate recipe ID format. Kept as a separate entry point for callers
    that want to validate just the id (used by preview_recipe / write_recipe
    before the full recipe shape is available)."""
    if not isinstance(recipe_id, str):
        return [f"id: must be a string, {_describe(recipe_id)}"]
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", recipe_id):
        return [
            f"id: '{recipe_id}' is invalid. Use lowercase letters, numbers, and "
            f"hyphens only — must start and end alphanumeric (e.g. 'my-dashboard-name')"
        ]
    return []
