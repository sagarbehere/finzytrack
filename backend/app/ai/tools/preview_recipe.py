"""
preview_recipe tool — validates a dashboard recipe and returns it for live preview.

Unlike write_recipe, this does NOT save to disk. It performs the same structural
and SQL validation, then returns the validated recipe JSON so the frontend can
render it as a live dashboard in the preview sidebar.

The last successfully previewed recipe is cached so that write_recipe can save
it without the LLM needing to re-output the entire JSON.
"""

import logging

from app.ai.tools.base import BaseTool
from app.ai.tools.write_recipe import (
    _validate_dashboard,
    _validate_id,
    _dry_run_queries,
)

logger = logging.getLogger(__name__)

# Module-level cache for the last successfully previewed recipe.
# Shared between PreviewRecipeTool and WriteRecipeTool within a single
# agent loop (same process, same request).
_last_previewed_recipe: dict | None = None


def get_last_previewed_recipe() -> dict | None:
    return _last_previewed_recipe


def clear_last_previewed_recipe() -> None:
    global _last_previewed_recipe
    _last_previewed_recipe = None


class PreviewRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "preview_recipe"

    @property
    def description(self) -> str:
        return (
            "Validate a dashboard recipe and return it for live preview in the sidebar. "
            "This does NOT save the recipe to disk — it only validates and returns the JSON "
            "so the user can see a live interactive preview of the dashboard. "
            "After the user approves, call write_recipe to save it permanently. "
            "Use this BEFORE write_recipe so the user can see and refine the dashboard."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "object",
                    "description": "The full dashboard recipe JSON object to preview.",
                },
            },
            "required": ["content"],
        }

    def __init__(self, sqlite_path: str | None = None):
        self._sqlite_path = sqlite_path

    async def execute(self, content: dict) -> dict:
        global _last_previewed_recipe

        # Structural validation
        errors = _validate_dashboard(content)
        recipe_id = content.get("id")
        if isinstance(recipe_id, str):
            errors.extend(_validate_id(recipe_id))

        if errors:
            return {
                "success": False,
                "error": "Dashboard validation failed",
                "validation_errors": errors,
            }

        # SQL dry-run
        sql_errors = _dry_run_queries(content, self._sqlite_path)
        if sql_errors:
            return {
                "success": False,
                "error": "SQL query validation failed",
                "validation_errors": sql_errors,
            }

        # Cache for write_recipe
        _last_previewed_recipe = content

        return {
            "success": True,
            "message": "Recipe validated successfully. A live preview is now showing in the sidebar.",
            "recipe": content,
        }
