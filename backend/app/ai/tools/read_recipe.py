import json
import logging
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ReadRecipeTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_recipe"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a recipe file. The path should be relative to the "
            "recipes directory, e.g. 'dashboards/year-summary.json' or "
            "'widgets/expense-treemap.json'. Use list_recipes first to see available files. "
            "Two main reasons to call this: (1) the user explicitly named an existing "
            "recipe and wants something similar, OR (2) the user wants widget-level / "
            "multi-level parameter cascade — read 'dashboards/year-summary.json' or "
            "'dashboards/month-summary.json' for the canonical pattern (gallery widgets "
            "from get_example_widget don't carry widget-level params)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path to the recipe file, e.g. "
                        "'dashboards/year-summary.json' or 'widgets/expense-treemap.json'"
                    ),
                },
            },
            "required": ["path"],
        }

    def __init__(self, recipes_dir: Path):
        self._recipes_dir = recipes_dir

    async def execute(self, path: str) -> dict:
        target = (self._recipes_dir / path).resolve()

        # Path traversal protection
        if not target.is_relative_to(self._recipes_dir.resolve()):
            return {"success": False, "error": "Invalid path — path traversal not allowed"}

        if not target.is_file():
            return {"success": False, "error": f"Recipe file not found: {path}"}

        try:
            content = json.loads(target.read_text(encoding="utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Failed to read recipe: {e}"}

        return {"success": True, "path": path, "content": content}
