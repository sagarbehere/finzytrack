#!/usr/bin/env python3
"""Generate the auto-appendix in schema_recipe_dashboard.md from the JSON Schema.

The schema (frontend/src/types/recipe.schema.json) is the source of truth for
the JSON shape of recipes. The hand-written prose in schema_recipe_dashboard.md
teaches the model how to USE the schema (rules, examples, gotchas); this script
appends a TYPE REFERENCE section listing every $defs entry so the model has the
authoritative shape on hand.

The appendix sits between two markers and is rewritten in-place every run, so
the script is idempotent and safe to run from desktop/build.py or by hand.

Run:
  python scripts/generate_recipe_schema_doc.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "frontend" / "src" / "types" / "recipe.schema.json"
DOC_PATH = ROOT / "backend" / "resources" / "prompts" / "schema_recipe_dashboard.md"

START_MARKER = "<!-- BEGIN AUTO-GENERATED FROM recipe.schema.json — do not edit by hand -->"
END_MARKER = "<!-- END AUTO-GENERATED -->"


def _format_type(node: dict) -> str:
    """Render a JSON Schema type expression in compact form."""
    if "$ref" in node:
        return node["$ref"].split("/")[-1]
    if "const" in node:
        return f"'{node['const']}'"
    if "enum" in node:
        rendered = [f"'{v}'" if isinstance(v, str) else str(v) for v in node["enum"]]
        return " | ".join(rendered)
    if "oneOf" in node:
        return " | ".join(_format_type(opt) for opt in node["oneOf"])
    if "anyOf" in node:
        return " | ".join(_format_type(opt) for opt in node["anyOf"])
    t = node.get("type")
    if isinstance(t, list):
        return " | ".join(t)
    if t == "array":
        item_t = _format_type(node.get("items", {})) if "items" in node else "any"
        return f"{item_t}[]"
    if t == "object":
        if "additionalProperties" in node:
            ap = node["additionalProperties"]
            if isinstance(ap, dict):
                return f"Record<string, {_format_type(ap)}>"
        return "object"
    return t or "any"


def _render_def(name: str, definition: dict) -> list[str]:
    """Return Markdown lines for a single $defs entry."""
    out: list[str] = [f"#### `{name}`"]
    desc = definition.get("description")
    if desc:
        out.append(desc)
    out.append("")

    # Standalone enum / oneOf at the top level — no properties to table
    if definition.get("type") != "object" or "properties" not in definition:
        out.append(f"Type: `{_format_type(definition)}`")
        out.append("")
        return out

    required = set(definition.get("required", []))
    out.append("| Field | Type | Required | Description |")
    out.append("|-------|------|----------|-------------|")
    for prop, schema in definition["properties"].items():
        type_str = _format_type(schema)
        req = "yes" if prop in required else "—"
        prop_desc = (schema.get("description") or "").replace("\n", " ").strip()
        out.append(f"| `{prop}` | `{type_str}` | {req} | {prop_desc} |")
    out.append("")
    return out


def _render_appendix(schema: dict) -> str:
    lines: list[str] = []
    lines.append("### Type reference (generated from `recipe.schema.json`)")
    lines.append("")
    lines.append(
        "The following section is generated from the authoritative JSON Schema. "
        "Use it as the ground truth when the prose above is unclear. The top-level "
        "recipe must match either `JsonWidgetRecipe` or `JsonDashboardRecipe`."
    )
    lines.append("")

    # Render $defs in a stable order: top-level recipe types first, then everything else alphabetically.
    defs = schema.get("$defs", {})
    primary = ["JsonDashboardRecipe", "JsonWidgetRecipe"]
    rest = sorted(name for name in defs if name not in primary)
    for name in primary + rest:
        if name in defs:
            lines.extend(_render_def(name, defs[name]))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if not SCHEMA_PATH.is_file():
        print(f"ERROR: schema not found at {SCHEMA_PATH}", file=sys.stderr)
        return 1
    if not DOC_PATH.is_file():
        print(f"ERROR: doc not found at {DOC_PATH}", file=sys.stderr)
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    appendix = _render_appendix(schema)

    doc = DOC_PATH.read_text(encoding="utf-8")
    block = f"{START_MARKER}\n\n{appendix}\n{END_MARKER}\n"

    if START_MARKER in doc and END_MARKER in doc:
        before, _, rest = doc.partition(START_MARKER)
        _, _, after = rest.partition(END_MARKER)
        # `after` starts with the trailing newline already present after END_MARKER
        new_doc = before + block + after.lstrip("\n")
    else:
        # First run — append at end with one blank line of separation
        sep = "" if doc.endswith("\n\n") else ("\n" if doc.endswith("\n") else "\n\n")
        new_doc = doc + sep + block

    if new_doc != doc:
        DOC_PATH.write_text(new_doc, encoding="utf-8")
        print(f"  generated appendix in {DOC_PATH.relative_to(ROOT)}")
    else:
        print(f"  appendix already up to date in {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
