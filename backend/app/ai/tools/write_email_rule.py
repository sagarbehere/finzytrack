import io
import re
import logging
from pathlib import Path

from ruamel.yaml import YAML

from app.ai.tools.base import BaseTool
from app.ai.tools.test_email_extraction import _test_one_field
from app.core.backup_manager import BackupManager
from app.email_import.rule_schemas import ExtractionFieldDef, RuleFile as EmailRuleFile
from app.helpers.rule_validation import reference_shape

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


def _validate_against_email(
    data: dict, email_body: str, email_subject: str,
) -> list[str]:
    """
    Run every extraction pattern in the rule against the provided email text.
    Returns a list of error strings (empty = all patterns match).
    Only checks the first transaction type whose email_filter matches.
    """
    errors: list[str] = []
    matched_any = False

    for txn_type in data.get("transaction_types", []):
        type_name = txn_type.get("name", "<unnamed>")
        email_filter = txn_type.get("email_filter") or {}

        # Check if this transaction type's filters match the email.
        # Non-matching types are expected (they handle different email formats)
        # so we silently skip them — only the matching type gets validated.
        subj_pat = email_filter.get("subject_regex")
        if subj_pat and not re.search(subj_pat, email_subject, re.IGNORECASE):
            continue

        body_pat = email_filter.get("body_regex")
        if body_pat and not re.search(body_pat, email_body, re.IGNORECASE):
            continue

        # Filters matched — now test every extraction field
        matched_any = True
        extraction = txn_type.get("extraction") or {}
        for field_name, field_raw in extraction.items():
            if not isinstance(field_raw, dict):
                continue
            is_optional = field_raw.get("optional", False)
            try:
                field_def = ExtractionFieldDef.model_validate(field_raw)
            except Exception as e:
                errors.append(f"'{type_name}'.{field_name}: invalid field definition — {e}")
                continue

            result = _test_one_field(field_name, field_def, email_body, email_subject, None)
            if not result.get("matched", False) and not is_optional:
                err_detail = result.get("error", "pattern did not match")
                errors.append(f"'{type_name}'.{field_name}: {err_detail}")

    if not matched_any:
        type_names = [t.get("name", "<unnamed>") for t in data.get("transaction_types", [])]
        errors.append(
            f"No transaction type's email_filter matched the provided email. "
            f"Types checked: {', '.join(type_names)}"
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
            "IMPORTANT: Always pass email_body and email_subject from the original email — "
            "the tool re-tests all extraction patterns against the email before saving and "
            "will refuse to save if any required field fails to extract. "
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
                "email_body": {
                    "type": "string",
                    "description": (
                        "Plain-text body of the original email. When provided, the tool "
                        "re-runs all extraction patterns against this text and refuses to "
                        "save if any required field fails to match."
                    ),
                },
                "email_subject": {
                    "type": "string",
                    "description": "Subject line of the original email (used for cross-validation with email_body).",
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

    async def execute(
        self,
        filename: str,
        content: str,
        overwrite: bool = False,
        email_body: str | None = None,
        email_subject: str | None = None,
    ) -> dict:
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
                    "reference_shape": reference_shape("email"),
                }

        # Cross-validate patterns against the original email
        if isinstance(data, dict) and email_body:
            email_errors = _validate_against_email(
                data, email_body, email_subject or "",
            )
            if email_errors:
                return {
                    "success": False,
                    "error": (
                        "Patterns do not match the original email — fix before saving:\n"
                        + "\n".join(f"  - {e}" for e in email_errors)
                    ),
                    "reference_shape": reference_shape("email"),
                }

        # Validate against Pydantic schema
        try:
            EmailRuleFile.model_validate(data)
        except Exception as e:
            return {
                "success": False,
                "error": f"Schema validation failed: {e}",
                "reference_shape": reference_shape("email"),
            }

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
