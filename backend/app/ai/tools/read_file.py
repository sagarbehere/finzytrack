import logging
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 64 * 1024  # 64 KB cap for reading rule files


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the content of an existing rule file. "
            "Only files within the configured CSV, XLS, or email rules directories may be read. "
            "Use this when the user wants to modify an existing rule."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the rule file to read",
                },
            },
            "required": ["path"],
        }

    def __init__(self, allowed_dirs: list[Path]):
        # Only files within these directories may be read
        self._allowed_dirs = [d.resolve() for d in allowed_dirs if d is not None]

    async def execute(self, path: str) -> dict:
        target = Path(path).resolve()

        if not any(target.is_relative_to(d) for d in self._allowed_dirs):
            return {"success": False, "error": "Access denied: path is outside allowed rule directories"}

        if not target.is_file():
            return {"success": False, "error": f"File not found: {path}"}

        try:
            content = target.read_bytes()
            if len(content) > MAX_READ_BYTES:
                content = content[:MAX_READ_BYTES]
                text = content.decode("utf-8", errors="replace") + "\n... (truncated)"
            else:
                text = content.decode("utf-8", errors="replace")
            return {"success": True, "content": text}
        except Exception as e:
            return {"success": False, "error": str(e)}
