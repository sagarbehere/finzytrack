import io
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.core.backup_manager import BackupManager
from app.schemas.xls_schemas import XlsRule

logger = logging.getLogger(__name__)

# Keys that belong only to CSV rules — if present in an XLS write call, reject immediately
_CSV_ONLY_KEYS = {"separator", "encoding"}


class WriteXlsRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_xls_rule"

    @property
    def description(self) -> str:
        return (
            "Validate an XLS/XLSX import rule against the schema and save it to the configured "
            "XLS rules directory. Use this after the user has confirmed the filename. "
            "Returns the full path where the file was saved. "
            "ONLY for XLS/XLSX files — never use this for CSV or TSV files (use write_csv_rule instead). "
            "Pass overwrite: true to overwrite an existing file."
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
                "overwrite": {
                    "type": "boolean",
                    "description": "Set to true to overwrite an existing file. Default: false.",
                },
            },
            "required": ["filename", "content"],
        }

    def __init__(self, rules_dir: Path | None, backup_manager: BackupManager | None = None):
        self._rules_dir = rules_dir
        self._backup_manager = backup_manager

    async def execute(self, filename: str, content: str, overwrite: bool = False) -> dict:
        if not self._rules_dir:
            return {"success": False, "error": "xls_rules_dir is not configured in config.yaml"}

        # Parse YAML first so we can inspect the keys
        try:
            yaml = YAML(typ="safe")
            data = yaml.load(io.StringIO(content))
        except Exception as e:
            return {"success": False, "error": f"YAML parse error: {e}"}

        # Reject CSV-specific content saved to the XLS directory
        if isinstance(data, dict):
            csv_keys_found = _CSV_ONLY_KEYS & data.keys()
            if csv_keys_found:
                return {
                    "success": False,
                    "error": (
                        f"This rule contains CSV-specific fields ({', '.join(sorted(csv_keys_found))}) "
                        "and must be saved with write_csv_rule, not write_xls_rule."
                    ),
                }

        # Validate against XLS schema
        try:
            XlsRule.model_validate(data)
        except Exception as e:
            return {"success": False, "error": f"Validation failed: {e}"}

        if not filename.endswith((".yaml", ".yml")):
            filename += ".yaml"

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
        file_existed = save_path.exists()
        if self._backup_manager:
            with self._backup_manager.atomic_write(str(save_path)) as f:
                f.seek(0)
                f.truncate()
                f.write(content)
        else:
            save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved XLS rule to {save_path}")

        return {"success": True, "path": str(save_path), "backup_created": file_existed}
