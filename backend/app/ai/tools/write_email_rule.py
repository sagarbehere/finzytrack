import io
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Import from email service schemas if available, else skip validation
try:
    import sys
    from pathlib import Path as _Path
    _email_service = _Path(__file__).parents[5] / "email_service"
    if str(_email_service) not in sys.path:
        sys.path.insert(0, str(_email_service))
    from app.schemas.rule_schemas import RuleFile as EmailRuleFile
    _HAVE_EMAIL_SCHEMA = True
except Exception:
    _HAVE_EMAIL_SCHEMA = False


class WriteEmailRuleTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_email_rule"

    @property
    def description(self) -> str:
        return (
            "Validate an email import rule and save it to the configured email rules directory. "
            "Use this after the user has confirmed the filename. "
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
            },
            "required": ["filename", "content"],
        }

    def __init__(self, rules_dir: Path | None):
        self._rules_dir = rules_dir

    async def execute(self, filename: str, content: str) -> dict:
        if not self._rules_dir:
            return {"success": False, "error": "email_rules_dir is not configured in config.yaml"}

        # Validate against schema if available
        if _HAVE_EMAIL_SCHEMA:
            try:
                yaml = YAML(typ="safe")
                data = yaml.load(io.StringIO(content))
                EmailRuleFile.model_validate(data)
            except Exception as e:
                return {"success": False, "error": f"Validation failed: {e}"}

        if not filename.endswith((".yaml", ".yml")):
            filename += ".yaml"

        save_path = (self._rules_dir / filename).resolve()
        if not save_path.is_relative_to(self._rules_dir.resolve()):
            return {"success": False, "error": "Invalid filename — path traversal not allowed"}

        self._rules_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved email rule to {save_path}")

        return {"success": True, "path": str(save_path)}
