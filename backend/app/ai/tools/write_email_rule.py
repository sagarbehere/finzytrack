import io
import re
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.core.backup_manager import BackupManager
from app.email_import.rule_schemas import RuleFile as EmailRuleFile

logger = logging.getLogger(__name__)


def _validate_regex_patterns(data: dict) -> list[str]:
    """
    Walk transaction_types and validate all regex patterns.
    Returns a list of error strings (empty = all OK).
    """
    errors = []
    for txn_type in data.get("transaction_types", []):
        type_name = txn_type.get("name", "<unnamed>")

        # email_filter regexes — just check they compile
        for filter_key in ("subject_regex", "body_regex"):
            pattern = (txn_type.get("email_filter") or {}).get(filter_key)
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"transaction_type '{type_name}' email_filter.{filter_key}: invalid regex — {e}")

        # extraction patterns — must compile AND have exactly one capture group
        for field_name, field_def in (txn_type.get("extraction") or {}).items():
            if not isinstance(field_def, dict):
                continue
            pattern = field_def.get("pattern")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"transaction_type '{type_name}' extraction.{field_name}: invalid regex — {e}"
                )
                continue
            if compiled.groups != 1:
                errors.append(
                    f"transaction_type '{type_name}' extraction.{field_name}: "
                    f"pattern has {compiled.groups} capture group(s) — exactly 1 required"
                )

    return errors


class WriteEmailRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_email_rule"

    @property
    def description(self) -> str:
        return (
            "Validate an email import rule and save it to the configured email rules directory. "
            "Use this after the user has confirmed the extracted values. "
            "Pass overwrite: true to update an existing file. "
            "Returns the full path where the file was saved."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "YAML filename for the rule, e.g. 'chase-alerts.yaml'",
                },
                "content": {
                    "type": "string",
                    "description": "Full YAML content of the email rule",
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
            return {"success": False, "error": "email_import is not enabled in config.yaml"}

        # Parse YAML
        try:
            yaml = YAML(typ="safe")
            data = yaml.load(io.StringIO(content))
        except Exception as e:
            return {"success": False, "error": f"YAML parse error: {e}"}

        # Validate regex patterns (capture groups, compile errors)
        if isinstance(data, dict):
            regex_errors = _validate_regex_patterns(data)
            if regex_errors:
                return {
                    "success": False,
                    "error": "Regex validation failed:\n" + "\n".join(f"  - {e}" for e in regex_errors),
                }

        # Validate against Pydantic schema
        try:
            EmailRuleFile.model_validate(data)
        except Exception as e:
            return {"success": False, "error": f"Schema validation failed: {e}"}

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
        file_existed = save_path.exists()
        if self._backup_manager:
            with self._backup_manager.atomic_write(str(save_path)) as f:
                f.seek(0)
                f.truncate()
                f.write(content)
        else:
            save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved email rule to {save_path}")

        return {"success": True, "path": str(save_path), "backup_created": file_existed}
