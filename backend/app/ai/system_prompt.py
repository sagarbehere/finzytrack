"""
System prompt builder for the FinzyTrack AI assistant.

Prompt content lives in backend/resources/prompts/ as plain Markdown files:
  assistant_base.md      — role, workflow, instructions (setup mode)
  assistant_analyst.md   — role, workflow, instructions (analyst mode)
  schema_csv.md          — CSV rule schema + example
  schema_xls.md          — XLS rule schema + example
  schema_email.md        — email rule schema + example
  schema_postings.md             — postings table schema + sign conventions
  schema_recipe_dashboard.md    — dashboard recipe JSON schema (loaded on demand via tool)

Mode routing:
  - file_type is set   → setup mode (assistant_base.md + relevant schema)
  - no file_type        → analyst mode (analyst + postings schema)
"""

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parents[2] / "resources" / "prompts"

_SCHEMA_FILES = {
    "csv":   "schema_csv.md",
    "xls":   "schema_xls.md",
    "email": "schema_email.md",
}


@lru_cache(maxsize=8)
def _load(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Prompt file not found: {path}. "
            "Ensure backend/resources/prompts/ contains the expected .md files."
        )
    return path.read_text(encoding="utf-8").strip()


def build_system_prompt(context: dict) -> str:
    """
    Assemble the system prompt for the assistant.

    When a file is attached (file_type is set), uses setup mode:
      assistant_base.md + the relevant schema file(s).

    When no file is attached, uses analyst mode:
      assistant_analyst.md + schema_postings.md.

    Other recognised context keys:
      page: str  — current frontend page, appended as a brief note
    """
    file_type = context.get("file_type")

    if file_type:
        # Setup mode — rule creation
        parts = [_load("assistant_base.md")]
        if file_type in _SCHEMA_FILES:
            parts.append(_load(_SCHEMA_FILES[file_type]))
        else:
            for filename in _SCHEMA_FILES.values():
                parts.append(_load(filename))
    else:
        # Analyst mode — financial questions + recipe generation
        # Recipe schema is loaded on demand via get_recipe_schema tool,
        # not injected here, to keep the system prompt small.
        parts = [_load("assistant_analyst.md"), _load("schema_postings.md")]

    if context.get("page"):
        parts.append(f"## Current context\nThe user is on the '{context['page']}' page.")

    return "\n\n---\n\n".join(parts)
