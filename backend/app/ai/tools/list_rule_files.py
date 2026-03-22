import logging
from pathlib import Path
from typing import Literal

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ListRuleFilesTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_rule_files"

    @property
    def description(self) -> str:
        return (
            "List existing rule files of a given type (csv, xls, or email). "
            "Use this when the user wants to modify an existing rule file — "
            "call this first to show the available files, then ask the user which one to edit."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["csv", "xls", "email"],
                    "description": "Type of rule files to list",
                },
            },
            "required": ["type"],
        }

    def __init__(
        self,
        csv_rules_dir: Path | None,
        xls_rules_dir: Path | None,
        email_rules_dir: Path | None,
    ):
        self._dirs: dict[str, Path | None] = {
            "csv": csv_rules_dir,
            "xls": xls_rules_dir,
            "email": email_rules_dir,
        }

    async def execute(self, type: Literal["csv", "xls", "email"]) -> dict:
        rules_dir = self._dirs.get(type)
        if not rules_dir or not rules_dir.is_dir():
            return {
                "success": True,
                "files": [],
                "directory": str(rules_dir) if rules_dir else None,
                "note": f"No {type} rules directory configured or directory does not exist",
            }

        files = sorted(
            p.name for p in rules_dir.iterdir()
            if p.suffix in (".yaml", ".yml") and p.is_file()
        )
        return {
            "success": True,
            "files": files,
            "directory": str(rules_dir),
        }
