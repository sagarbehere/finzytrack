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
from app.helpers.recipe_validation import (
    reference_shape as _reference_shape,
    validate_dashboard as _validate_dashboard,
    validate_id as _validate_id,
    validate_widget as _validate_widget,
)

logger = logging.getLogger(__name__)


_NO_COLUMN_RE = re.compile(r"no such column: (\S+)", re.IGNORECASE)
_NO_TABLE_RE = re.compile(r"no such table: (\S+)", re.IGNORECASE)


def _sql_error_hint(message: str) -> str | None:
    """Return a one-line hint for common SQL errors, or None if nothing to add."""
    m = _NO_COLUMN_RE.search(message)
    if m:
        col = m.group(1)
        return (
            f"column '{col}' is not in the postings table. Check the postings schema "
            f"(transaction_date, transaction_payee, account, account_type, amount, "
            f"currency, year, year_month, quarter, …). For other tables run "
            f"`PRAGMA table_info(<table>)` via execute_query."
        )
    m = _NO_TABLE_RE.search(message)
    if m:
        return (
            f"table '{m.group(1)}' does not exist. Standard tables: postings, accounts, "
            f"account_balances, commodities, prices, lots, balance_assertions. "
            f"Run `SELECT name FROM sqlite_master WHERE type='table'` to list them."
        )
    if "syntax error" in message.lower() and ":" in message:
        return (
            "syntax error near a colon — placeholders like ':year' are valid in the "
            "stored query but the dry-run substitutes them with '__dry_run__'. If the "
            "real query relies on numeric placeholders, cast them: CAST(:year AS INTEGER)."
        )
    return None


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
        except (sqlite3.OperationalError, Exception) as e:
            wid = w.get("id", f"index {i}")
            kind = "SQL error" if isinstance(e, sqlite3.OperationalError) else "query validation failed"
            msg = f"widgets[{i}] ('{wid}'): {kind} — {e}"
            hint = _sql_error_hint(str(e))
            if hint:
                msg += f". Hint: {hint}"
            errors.append(msg)

    return errors


# ── Tool class ───────────────────────────────────────────────────────────────


class WriteRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_recipe"

    @property
    def description(self) -> str:
        return (
            "Save a recipe to disk. Saves widgets to widgets/ and dashboards to dashboards/. "
            "If you already called preview_recipe, just pass the filename — the previewed "
            "recipe is saved automatically (do NOT re-pass content). The recipe_type is also "
            "remembered from the preview. "
            "Pass overwrite: true to replace an existing file with the same name."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "JSON filename, e.g. 'net-worth-kpi.json'. Saved to widgets/ "
                        "or dashboards/ based on recipe_type."
                    ),
                },
                "content": {
                    "type": "object",
                    "description": (
                        "The recipe JSON object. OPTIONAL if you already called "
                        "preview_recipe — the previewed recipe is used automatically."
                    ),
                },
                "recipe_type": {
                    "type": "string",
                    "enum": ["widget", "dashboard"],
                    "description": (
                        "Type of recipe. OPTIONAL if preview_recipe was called — "
                        "remembered automatically. Otherwise required."
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
        self,
        filename: str,
        content: dict | None = None,
        recipe_type: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        # Resolve content and type from preview cache if not provided
        if content is None or recipe_type is None:
            from app.ai.tools.preview_recipe import get_last_previewed_recipe
            cached_content, cached_type = get_last_previewed_recipe()
            if content is None:
                content = cached_content
            if recipe_type is None:
                recipe_type = cached_type

        if content is None:
            return {
                "success": False,
                "error": (
                    "No content provided and no previewed recipe available. "
                    "Either pass the recipe JSON as content, or call preview_recipe first."
                ),
            }
        if recipe_type is None:
            recipe_type = "dashboard"  # default

        # Ensure .json extension
        if not filename.endswith(".json"):
            filename += ".json"

        # ── 1. Validation ───────────────────────────────────────────────
        if recipe_type == "widget":
            errors = _validate_widget(content, "(root)")
        else:
            errors = _validate_dashboard(content)

        recipe_id = content.get("id")
        if isinstance(recipe_id, str):
            errors.extend(_validate_id(recipe_id))

        if errors:
            ref_def = "JsonWidgetRecipe" if recipe_type == "widget" else "JsonDashboardRecipe"
            return {
                "success": False,
                "error": f"{'Widget' if recipe_type == 'widget' else 'Dashboard'} validation failed",
                "validation_errors": errors,
                "reference_shape": _reference_shape(ref_def),
            }

        # ── 2. SQL dry-run ──────────────────────────────────────────────
        if recipe_type == "widget":
            sql_errors = _dry_run_queries({"widgets": [content]}, self._sqlite_path)
        else:
            sql_errors = _dry_run_queries(content, self._sqlite_path)
        if sql_errors:
            return {
                "success": False,
                "error": "SQL query validation failed",
                "validation_errors": sql_errors,
            }

        # ── 3. Path safety + overwrite check ────────────────────────────
        subfolder = "widgets" if recipe_type == "widget" else "dashboards"
        target_dir = self._recipes_dir / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)

        save_path = (target_dir / filename).resolve()
        if not save_path.is_relative_to(target_dir.resolve()):
            return {"success": False, "error": "Invalid filename — path traversal not allowed"}

        if save_path.exists() and not overwrite:
            return {
                "success": False,
                "error": (
                    f"File '{subfolder}/{filename}' already exists. "
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

        logger.info(f"Saved {recipe_type} recipe to {save_path}")

        # ── 5. Update manifest ──────────────────────────────────────────
        manifest_path = self._recipes_dir / "manifest.json"
        manifest_entry = f"{subfolder}/{filename}"
        manifest_key = "widgets" if recipe_type == "widget" else "dashboards"

        try:
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                manifest = {"widgets": [], "dashboards": []}

            if manifest_entry not in manifest.get(manifest_key, []):
                manifest.setdefault(manifest_key, []).append(manifest_entry)
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                logger.info(f"Added '{manifest_entry}' to manifest")
        except Exception as e:
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
