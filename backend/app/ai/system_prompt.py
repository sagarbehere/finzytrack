"""
System prompt builder for the FinzyTrack AI assistant.

Prompt content lives in backend/resources/prompts/ as plain Markdown files:
  assistant_base.md   — role, workflow, instructions (always included)
  schema_csv.md       — CSV rule schema + example
  schema_xls.md       — XLS rule schema + example
  schema_email.md     — email rule schema + example

When the request context includes a known file_type, only the relevant schema
is appended to the base prompt. This keeps the prompt compact for small models.
When file_type is unknown or absent, all three schemas are included so the
assistant can handle any type.
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

    Always includes assistant_base.md. Appends schema files based on
    context["file_type"]:
      - "csv"   → schema_csv.md only
      - "xls"   → schema_xls.md only
      - "email" → schema_email.md only
      - absent / unknown → all three schemas (safe fallback, larger prompt)

    Other recognised context keys:
      page: str  — current frontend page, appended as a brief note
    """
    parts = [_load("assistant_base.md")]

    file_type = context.get("file_type")
    if file_type in _SCHEMA_FILES:
        parts.append(_load(_SCHEMA_FILES[file_type]))
    else:
        # No file attached yet, or unrecognised type — include everything
        for filename in _SCHEMA_FILES.values():
            parts.append(_load(filename))

    if context.get("page"):
        parts.append(f"## Current context\nThe user is on the '{context['page']}' page.")

    return "\n\n---\n\n".join(parts)
