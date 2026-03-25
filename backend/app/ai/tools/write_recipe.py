"""
write_recipe tool — validates and saves a dashboard recipe JSON to config/recipes/.

Validation layers:
1. Structural — required fields, valid enum values, layout consistency
2. SQL dry-run — executes each widget's query to verify it runs without error
3. Manifest update — adds the new file to manifest.json atomically
"""

import json
import logging
import re
import sqlite3
from pathlib import Path

from app.ai.tools.base import BaseTool
from app.core.backup_manager import BackupManager

logger = logging.getLogger(__name__)

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


def _is_non_empty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _validate_value_format(value, field: str) -> list[str]:
    if value is None:
        return []
    if value not in VALID_VALUE_FORMATS:
        return [f"{field}: must be one of {sorted(VALID_VALUE_FORMATS)}"]
    return []


def _validate_parameters(params, prefix: str) -> list[str]:
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
        if not _is_non_empty_str(p.get("name")):
            errors.append(f"{path}.name: required, must be a non-empty string")
        if not _is_non_empty_str(p.get("label")):
            errors.append(f"{path}.label: required, must be a non-empty string")
        if p.get("type") not in VALID_PARAM_TYPES:
            errors.append(f"{path}.type: must be one of {sorted(VALID_PARAM_TYPES)}")
    return errors


def _validate_transform(transform, prefix: str) -> list[str]:
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
        if "field" in transform and not _is_non_empty_str(transform["field"]):
            errors.append(f"{prefix}.field: must be a non-empty string")
        if "order" in transform and transform["order"] not in ("asc", "desc"):
            errors.append(f'{prefix}.order: must be "asc" or "desc"')
    elif t == "limit":
        if "count" in transform and (not isinstance(transform["count"], (int, float)) or transform["count"] < 1):
            errors.append(f"{prefix}.count: must be a positive number")
    elif t == "pluck":
        if not _is_non_empty_str(transform.get("field")):
            errors.append(f"{prefix}.field: required for pluck transform")
    elif t == "pivot":
        for req in ("rowField", "columnField", "valueField"):
            if not _is_non_empty_str(transform.get(req)):
                errors.append(f"{prefix}.{req}: required for pivot transform")
        if "formatColumn" in transform and transform["formatColumn"] not in VALID_FORMAT_COLUMNS:
            errors.append(f"{prefix}.formatColumn: must be one of {sorted(VALID_FORMAT_COLUMNS)}")
        if "sortRowsBy" in transform and transform["sortRowsBy"] not in VALID_SORT_ROWS_BY:
            errors.append(f"{prefix}.sortRowsBy: must be one of {sorted(VALID_SORT_ROWS_BY)}")
    return errors


def _validate_visualization(viz, prefix: str) -> list[str]:
    errors = []
    if not isinstance(viz, dict):
        return [f"{prefix}: required, must be an object"]
    vtype = viz.get("type")
    if vtype not in VALID_VIZ_TYPES:
        return [f"{prefix}.type: must be one of {sorted(VALID_VIZ_TYPES)}"]

    if vtype == "chart":
        ct = viz.get("chartType")
        if not _is_non_empty_str(ct):
            errors.append(f"{prefix}.chartType: required for chart visualization")
        elif ct not in SUPPORTED_CHART_TYPES:
            errors.append(f"{prefix}.chartType: must be one of {sorted(SUPPORTED_CHART_TYPES)}")
        errors.extend(_validate_value_format(viz.get("seriesLabelFormat"), f"{prefix}.seriesLabelFormat"))
        errors.extend(_validate_value_format(viz.get("yAxisLabelFormat"), f"{prefix}.yAxisLabelFormat"))
        errors.extend(_validate_value_format(viz.get("xAxisLabelFormat"), f"{prefix}.xAxisLabelFormat"))
        if "options" in viz and not isinstance(viz["options"], dict):
            errors.append(f"{prefix}.options: must be an object")

    elif vtype == "kpi":
        if "format" in viz:
            errors.extend(_validate_value_format(viz["format"], f"{prefix}.format"))
        if "iconColor" in viz and viz["iconColor"] not in VALID_ICON_COLORS:
            errors.append(f"{prefix}.iconColor: must be one of {sorted(VALID_ICON_COLORS)}")

    elif vtype == "pivot":
        vl = viz.get("valueLink")
        if vl is not None:
            if not isinstance(vl, dict):
                errors.append(f"{prefix}.valueLink: must be an object")
            else:
                if not _is_non_empty_str(vl.get("name")):
                    errors.append(f"{prefix}.valueLink.name: required")
                if not isinstance(vl.get("query"), dict):
                    errors.append(f"{prefix}.valueLink.query: required, must be an object")

    return errors


def _validate_widget(widget, prefix: str) -> list[str]:
    errors = []
    if not isinstance(widget, dict):
        return [f"{prefix}: must be a JSON object"]
    if not _is_non_empty_str(widget.get("id")):
        errors.append(f"{prefix}.id: required, must be a non-empty string")
    if not _is_non_empty_str(widget.get("title")):
        errors.append(f"{prefix}.title: required, must be a non-empty string")
    if not _is_non_empty_str(widget.get("query")):
        errors.append(f"{prefix}.query: required, must be a non-empty string")
    errors.extend(_validate_visualization(widget.get("visualization"), f"{prefix}.visualization"))
    errors.extend(_validate_transform(widget.get("transform"), f"{prefix}.transform"))
    errors.extend(_validate_parameters(widget.get("parameters"), f"{prefix}.parameters"))
    return errors


def _validate_dashboard(dashboard: dict) -> list[str]:
    """Full structural validation of a dashboard recipe."""
    errors = []
    if not isinstance(dashboard, dict):
        return ["(root): must be a JSON object"]
    if not _is_non_empty_str(dashboard.get("id")):
        errors.append("id: required, must be a non-empty string")
    if not _is_non_empty_str(dashboard.get("title")):
        errors.append("title: required, must be a non-empty string")

    errors.extend(_validate_parameters(dashboard.get("parameters"), "parameters"))

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
                if not _is_non_empty_str(w.get("widgetId")):
                    errors.append(f"{path}.widgetId: required")
                if not _is_non_empty_str(w.get("gridArea")):
                    errors.append(f"{path}.gridArea: required")

    # Inline widgets validation
    widgets = dashboard.get("widgets")
    if not isinstance(widgets, list):
        errors.append("widgets: required, must be an array")
    else:
        for i, w in enumerate(widgets):
            errors.extend(_validate_widget(w, f"widgets[{i}]"))

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


def _validate_id(recipe_id: str) -> list[str]:
    """Validate recipe ID format: lowercase alphanumeric + hyphens."""
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", recipe_id) and len(recipe_id) > 1:
        return [
            f"id: '{recipe_id}' is invalid. Use lowercase letters, numbers, and "
            f"hyphens only (e.g. 'my-dashboard-name')"
        ]
    return []


def _dry_run_queries(dashboard: dict, sqlite_path: str | None) -> list[str]:
    """Execute each widget's SQL query to check for errors. Returns list of errors."""
    if not sqlite_path:
        return []
    widgets = dashboard.get("widgets", [])
    if not isinstance(widgets, list):
        return []

    errors = []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            continue
        query = w.get("query")
        if not isinstance(query, str):
            continue

        # Replace :paramName placeholders with dummy values for dry-run
        # This lets us check SQL syntax without needing real parameter values
        dry_query = re.sub(r":(\w+)", "'__dry_run__'", query)

        try:
            con = sqlite3.connect(sqlite_path, uri=True)
            con.execute("PRAGMA query_only = true")
            # Use LIMIT 0 wrapper to avoid actually fetching data
            con.execute(f"SELECT * FROM ({dry_query}) LIMIT 0")
            con.close()
        except sqlite3.OperationalError as e:
            wid = w.get("id", f"index {i}")
            errors.append(f"widgets[{i}] ('{wid}'): SQL error — {e}")
        except Exception as e:
            wid = w.get("id", f"index {i}")
            errors.append(f"widgets[{i}] ('{wid}'): query validation failed — {e}")

    return errors


# ── Tool class ───────────────────────────────────────────────────────────────


class WriteRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_recipe"

    @property
    def description(self) -> str:
        return (
            "Save a dashboard recipe to config/recipes/dashboards/. "
            "If you already called preview_recipe, just pass the filename — the "
            "previewed recipe will be saved automatically (do NOT re-pass content). "
            "If no preview exists, pass content with the full recipe JSON. "
            "Pass overwrite: true to replace an existing dashboard with the same filename."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "JSON filename for the dashboard, e.g. 'spending-overview.json'. "
                        "Will be saved to dashboards/ subfolder."
                    ),
                },
                "content": {
                    "type": "object",
                    "description": (
                        "The full dashboard recipe JSON object. OPTIONAL if you already "
                        "called preview_recipe — the previewed recipe is used automatically."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Set to true to overwrite an existing file. Default: false.",
                },
            },
            "required": ["filename"],
        }

    def __init__(
        self,
        recipes_dir: Path,
        sqlite_path: str | None = None,
        backup_manager: BackupManager | None = None,
    ):
        self._recipes_dir = recipes_dir
        self._sqlite_path = sqlite_path
        self._backup_manager = backup_manager

    async def execute(
        self, filename: str, content: dict | None = None, overwrite: bool = False
    ) -> dict:
        # Resolve content: use cached preview if content not provided
        if content is None:
            from app.ai.tools.preview_recipe import get_last_previewed_recipe
            content = get_last_previewed_recipe()
            if content is None:
                return {
                    "success": False,
                    "error": (
                        "No content provided and no previewed recipe available. "
                        "Either pass the recipe JSON as content, or call preview_recipe first."
                    ),
                }

        # Ensure .json extension
        if not filename.endswith(".json"):
            filename += ".json"

        # ── 1. Structural validation ────────────────────────────────────
        errors = _validate_dashboard(content)

        # Validate ID format
        recipe_id = content.get("id")
        if isinstance(recipe_id, str):
            errors.extend(_validate_id(recipe_id))

        if errors:
            return {
                "success": False,
                "error": "Dashboard validation failed",
                "validation_errors": errors,
            }

        # ── 2. SQL dry-run validation ───────────────────────────────────
        sql_errors = _dry_run_queries(content, self._sqlite_path)
        if sql_errors:
            return {
                "success": False,
                "error": "SQL query validation failed",
                "validation_errors": sql_errors,
            }

        # ── 3. Path safety + overwrite check ────────────────────────────
        dashboards_dir = self._recipes_dir / "dashboards"
        dashboards_dir.mkdir(parents=True, exist_ok=True)

        save_path = (dashboards_dir / filename).resolve()
        if not save_path.is_relative_to(dashboards_dir.resolve()):
            return {"success": False, "error": "Invalid filename — path traversal not allowed"}

        if save_path.exists() and not overwrite:
            return {
                "success": False,
                "error": (
                    f"File 'dashboards/{filename}' already exists. "
                    "Pass overwrite: true to overwrite it, or choose a different filename."
                ),
            }

        # ── 4. Write the file ───────────────────────────────────────────
        file_existed = save_path.exists()
        json_content = json.dumps(content, indent=2, ensure_ascii=False) + "\n"

        if self._backup_manager:
            with self._backup_manager.atomic_write(str(save_path)) as f:
                f.seek(0)
                f.truncate()
                f.write(json_content)
        else:
            save_path.write_text(json_content, encoding="utf-8")

        logger.info(f"Saved dashboard recipe to {save_path}")

        # ── 5. Update manifest ──────────────────────────────────────────
        manifest_path = self._recipes_dir / "manifest.json"
        manifest_entry = f"dashboards/{filename}"

        try:
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                manifest = {"widgets": [], "dashboards": []}

            if manifest_entry not in manifest.get("dashboards", []):
                manifest.setdefault("dashboards", []).append(manifest_entry)
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                logger.info(f"Added '{manifest_entry}' to manifest")
        except Exception as e:
            # Recipe was written successfully; manifest update failed — warn but don't fail
            logger.error(f"Failed to update manifest: {e}")
            return {
                "success": True,
                "path": str(save_path),
                "backup_created": file_existed,
                "warning": f"Recipe saved but manifest update failed: {e}",
            }

        return {
            "success": True,
            "path": str(save_path),
            "manifest_entry": manifest_entry,
            "backup_created": file_existed,
        }
