import json
import logging
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ListRecipesTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_recipes"

    @property
    def description(self) -> str:
        return (
            "List all available dashboard and widget recipes from the manifest. "
            "Returns the manifest contents showing which recipe files exist. "
            "Use this to see what dashboards and widgets are already defined before "
            "creating new ones."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    def __init__(self, recipes_dir: Path):
        self._recipes_dir = recipes_dir

    async def execute(self) -> dict:
        manifest_path = self._recipes_dir / "manifest.json"
        if not manifest_path.is_file():
            return {"success": False, "error": "No recipe manifest found"}

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Failed to read manifest: {e}"}

        return {"success": True, "manifest": manifest}
