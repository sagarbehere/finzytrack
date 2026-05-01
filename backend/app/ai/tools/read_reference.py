"""read_reference — read a hand-picked source file as authoritative ground truth.

The tool exposes a small allowlist of source files (see app.ai.reference). It
deliberately does NOT accept arbitrary paths — the model picks by name from a
fixed menu enumerated in the tool description, dynamically built from files
that actually exist on disk so the model never sees options it can't use.

Use sparingly: the in-context schema docs cover normal cases. Reach for this
tool only when the schema doc seems insufficient (unusual field, generator
ambiguity, ECharts option that needs verification).
"""

import logging

from app.ai import reference as _ref
from app.ai.reference import ALLOWED_REFERENCES, list_available_references
from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ReadReferenceTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_reference"

    @property
    def description(self) -> str:
        available = list_available_references()
        if not available:
            return (
                "Read a curated source file as authoritative ground truth. "
                "WARNING: no reference files are currently available on this server "
                "— ask the operator to run scripts/sync_ai_reference.py."
            )
        menu = "\n".join(f"  - {r['name']}: {r['description']}" for r in available)
        return (
            "Read a curated source file as authoritative ground truth. Available files:\n"
            f"{menu}\n"
            "Do not call for routine work — the in-context schema docs cover normal cases. "
            "Use this when the schema seems incomplete or you're uncertain about a field's shape."
        )

    @property
    def parameters_schema(self) -> dict:
        # Dynamic enum: only files actually present on disk. The model can never
        # call us with a name we can't fulfil.
        present = [r["name"] for r in list_available_references()] or list(ALLOWED_REFERENCES.keys())
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": present,
                    "description": "The reference filename to read. Must be one of the allowlisted entries.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, name: str) -> dict:
        if name not in ALLOWED_REFERENCES:
            logger.info("read_reference rejected unknown name: %r", name)
            return {
                "success": False,
                "error": f"Unknown reference '{name}'. Available: {sorted(ALLOWED_REFERENCES.keys())}.",
            }

        path = _ref.REFERENCE_DIR / name
        if not path.is_file():
            available = [r["name"] for r in list_available_references()]
            logger.warning(
                "read_reference: '%s' is allowlisted but missing on disk at %s. "
                "Available: %s. Run scripts/sync_ai_reference.py.",
                name, path, available,
            )
            return {
                "success": False,
                "error": (
                    f"Reference file '{name}' is allowlisted but not present on disk. "
                    f"Run scripts/sync_ai_reference.py to populate it. "
                    f"Files currently available: {available}."
                ),
            }

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("read_reference failed to read %s: %s", path, e)
            return {"success": False, "error": f"Failed to read {name}: {e}"}

        logger.info("read_reference served '%s' (%d chars)", name, len(content))
        return {"success": True, "name": name, "content": content}
