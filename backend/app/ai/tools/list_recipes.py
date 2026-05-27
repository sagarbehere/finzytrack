import json
import logging
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _summarise_widget(path: Path) -> dict:
    """Extract id, title, description, and visualization shape from a widget file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"path": str(path.name), "error": f"failed to read: {e}"}

    viz = data.get("visualization") or {}
    vtype = viz.get("type")
    shape = vtype
    if vtype == "chart":
        ct = viz.get("chartType")
        if ct:
            shape = f"chart:{ct}"
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "description": data.get("description"),
        "shape": shape,
    }


def _summarise_dashboard(path: Path) -> dict:
    """Extract id, title, description, parameters, and widget count from a dashboard."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"path": str(path.name), "error": f"failed to read: {e}"}

    layout = data.get("layout") or {}
    layout_widgets = layout.get("widgets") or []
    param_names = [p.get("name") for p in (data.get("parameters") or []) if isinstance(p, dict)]
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "description": data.get("description"),
        "widget_count": len(layout_widgets),
        "parameters": param_names,
    }


class ListRecipesTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_recipes"

    @property
    def description(self) -> str:
        return (
            "List all available dashboard and widget recipes with one-line summaries "
            "(id, title, description, and shape). Use this to pick the closest "
            "existing recipe(s) to model a new request on — read up to 3 with "
            "read_recipe before drafting. Cheaper than read_recipe; call this first."
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
        # Auto-discover from the filesystem — no manifest file is maintained.
        def _scan(subfolder: str) -> list[str]:
            dir_path = self._recipes_dir / subfolder
            if not dir_path.is_dir():
                return []
            return sorted(f"{subfolder}/{p.name}" for p in dir_path.glob("*.json"))

        widgets = []
        for rel in _scan("widgets"):
            summary = _summarise_widget(self._recipes_dir / rel)
            summary["path"] = rel
            widgets.append(summary)

        dashboards = []
        for rel in _scan("dashboards"):
            summary = _summarise_dashboard(self._recipes_dir / rel)
            summary["path"] = rel
            dashboards.append(summary)

        return {"success": True, "widgets": widgets, "dashboards": dashboards}
