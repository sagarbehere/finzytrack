import io
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.schemas.xls_schemas import XlsRule

logger = logging.getLogger(__name__)


class WriteXlsRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_xls_rule"

    @property
    def description(self) -> str:
        return (
            "Validate an XLS/XLSX import rule against the schema and save it to the configured "
            "XLS rules directory. Use this after the user has confirmed the filename. "
            "Returns the full path where the file was saved."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "YAML filename for the rule, e.g. 'icici-savings.yaml'",
                },
                "content": {
                    "type": "string",
                    "description": "Full YAML content of the XLS rule",
                },
            },
            "required": ["filename", "content"],
        }

    def __init__(self, rules_dir: Path | None):
        self._rules_dir = rules_dir

    async def execute(self, filename: str, content: str) -> dict:
        if not self._rules_dir:
            return {"success": False, "error": "xls_rules_dir is not configured in config.yaml"}

        # Validate against schema
        try:
            yaml = YAML(typ="safe")
            data = yaml.load(io.StringIO(content))
            XlsRule.model_validate(data)
        except Exception as e:
            return {"success": False, "error": f"Validation failed: {e}"}

        if not filename.endswith((".yaml", ".yml")):
            filename += ".yaml"

        save_path = (self._rules_dir / filename).resolve()
        if not save_path.is_relative_to(self._rules_dir.resolve()):
            return {"success": False, "error": "Invalid filename — path traversal not allowed"}

        self._rules_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved XLS rule to {save_path}")

        return {"success": True, "path": str(save_path)}
