import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.schemas.csv_schemas import CsvRule

logger = logging.getLogger(__name__)


class WriteCsvRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_csv_rule"

    @property
    def description(self) -> str:
        return (
            "Validate a CSV import rule against the schema and save it to the configured "
            "CSV rules directory. Use this after the user has confirmed the filename. "
            "Returns the full path where the file was saved."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "YAML filename for the rule, e.g. 'chase-checking.yaml'",
                },
                "content": {
                    "type": "string",
                    "description": "Full YAML content of the CSV rule",
                },
            },
            "required": ["filename", "content"],
        }

    def __init__(self, rules_dir: Path | None):
        self._rules_dir = rules_dir

    async def execute(self, filename: str, content: str) -> dict:
        if not self._rules_dir:
            return {"success": False, "error": "csv_rules_dir is not configured in config.yaml"}

        # Validate against schema
        try:
            yaml = YAML(typ="safe")
            import io
            data = yaml.load(io.StringIO(content))
            CsvRule.model_validate(data)
        except Exception as e:
            return {"success": False, "error": f"Validation failed: {e}"}

        # Ensure .yaml extension
        if not filename.endswith((".yaml", ".yml")):
            filename += ".yaml"

        # Path traversal protection
        save_path = (self._rules_dir / filename).resolve()
        if not save_path.is_relative_to(self._rules_dir.resolve()):
            return {"success": False, "error": "Invalid filename — path traversal not allowed"}

        self._rules_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved CSV rule to {save_path}")

        return {"success": True, "path": str(save_path)}
