"""
write_recipe tool — validates and saves a dashboard recipe JSON to config/recipes/.

Validation layers:
1. Structural — required fields, valid enum values, layout consistency
2. SQL dry-run — executes each widget's query to verify it runs without error
3. (Auto-discovery — no manifest is maintained; files are picked up by their location)
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


# Match :paramName placeholders in SQL — used to extract parameter names so we
# can pass them as named bindings to SQLite. The regex itself isn't used to
# substitute (which would corrupt :name occurrences inside string literals);
# SQLite's own parser knows about string boundaries when handling :name
# placeholders, so we let it do the work.
_PARAM_NAME_RE = re.compile(r":(\w+)")

# Strip SQL string literals ('...' with '' escapes) and quoted identifiers
# ("..." with "" escapes). Used before scanning for stray $name placeholders
# so we don't false-positive on `'$column'` inside a string literal.
_SQL_STRING_RE = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
_DOLLAR_PARAM_RE = re.compile(r"\$(\w+)")


def _detect_dollar_placeholders(query: str) -> list[str]:
    """Return $name tokens found outside string literals (sorted, unique).

    SQLite supports `$name` as a real parameter syntax, but our recipe
    convention is `:name` for SQL bindings. `$name` appears elsewhere in
    recipes (clickLink filters, formatter currency) as a template referring
    to a result column — never as a SQL parameter. So any bare `$name` in
    SQL is almost always the LLM mixing up the two syntaxes.
    """
    stripped = _SQL_STRING_RE.sub("''", query)
    return sorted(set(_DOLLAR_PARAM_RE.findall(stripped)))


# The client transform catalog (kept in sync with useRecipeTransforms.ts). The
# server can't run the catalog, so fn-name validity is checked against this list
# (§3.6 G8 / §4.11).
KNOWN_TRANSFORMS = {
    "none", "firstRow", "firstValue", "sortBy", "limit", "pluck", "where", "pivot",
    "joinBudgetActual", "joinByPeriod", "budgetSummary", "unbudgetedSpending",
    "appendTotal", "groupBy", "runningSum", "envelopeRollover", "envelopeBalances",
}
# NOTE: hand-mirrors the client transform catalog in
# frontend/src/composables/useRecipeTransforms.ts (transformCatalog). The server
# can't run the catalog, so fn-name validity is checked here (§3.6 G8). Keep the
# two lists in sync when adding a transform.


def _dry_run_query_step(query: str, engine: str, wid: str, sidx: int, sqlite_path: str | None) -> list[str]:
    """Dry-run one query step's text. The SQLite-mirror dry-run only applies to
    the sqlite engine; beanquery (BQL) text is structurally validated but not
    executed here (it isn't SQL).

    Assumes structural/semantic validation already passed (no {{...}} in query,
    refs resolve) — this covers only *execution* concerns."""
    errors: list[str] = []
    if not isinstance(query, str):
        return errors
    label = f"widget '{wid}' steps[{sidx}]"
    if engine == "beanquery":
        return errors
    dollar_names = _detect_dollar_placeholders(query)
    if dollar_names:
        tokens = ", ".join(f"${n}" for n in dollar_names)
        errors.append(
            f"{label}: invalid query parameter syntax — found {tokens}. Bindings "
            f"must use ':name'. The '$name' syntax is for clickLink/formatters."
        )
        return errors
    if not sqlite_path:
        return errors
    param_names = set(_PARAM_NAME_RE.findall(query))
    params = {name: "__dry_run__" for name in param_names}
    try:
        con = sqlite3.connect(sqlite_path, uri=True)
        con.execute("PRAGMA query_only = true")
        con.execute(f"SELECT * FROM ({query}) LIMIT 0", params)
        con.close()
    except (sqlite3.OperationalError, Exception) as e:
        kind = "SQL error" if isinstance(e, sqlite3.OperationalError) else "query validation failed"
        msg = f"{label}: {kind} — {e}"
        hint = _sql_error_hint(str(e))
        if hint:
            msg += f". Hint: {hint}"
        errors.append(msg)
    return errors


def _dry_run_queries(dashboard: dict, sqlite_path: str | None) -> list[str]:
    """Step-aware dry-run (§4.11): for each widget, dry-run query steps, validate
    that compute steps reference a real function with schema-valid args
    (validation-only — not executed), and that transform steps reference a known
    fn wired to declared steps. Returns a list of error strings."""
    import jsonschema
    from app.api.routers.compute import build_registry

    widgets = dashboard.get("widgets", [])
    if not isinstance(widgets, list):
        return []

    registry = build_registry(None)
    errors: list[str] = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        wid = w.get("id", "?")
        steps = w.get("steps", [])
        if not isinstance(steps, list):
            continue
        for sidx, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            kind = s.get("kind")
            if kind == "query":
                errors.extend(_dry_run_query_step(s.get("query", ""), s.get("engine", "sqlite"), wid, sidx, sqlite_path))
            elif kind == "compute":
                fn = registry.get(s.get("fn", ""))
                if fn is None:
                    errors.append(f"widget '{wid}' steps[{sidx}]: unknown compute function '{s.get('fn')}'. Call get_compute_functions to see what exists.")
                    continue
                try:
                    jsonschema.validate(instance=s.get("args", {}), schema=fn.parameters_schema)
                except jsonschema.ValidationError as e:
                    errors.append(f"widget '{wid}' steps[{sidx}]: invalid args for '{s.get('fn')}' — {e.message}")
            elif kind == "transform":
                # Transform input step-refs + acyclicity are checked by
                # validate_dashboard (recipe_validation) before this dry-run; here
                # we only confirm the fn exists in the (server-only) catalog.
                if s.get("fn") not in KNOWN_TRANSFORMS:
                    errors.append(f"widget '{wid}' steps[{sidx}]: unknown transform '{s.get('fn')}'. Known: {sorted(KNOWN_TRANSFORMS)}.")

    return errors


# ── Tool class ───────────────────────────────────────────────────────────────


class WriteRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_recipe"

    @property
    def description(self) -> str:
        return (
            "Save a dashboard recipe to disk (dashboards/). If you already called "
            "preview_recipe, just pass the filename — the previewed recipe is saved "
            "automatically (do NOT re-pass content). "
            "Pass overwrite: true to replace an existing file with the same name."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "JSON filename, e.g. 'net-worth.json'. Saved to dashboards/.",
                },
                "content": {
                    "type": "object",
                    "description": (
                        "The dashboard recipe JSON object. OPTIONAL if you already called "
                        "preview_recipe — the previewed recipe is used automatically."
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
        overwrite: bool = False,
    ) -> dict:
        # Resolve content from preview cache if not provided.
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

        # ── 1. Validation ───────────────────────────────────────────────
        errors = _validate_dashboard(content)
        recipe_id = content.get("id")
        if isinstance(recipe_id, str):
            errors.extend(_validate_id(recipe_id))

        if errors:
            return {
                "success": False,
                "error": "Dashboard validation failed",
                "validation_errors": errors,
                "reference_shape": _reference_shape("JsonDashboardRecipe"),
            }

        # ── 2. Step-aware dry-run (SQL + compute + transform) ───────────
        sql_errors = _dry_run_queries(content, self._sqlite_path)
        if sql_errors:
            return {
                "success": False,
                "error": "Recipe step validation failed",
                "validation_errors": sql_errors,
            }

        # ── 3. Path safety + overwrite check ────────────────────────────
        subfolder = "dashboards"
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

        logger.info(f"Saved dashboard recipe to {save_path}")

        # Recipes are auto-discovered from the filesystem; no manifest to update.
        return {
            "success": True,
            "path": str(save_path),
            "relative_path": f"{subfolder}/{filename}",
            "backup_created": file_existed,
        }
