"""get_example_widget — return a known-working example widget for a chart/viz type.

The tool reads the curated Widget Gallery dashboard from
``backend/resources/seed_config/recipes/dashboards/widget-gallery.json``. The
gallery is the single source of truth for working examples; adding a new
chart type to it automatically extends this tool's enum without code changes.

Reading from ``seed_config/`` (not from the user's ``config/`` copy) means
the AI's reference templates are insulated from accidental user edits to
the seeded gallery.
"""

from __future__ import annotations

import json
import logging
import sys
from functools import lru_cache
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


# Resolve the seed gallery path — same dev-vs-frozen pattern as elsewhere.
_GALLERY_DIR_DEV = (
    Path(__file__).parents[3] / "resources" / "seed_config" / "recipes" / "dashboards"
)
_GALLERY_DIR_FROZEN = (
    Path(getattr(sys, "_MEIPASS", "")) / "backend" / "seed_config" / "recipes" / "dashboards"
)
_GALLERY_DIR = (
    _GALLERY_DIR_FROZEN if getattr(sys, "frozen", False) else _GALLERY_DIR_DEV
)
GALLERY_PATH: Path = _GALLERY_DIR / "widget-gallery.json"


def _viz_type_key(widget: dict) -> str | None:
    """For a widget, return the type-key used to look it up:
    - 'bar', 'line', 'pie', 'area', 'scatter', 'treemap' for chart widgets
    - 'kpi', 'table', 'pivot' for non-chart widgets
    Returns None if the visualization is malformed."""
    viz = widget.get("visualization")
    if not isinstance(viz, dict):
        return None
    t = viz.get("type")
    if t == "chart":
        return viz.get("chartType")
    return t


@lru_cache(maxsize=1)
def _load_gallery() -> dict:
    if not GALLERY_PATH.is_file():
        raise FileNotFoundError(
            f"widget-gallery.json not found at {GALLERY_PATH}. "
            "Run scripts/sync_ai_reference.py — wait, no — this file is "
            "checked in under backend/resources/seed_config/. If it's "
            "missing, restore from git."
        )
    return json.loads(GALLERY_PATH.read_text(encoding="utf-8"))


def list_supported_types() -> list[str]:
    """Return the type keys the gallery currently provides examples for.
    Used to build the tool's parameter enum dynamically."""
    try:
        gallery = _load_gallery()
    except Exception as e:
        logger.warning("get_example_widget: gallery unreadable (%s)", e)
        return []
    out: list[str] = []
    for w in gallery.get("widgets", []) or []:
        if not isinstance(w, dict):
            continue
        key = _viz_type_key(w)
        if key and key not in out:
            out.append(key)
    return out


def get_example(chart_type: str) -> dict | None:
    """Return the first gallery widget whose visualization matches chart_type."""
    try:
        gallery = _load_gallery()
    except Exception:
        return None
    for w in gallery.get("widgets", []) or []:
        if isinstance(w, dict) and _viz_type_key(w) == chart_type:
            return w
    return None


class GetExampleWidgetTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_example_widget"

    @property
    def description(self) -> str:
        types = list_supported_types()
        if not types:
            return (
                "Return a working example widget for a chart/viz type. "
                "WARNING: the widget gallery is not currently available — "
                "ask the operator to verify backend/resources/seed_config/."
            )
        return (
            "Return a known-working example widget for a chart/visualization "
            "type. The example comes from the curated Widget Gallery dashboard "
            "and is guaranteed to validate and render correctly. Adapt the SQL "
            "and titles to the user's request; keep the structural patterns "
            "(encode, tooltip, formatters, click-through) — those encode the "
            "type's quirks and are easy to get wrong.\n\n"
            "**Call this BEFORE drafting any chart/widget recipe.** You only "
            "need to call once per chart_type per conversation.\n\n"
            f"Available types: {', '.join(types)}.\n\n"
            "Returns the example widget JSON plus a 'key_points' note "
            "describing the type's gotchas (taken from the gallery's helpText)."
        )

    @property
    def parameters_schema(self) -> dict:
        types = list_supported_types() or [
            # Fallback so the schema isn't empty when the gallery is absent.
            "bar", "line", "pie", "area", "scatter", "treemap", "kpi", "table", "pivot",
        ]
        return {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": types,
                    "description": "The visualization type. For charts use the chartType ('bar', 'line', 'pie', etc.); for non-chart widgets use the visualization type ('kpi', 'table', 'pivot').",
                },
            },
            "required": ["chart_type"],
        }

    async def execute(self, chart_type: str) -> dict:
        widget = get_example(chart_type)
        if widget is None:
            available = list_supported_types()
            logger.info("get_example_widget: no example for %r (available=%s)", chart_type, available)
            return {
                "success": False,
                "error": (
                    f"No gallery example for chart_type '{chart_type}'. "
                    f"Available types: {available}."
                ),
            }
        logger.info("get_example_widget served '%s' (id=%s)", chart_type, widget.get("id"))
        return {
            "success": True,
            "chart_type": chart_type,
            "widget": widget,
            "key_points": widget.get("helpText", "").strip() or None,
        }
