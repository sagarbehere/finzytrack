import io
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.schemas.csv_schemas import CsvRule

logger = logging.getLogger(__name__)

# Keys that belong to XLS rules — if present in a CSV write call, reject immediately
_XLS_ONLY_KEYS = {"sheet_index", "sheet_name"}


class WriteCsvRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_csv_rule"

    @property
    def description(self) -> str:
        return (
            "Validate a CSV/TSV import rule against the schema and save it to the configured "
            "CSV rules directory. Use this after the user has confirmed the filename. "
            "Returns the full path where the file was saved. "
            "ONLY for CSV/TSV files — never use this for XLS or XLSX files (use write_xls_rule instead). "
            "Pass overwrite: true to overwrite an existing file."
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
                "overwrite": {
                    "type": "boolean",
                    "description": "Set to true to overwrite an existing file. Default: false.",
                },
            },
            "required": ["filename", "content"],
        }

    def __init__(self, rules_dir: Path | None):
        self._rules_dir = rules_dir

    async def execute(self, filename: str, content: str, overwrite: bool = False) -> dict:
        if not self._rules_dir:
            return {"success": False, "error": "csv_rules_dir is not configured in config.yaml"}

        # Parse YAML first so we can inspect the keys
        try:
            yaml = YAML(typ="safe")
            data = yaml.load(io.StringIO(content))
        except Exception as e:
            return {"success": False, "error": f"YAML parse error: {e}"}

        # Reject XLS-specific content saved to the CSV directory
        if isinstance(data, dict):
            xls_keys_found = _XLS_ONLY_KEYS & data.keys()
            if xls_keys_found:
                return {
                    "success": False,
                    "error": (
                        f"This rule contains XLS-specific fields ({', '.join(sorted(xls_keys_found))}) "
                        "and must be saved with write_xls_rule, not write_csv_rule."
                    ),
                }

        # Validate against CSV schema
        try:
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

        # Overwrite protection
        if save_path.exists() and not overwrite:
            return {
                "success": False,
                "error": (
                    f"File '{filename}' already exists. Pass overwrite: true to overwrite it, "
                    "or choose a different filename."
                ),
            }

        self._rules_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved CSV rule to {save_path}")

        return {"success": True, "path": str(save_path)}
