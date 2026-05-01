"""Resolver and allowlist for AI reference source files.

The `read_reference` tool exposes a small, hand-picked set of source files to
the AI assistant. Files are synced into `backend/resources/ai_reference/` by
`scripts/sync_ai_reference.py` and bundled by PyInstaller for the desktop app.

The allowlist below is the single source of truth: a file is readable iff its
name appears here. The tool never accepts free-form paths.

This module also exposes ``log_readiness`` and ``get_readiness`` so the
backend can fail loudly (in logs and via a diagnostics endpoint) when a file
is allowlisted but not on disk — preventing the assistant from silently
losing access to a reference file because of a missed sync step.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


_REFERENCE_DIR_DEV = Path(__file__).parents[2] / "resources" / "ai_reference"
_REFERENCE_DIR_FROZEN = (
    Path(getattr(sys, "_MEIPASS", "")) / "resources" / "ai_reference"
)
REFERENCE_DIR: Path = (
    _REFERENCE_DIR_FROZEN if getattr(sys, "frozen", False) else _REFERENCE_DIR_DEV
)

_SCHEMA_DIR_DEV = Path(__file__).parents[2] / "resources" / "schemas"
_SCHEMA_DIR_FROZEN = Path(getattr(sys, "_MEIPASS", "")) / "resources" / "schemas"
SCHEMA_DIR: Path = (
    _SCHEMA_DIR_FROZEN if getattr(sys, "frozen", False) else _SCHEMA_DIR_DEV
)


# filename → one-line description shown to the model in the tool description.
# Keep descriptions short and oriented around *when to read*, not *what's inside*.
ALLOWED_REFERENCES: dict[str, str] = {
    "recipes.ts": (
        "TypeScript type definitions for dashboard/widget recipes — RecipeDashboard, "
        "Widget, Visualization, Parameter, Transform, ClickLink, etc. Read this when "
        "you need the authoritative shape of a field that the prompt schema doesn't fully spell out."
    ),
    "generators.ts": (
        "Implementation of all $gen generators (currentYear, currentMonth, monthOptions, "
        "defaultCurrency, …). Read this to confirm exactly which generator names exist "
        "and what they emit."
    ),
}

# Files the assistant or its validators *require* — not user-facing, but
# checked at startup so a missed sync step fails loudly instead of silently
# breaking the assistant.
REQUIRED_SCHEMA_FILES: tuple[str, ...] = ("recipe.schema.json",)


def list_available_references() -> list[dict]:
    """Return one entry per *actually present* reference file."""
    if not REFERENCE_DIR.is_dir():
        return []
    out = []
    for name, desc in ALLOWED_REFERENCES.items():
        if (REFERENCE_DIR / name).is_file():
            out.append({"name": name, "description": desc})
    return out


def get_readiness() -> dict:
    """Inspect every file the AI assistant depends on. Used by the diagnostics
    endpoint and at startup to detect missed sync steps.

    Returns a dict with keys:
      ok                  bool  — true iff every required file is present
      reference_dir       str   — resolved path
      references_present  list  — allowlist entries that exist on disk
      references_missing  list  — allowlist entries that are missing
      schema_dir          str   — resolved path
      schemas_present     list
      schemas_missing     list
      remediation         str   — one-line "run this command" hint, or empty
    """
    refs_present: list[str] = []
    refs_missing: list[str] = []
    for name in ALLOWED_REFERENCES:
        if (REFERENCE_DIR / name).is_file():
            refs_present.append(name)
        else:
            refs_missing.append(name)

    schemas_present: list[str] = []
    schemas_missing: list[str] = []
    for name in REQUIRED_SCHEMA_FILES:
        if (SCHEMA_DIR / name).is_file():
            schemas_present.append(name)
        else:
            schemas_missing.append(name)

    ok = not refs_missing and not schemas_missing
    remediation = "" if ok else "Run `python scripts/sync_ai_reference.py` to populate the missing files."
    return {
        "ok": ok,
        "reference_dir": str(REFERENCE_DIR),
        "references_present": refs_present,
        "references_missing": refs_missing,
        "schema_dir": str(SCHEMA_DIR),
        "schemas_present": schemas_present,
        "schemas_missing": schemas_missing,
        "remediation": remediation,
    }


def log_readiness() -> dict:
    """Emit a structured log line describing AI assistant readiness.

    A WARNING is logged if any required file is missing — visible in journalctl
    and in any log viewer the desktop app exposes. Returns the same dict as
    ``get_readiness()`` so callers can chain on it.
    """
    state = get_readiness()
    if state["ok"]:
        logger.info(
            "AI assistant readiness OK — references=%s schemas=%s",
            state["references_present"], state["schemas_present"],
        )
    else:
        logger.warning(
            "AI assistant readiness DEGRADED — missing references=%s missing schemas=%s. %s",
            state["references_missing"], state["schemas_missing"], state["remediation"],
        )
    return state


def autosync_dev() -> bool:
    """In dev mode (not frozen), auto-run scripts/sync_ai_reference.py if any
    required file is missing. This makes a fresh clone work without remembering
    to run the sync script manually.

    No-op in frozen mode — the desktop bundle ships pre-synced copies, so the
    script and the source files don't exist at runtime. Returns True if a sync
    was attempted, False otherwise.
    """
    if getattr(sys, "frozen", False):
        return False

    state = get_readiness()
    if state["ok"]:
        return False

    # The repo's scripts/ dir lives 3 parents up from this file:
    #   backend/app/ai/reference.py -> repo root
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "sync_ai_reference.py"
    if not script.is_file():
        logger.warning("autosync_dev: sync script not found at %s", script)
        return False

    import subprocess
    logger.info("AI assistant files missing in dev — auto-running %s", script)
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning("autosync_dev: sync script exited %d. stderr=%s",
                           result.returncode, result.stderr.strip())
            return True
        logger.info("autosync_dev: sync complete. stdout=%s", result.stdout.strip())
    except Exception as e:
        logger.warning("autosync_dev: failed to run sync script: %s", e)
    return True
