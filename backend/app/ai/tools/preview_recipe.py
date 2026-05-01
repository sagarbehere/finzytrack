"""
preview_recipe tool — validates a recipe and returns it for live preview.

Supports both widget recipes and dashboard recipes. Widget recipes are
auto-wrapped in a 1-widget dashboard for sidebar rendering.

The last successfully previewed recipe is cached so that write_recipe can save
it without the LLM needing to re-output the entire JSON.
"""

import logging

from app.ai.diagnostics import record_validation_failure
from app.ai.tools.base import BaseTool
from app.ai.tools.write_recipe import _dry_run_queries
from app.helpers.recipe_validation import (
    validate_dashboard as _validate_dashboard,
    validate_id as _validate_id,
    validate_widget as _validate_widget,
)

logger = logging.getLogger(__name__)

# Module-level cache for the last successfully previewed recipe.
# Stores both the original content and its type.
_last_previewed_recipe: dict | None = None
_last_previewed_type: str | None = None  # "widget" or "dashboard"


def get_last_previewed_recipe() -> tuple[dict | None, str | None]:
    return _last_previewed_recipe, _last_previewed_type


def clear_last_previewed_recipe() -> None:
    global _last_previewed_recipe, _last_previewed_type
    _last_previewed_recipe = None
    _last_previewed_type = None


def _wrap_widget_as_dashboard(widget: dict) -> dict:
    """Wrap a widget recipe in a minimal 1-widget dashboard for preview."""
    return {
        "id": f"__preview__{widget.get('id', 'widget')}",
        "title": widget.get("title", "Preview"),
        "parameters": widget.get("parameters", []),
        "layout": {
            "columns": 6,
            "gap": "1.5rem",
            "rowHeight": "200px",
            "widgets": [
                {"widgetId": widget.get("id", "widget"), "gridArea": "1 / 1 / 4 / 7"}
            ],
        },
        "widgets": [widget],
    }


class PreviewRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "preview_recipe"

    @property
    def description(self) -> str:
        return (
            "Validate a recipe and show a live preview in the sidebar. "
            "Accepts both widget recipes and dashboard recipes. "
            "For widgets, the preview auto-wraps it in a single-widget dashboard. "
            "This does NOT save to disk. After the user approves, call write_recipe. "
            "Set recipe_type to 'widget' for a standalone widget, or 'dashboard' for "
            "a full dashboard layout."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "object",
                    "description": "The recipe JSON object (widget or dashboard).",
                },
                "recipe_type": {
                    "type": "string",
                    "enum": ["widget", "dashboard"],
                    "description": "Type of recipe: 'widget' for a single widget, 'dashboard' for a multi-widget layout.",
                },
            },
            "required": ["content", "recipe_type"],
        }

    def __init__(self, sqlite_path: str | None = None):
        self._sqlite_path = sqlite_path

    async def execute(self, content: dict, recipe_type: str = "dashboard") -> dict:
        global _last_previewed_recipe, _last_previewed_type

        recipe_id = content.get("id")
        if isinstance(recipe_id, str):
            id_errors = _validate_id(recipe_id)
            if id_errors:
                return {
                    "success": False,
                    "error": "Validation failed",
                    "validation_errors": id_errors,
                }

        if recipe_type == "widget":
            # Validate as a widget
            errors = _validate_widget(content, "(root)")
            if errors:
                record_validation_failure("preview_recipe", errors, recipe_id=content.get("id"))
                return {
                    "success": False,
                    "error": "Widget validation failed",
                    "validation_errors": errors,
                }

            # SQL dry-run for the single widget
            sql_errors = _dry_run_queries(
                {"widgets": [content]}, self._sqlite_path
            )
            if sql_errors:
                record_validation_failure("preview_recipe.sql", sql_errors, recipe_id=content.get("id"))
                return {
                    "success": False,
                    "error": "SQL query validation failed",
                    "validation_errors": sql_errors,
                }

            # Cache the original widget (not the wrapper)
            _last_previewed_recipe = content
            _last_previewed_type = "widget"

            # Return wrapped version for frontend preview
            preview_dashboard = _wrap_widget_as_dashboard(content)
            return {
                "success": True,
                "message": "Widget validated. A live preview is showing in the sidebar.",
                "recipe": preview_dashboard,
            }
        else:
            # Validate as a dashboard
            errors = _validate_dashboard(content)
            if errors:
                record_validation_failure("preview_recipe", errors, recipe_id=content.get("id"))
                return {
                    "success": False,
                    "error": "Dashboard validation failed",
                    "validation_errors": errors,
                }

            sql_errors = _dry_run_queries(content, self._sqlite_path)
            if sql_errors:
                record_validation_failure("preview_recipe.sql", sql_errors, recipe_id=content.get("id"))
                return {
                    "success": False,
                    "error": "SQL query validation failed",
                    "validation_errors": sql_errors,
                }

            _last_previewed_recipe = content
            _last_previewed_type = "dashboard"

            return {
                "success": True,
                "message": "Dashboard validated. A live preview is showing in the sidebar.",
                "recipe": content,
            }
