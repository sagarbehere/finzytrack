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


# ── Enum sets derived from the schema (never hand-mirrored) ───────────────────
# These value lists ARE the schema's enums/discriminators. Reading them from the
# already-loaded schema means they cannot drift from it — adding an engine or a
# chart type in recipe.schema.json updates every validator here for free.

def _defs() -> dict:
    return _load_schema()["$defs"]


def _enum(def_name: str) -> set[str]:
    """The `enum` list of a top-level $def (e.g. ChartType)."""
    return set(_defs()[def_name]["enum"])


def _discriminator_consts(union_def: str, prop: str) -> set[str]:
    """The `prop` const from each variant of a discriminated-union $def."""
    defs = _defs()
    out: set[str] = set()
    for variant in defs[union_def]["oneOf"]:
        ref = variant["$ref"].rsplit("/", 1)[-1]
        out.add(defs[ref]["properties"][prop]["const"])
    return out


VALID_VIZ_TYPES = _discriminator_consts("JsonRecipeVisualization", "type")
SUPPORTED_CHART_TYPES = _enum("ChartType")
VALID_PARAM_TYPES = set(_defs()["RecipeParameter"]["properties"]["type"]["enum"])
VALID_QUERY_ENGINES = set(_defs()["QueryStep"]["properties"]["engine"]["enum"])
VALID_STEP_KINDS = _discriminator_consts("Step", "kind")
# Transform fn-name validity is a code-side catalog check (§3.6 G8) and lives in
# the AI dry-run (write_recipe.KNOWN_TRANSFORMS), not here.


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


# Hand-curated placeholder values for common field names. The example
# generator falls back to a generic placeholder when a name isn't here.
_FIELD_PLACEHOLDERS: dict[str, str] = {
    "id": "my-recipe-id",
    "widgetId": "my-widget-id",
    "title": "My Title",
    "description": "Brief description",
    "name": "year",
    "label": "Year",
    "query": "SELECT account, SUM(CAST(amount AS REAL)) AS value FROM postings WHERE year = :year GROUP BY account",
    "gridArea": "1 / 1 / 2 / 4",
    "rowField": "account",
    "columnField": "year_month",
    "valueField": "amount",
    "field": "total",
    "formatColumn": "monthYear",
}


_DESC_EG_RE = re.compile(r"e\.g\.\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _description_example(desc: str | None) -> str | None:
    """Extract an example value from a JSON Schema description, e.g. parse
    "Vue route name, e.g. 'transactions'." → "transactions". Lets the schema
    author control example values without a separate registry.
    """
    if not desc:
        return None
    m = _DESC_EG_RE.search(desc)
    return m.group(1) if m else None


def _example_for(node: dict, field_name: str = "", _depth: int = 0) -> object:
    """Generate a minimal-valid example value for a schema node.

    Walks $ref, oneOf (picks first branch), enum (picks first), const, etc.
    Strings prefer (in order): an "e.g. 'X'" hint in the description, the
    field-name placeholder map, then a generic ellipsis.
    Capped at depth 6 to avoid pathological recursion.
    """
    if _depth > 6:
        return "..."

    node = _expand_ref(node)
    if "const" in node:
        return node["const"]
    if isinstance(node.get("enum"), list) and node["enum"]:
        return node["enum"][0]
    if "oneOf" in node and node["oneOf"]:
        return _example_for(node["oneOf"][0], field_name, _depth + 1)
    if "anyOf" in node and node["anyOf"]:
        return _example_for(node["anyOf"][0], field_name, _depth + 1)

    t = node.get("type")
    if t == "string":
        from_desc = _description_example(node.get("description"))
        if from_desc:
            return from_desc
        return _FIELD_PLACEHOLDERS.get(field_name, "...")
    if t == "integer":
        return _FIELD_PLACEHOLDERS_INT.get(field_name, 1)
    if t == "number":
        return _FIELD_PLACEHOLDERS_INT.get(field_name, 1)
    if t == "boolean":
        return False
    if t == "array":
        items = node.get("items") or {}
        return [_example_for(items, field_name, _depth + 1)]
    if t == "object":
        out: dict[str, object] = {}
        required = set(node.get("required") or [])
        for prop, sub in (node.get("properties") or {}).items():
            if prop in required:
                out[prop] = _example_for(sub, prop, _depth + 1)
        return out
    if isinstance(t, list):
        for opt in t:
            return _example_for({"type": opt}, field_name, _depth + 1)
    return "..."


_FIELD_PLACEHOLDERS_INT: dict[str, int] = {
    "columns": 12,
    "count": 10,
    "min": 0,
    "max": 100,
}


def _format_example(value: object) -> str:
    """Compact one-line JSON-ish representation suitable for inline error messages."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ": "))


def _required_shape_hint(parent_schema: dict, prop: str) -> str | None:
    """For a required field that's missing, look up its expected shape from the schema."""
    props = (parent_schema.get("properties") or {})
    sub = props.get(prop)
    if not sub:
        return None
    sub = _expand_ref(sub)
    desc = sub.get("description", "").strip()
    type_str = sub.get("type", "")
    example_value = _example_for(sub, prop)
    example_str = _format_example(example_value)

    def with_example(base: str) -> str:
        # Include the JSON example only when it adds information beyond the
        # type description. Skip for tiny scalars where it's redundant.
        if isinstance(example_value, (str, int, float, bool)):
            return f"{base}. Example: {example_str}"
        if isinstance(example_value, (list, dict)) and example_value:
            return f"{base}. Example: {example_str}"
        return base

    if isinstance(sub.get("enum"), list):
        return with_example(f"one of {sub['enum']}" + (f" — {desc}" if desc else ""))
    if type_str == "string" and sub.get("minLength"):
        return with_example("a non-empty string" + (f" ({desc})" if desc else ""))
    if type_str == "object":
        inner_required = sub.get("required") or []
        if inner_required:
            shape = f"an object with required fields {sorted(inner_required)}"
            return with_example(f"{shape} ({desc})" if desc else shape)
        return with_example(f"an object ({desc})" if desc else "an object")
    if type_str == "array":
        items = _expand_ref(sub.get("items") or {})
        item_desc = items.get("description") or items.get("type") or "items"
        base = f"an array of {item_desc}"
        return with_example(f"{base} ({desc})" if desc else base)
    if type_str:
        return with_example(f"a {type_str} ({desc})" if desc else f"a {type_str}")
    return desc or None


# Common AI-author confusions: when a required key is missing AND a similarly-
# named key is present, suggest the rename. Keyed by the missing key.
_LIKELY_TYPOS: dict[str, list[str]] = {
    "widgetId": ["id", "widget_id", "widget-id", "widgetID"],
    "valueField": ["value", "valueColumn", "valueCol"],
    "rowField": ["row", "rowColumn", "rows"],
    "columnField": ["column", "columnColumn", "columns"],
    "chartType": ["chart_type", "type"],
    "gridArea": ["grid_area", "area", "grid"],
}


def _typo_hint(missing_key: str, parent_obj: object) -> str | None:
    """If a required key is missing AND the parent object contains a likely-typo
    sibling, suggest the rename. Helps weaker models recover from naming slips."""
    if not isinstance(parent_obj, dict):
        return None
    candidates = _LIKELY_TYPOS.get(missing_key, [])
    for c in candidates:
        if c in parent_obj:
            return f"the parent object has a '{c}' field — rename it to '{missing_key}'"
    return None


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

    # Strategy 1: discriminator on 'type' (visualizations) or 'kind' (steps)
    if isinstance(inst, dict):
        disc_key = "type" if "type" in inst else ("kind" if "kind" in inst else None)
        if disc_key is not None:
            for i, branch in enumerate(branches):
                target = _expand_ref(branch)
                const = ((target.get("properties") or {}).get(disc_key) or {}).get("const")
                if const == inst[disc_key] and i in sub_errors_by_branch:
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
        msg = f"{path}: required, must be {shape}" if shape else f"{path}: required"
        # Typo detection: if the parent object has a similar-named field, hint
        # at the rename. err.instance is the parent object whose required key
        # is missing.
        typo = _typo_hint(missing, err.instance)
        if typo:
            msg += f". Hint: {typo}"
        return msg

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


# ── Steps semantics cross-checks (mirror the client validator, §4.8) ─────────

_ANY_TOKEN_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_WHOLE_TOKEN_RE = re.compile(r"^\{\{\s*([^}]+?)\s*\}\}$")
def _extract_refs(text: str) -> list[tuple[str, str]]:
    """Return (scope, id) pairs for every {{...}} reference in a string."""
    refs: list[tuple[str, str]] = []
    for m in _ANY_TOKEN_RE.finditer(text):
        path = m.group(1).strip()
        if path.startswith("dashboard.steps."):
            refs.append(("dashboard.steps", path[len("dashboard.steps."):].split(".")[0]))
        elif path.startswith("steps."):
            refs.append(("steps", path[len("steps."):].split(".")[0]))
        elif path.startswith("params."):
            refs.append(("params", path[len("params."):].split(".")[0]))
        else:
            refs.append(("unknown", path))
    return refs


def _step_refs(step: dict) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if step.get("kind") == "compute" and step.get("args") is not None:
        refs.extend(_extract_refs(json.dumps(step["args"])))
    if step.get("kind") == "transform":
        if isinstance(step.get("inputs"), list):
            for inp in step["inputs"]:
                if isinstance(inp, str):
                    refs.extend(_extract_refs(inp))
        # config may also carry {{steps.x}} refs (e.g. pace bounds) — scan it so
        # those become graph edges for ref-resolution + cycle detection too.
        if step.get("config") is not None:
            refs.extend(_extract_refs(json.dumps(step["config"])))
    return refs


def _validate_steps_semantics(
    steps, prefix: str, known_dashboard_step_ids: set[str]
) -> tuple[list[str], list[str]]:
    """Cross-checks the JSON Schema can't express: unique step ids, references
    resolve, acyclicity, and no {{...}} inside query.query. Returns
    (errors, step_ids). Assumes the structural (schema) pass already ran."""
    errors: list[str] = []
    if not isinstance(steps, list) or not steps:
        return errors, []

    ids: list[str] = []
    seen: set[str] = set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if isinstance(sid, str) and sid:
            if sid in seen:
                errors.append(f"{prefix}[{i}].id: duplicate step id '{sid}'")
            seen.add(sid)
            ids.append(sid)
        if s.get("kind") == "query" and isinstance(s.get("query"), str) and _ANY_TOKEN_RE.search(s["query"]):
            errors.append(
                f"{prefix}[{i}].query: query steps cannot use {{{{...}}}} references — "
                f"use :paramName for parameters; combine other steps in a transform"
            )

    id_set = set(ids)
    adjacency: dict[str, list[str]] = {}
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        local_deps: list[str] = []
        for scope, rid in _step_refs(s):
            if scope == "steps":
                if rid not in id_set:
                    errors.append(f"{prefix}[{i}]: references unknown step '{rid}'")
                else:
                    local_deps.append(rid)
            elif scope == "dashboard.steps":
                if rid not in known_dashboard_step_ids:
                    errors.append(f"{prefix}[{i}]: references unknown dashboard shared step '{rid}'")
            elif scope == "unknown":
                errors.append(f"{prefix}[{i}]: unknown reference scope in '{{{{{rid}}}}}' (use params / steps / dashboard.steps)")
        adjacency[s["id"]] = local_deps

    # Cycle detection
    state: dict[str, str] = {}

    def visit(node: str) -> bool:
        if state.get(node) == "done":
            return False
        if state.get(node) == "visiting":
            return True
        state[node] = "visiting"
        for dep in adjacency.get(node, []):
            if visit(dep):
                return True
        state[node] = "done"
        return False

    for sid in ids:
        if visit(sid):
            errors.append(f"{prefix}: cyclic step reference involving '{sid}'")
            break

    return errors, ids


# ── Public API ──────────────────────────────────────────────────────────────


def _validate_against(definition_name: str, instance, prefix: str) -> list[str]:
    validator = _validator_for(definition_name)
    expanded = list(_expand_errors(validator.iter_errors(instance)))
    expanded.sort(key=lambda e: list(e.absolute_path))
    return [_format_error(prefix, e) for e in expanded]


def _widget_step_checks(widget: dict, prefix: str, known_dashboard_step_ids: set[str]) -> list[str]:
    """Cross-checks the JSON Schema can't express for one inline widget: step
    semantics (unique ids, refs resolve, acyclicity, no {{...}} in query) + the
    output naming a real step. Shared by validate_widget and validate_dashboard's
    per-widget loop so the two can't diverge."""
    errors, step_ids = _validate_steps_semantics(
        widget.get("steps"), f"{prefix}.steps", known_dashboard_step_ids
    )
    output = widget.get("output")
    if isinstance(output, str) and step_ids and output not in step_ids:
        errors.append(f"{prefix}.output: names unknown step '{output}' (steps: {sorted(step_ids)})")
    return errors


def validate_widget(widget, prefix: str, known_dashboard_step_ids: set[str] | None = None) -> list[str]:
    """Validate a single inline widget recipe (steps/output form). Returns a
    list of error strings. `known_dashboard_step_ids` are the ids of dashboard
    shared steps the widget may reference via {{dashboard.steps.x}}."""
    if not isinstance(widget, dict):
        return [f"{prefix}: must be a JSON object, {_describe(widget)}"]
    errors = _validate_against("JsonWidgetRecipe", widget, prefix)
    errors.extend(_widget_step_checks(widget, prefix, known_dashboard_step_ids or set()))
    _check_risky_tooltip_formatters(widget, errors)
    return errors


def validate_visualization(viz, prefix: str) -> list[str]:
    """Validate a visualization config (chart / kpi / table / pivot)."""
    if not isinstance(viz, dict):
        return [f"{prefix}: must be an object with at minimum a 'type' field, {_describe(viz)}"]
    return _validate_against("JsonRecipeVisualization", viz, prefix)


_RISKY_TOOLTIP_RE = re.compile(r"\{c\b")


def _check_risky_tooltip_formatters(content: dict, errors: list[str]) -> None:
    """Walk a recipe and emit warnings for chart visualizations whose tooltip
    has a string formatter containing {c}. ECharts substitutes {c} with the
    full data value, which on dataset-driven charts is the row object and
    renders as the string '[object Object]'. The runtime strips this defensively
    but flagging it at validation time lets the AI fix the recipe at the source.

    Mutates ``errors`` in place by appending one entry per offending widget.
    """
    widgets = content.get("widgets") if isinstance(content, dict) else None
    if not isinstance(widgets, list):
        # Maybe content IS a single widget
        widgets = [content] if isinstance(content, dict) else []

    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            continue
        viz = w.get("visualization")
        if not isinstance(viz, dict) or viz.get("type") != "chart":
            continue
        tooltip = (viz.get("options") or {}).get("tooltip")
        if not isinstance(tooltip, dict):
            continue
        formatter = tooltip.get("formatter")
        if isinstance(formatter, str) and _RISKY_TOOLTIP_RE.search(formatter):
            wid = w.get("id", f"index {i}")
            errors.append(
                f"widgets[{i}] ('{wid}').visualization.options.tooltip.formatter: "
                f"string formatter '{formatter}' contains {{c}}, which resolves "
                f"to the row object on dataset-driven charts and renders as "
                f"'[object Object]'. Remove the formatter and use only "
                f"`tooltip: {{\"trigger\": \"axis\"}}` (or \"item\" for pie/treemap) "
                f"— the runtime auto-formats values with the widget's currency."
            )


def validate_dashboard(dashboard) -> list[str]:
    """Validate a dashboard recipe (the only recipe type) and run cross-checks."""
    if not isinstance(dashboard, dict):
        return [f"(root): must be a JSON object, {_describe(dashboard)}"]
    validator = _validator_for("JsonDashboardRecipe")
    expanded = list(_expand_errors(validator.iter_errors(dashboard)))
    expanded.sort(key=lambda e: list(e.absolute_path))
    errors = [_format_error("", e) for e in expanded]

    # Dashboard shared steps (optional) — validate semantics; collect their ids.
    dashboard_step_ids: set[str] = set()
    if dashboard.get("steps") is not None:
        shared_errors, shared_ids = _validate_steps_semantics(dashboard.get("steps"), "steps", set())
        errors.extend(shared_errors)
        dashboard_step_ids = set(shared_ids)

    # Per-widget cross-checks (steps semantics, output) — widgets are inline-only.
    inline_widgets = dashboard.get("widgets") or []
    if isinstance(inline_widgets, list):
        for i, w in enumerate(inline_widgets):
            if isinstance(w, dict):
                errors.extend(_widget_step_checks(w, f"widgets[{i}]", dashboard_step_ids))

    # Cross-check: every layout widgetId must resolve to an inline widget
    # (widgets are inline-only — there is no registry to fall back to).
    layout = dashboard.get("layout") or {}
    layout_widgets = layout.get("widgets") or []
    if isinstance(layout_widgets, list) and isinstance(inline_widgets, list):
        widget_ids = {w["id"] for w in inline_widgets if isinstance(w, dict) and "id" in w}
        for i, lw_item in enumerate(layout_widgets):
            if isinstance(lw_item, dict):
                wid = lw_item.get("widgetId")
                if wid and wid not in widget_ids:
                    errors.append(
                        f"layout.widgets[{i}].widgetId: '{wid}' has no matching inline widget "
                        f"definition. Available widget ids: {sorted(widget_ids)}."
                    )

    _check_risky_tooltip_formatters(dashboard, errors)
    return errors


def reference_shape(definition_name: str) -> dict:
    """Return a minimal valid example for a top-level type, suitable for
    inclusion in a tool's error response so a weaker model has a concrete
    target to converge on rather than reasoning purely from field paths.

    Example: reference_shape("JsonWidgetRecipe") returns a dict like
    {"id": "my-recipe-id", "title": "My Title", "query": "SELECT ...",
     "visualization": {"type": "chart", "chartType": "bar"}}.
    """
    schema = _load_schema()
    target = schema.get("$defs", {}).get(definition_name)
    if not target:
        return {}
    result = _example_for(target)
    return result if isinstance(result, dict) else {}


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
