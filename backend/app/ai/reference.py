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
    # NOTE: recipes.ts is intentionally NOT exposed. The recipe format's source of
    # truth is recipe.schema.json, which the model already receives as generated
    # prose+types via get_recipe_schema. recipes.ts is a derived, partly-generated
    # file that also declares CODE-defined recipe types (function-valued fields)
    # invalid in the JSON the assistant authors — a net-negative reference.
    "generators.ts": (
        "Implementation of all $gen generators (currentYear, currentMonth, monthOptions, "
        "defaultCurrency, …). Read this to confirm exactly which generator names exist "
        "and what they emit."
    ),
}

# Files the assistant or its validators *require* — not user-facing, but
# checked at startup so a missed sync step fails loudly instead of silently
# breaking the assistant.
REQUIRED_SCHEMA_FILES: tuple[str, ...] = ("recipe.schema.json", "transforms.catalog.json")


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


# Backend-consumed artifacts derived from frontend/src/types/recipe.schema.json.
# In dev they are regenerated at startup so an edit to the schema can never leave
# a stale-but-present copy behind (the failure mode a presence-only check misses).
# The frontend TS types are regenerated separately by the frontend's predev hook.
_SYNC_SCRIPTS: tuple[tuple[str, ...], ...] = (
    ("scripts", "sync_ai_reference.py"),          # copies schema + generators.ts into resources/
    ("scripts", "generate_recipe_schema_doc.py"),  # regenerates the AI prose-doc appendix
)


def autosync_dev() -> bool:
    """In dev mode (not frozen), regenerate the backend copies derived from the
    recipe schema so they can never go stale relative to the source.

    Unlike a presence-only check, this runs on every dev startup: the sync scripts
    are idempotent (a no-op write when the schema is unchanged), so the cost is a
    couple of fast subprocesses and the payoff is that editing recipe.schema.json
    needs no manual "remember to run the sync" step. Missing files are covered as a
    subset of "stale".

    No-op in frozen mode — the desktop bundle ships pre-synced copies and the
    scripts/source don't exist at runtime. Returns True if a sync was attempted.
    """
    if getattr(sys, "frozen", False):
        return False

    # The repo's scripts/ dir lives 3 parents up from this file:
    #   backend/app/ai/reference.py -> repo root
    repo_root = Path(__file__).resolve().parents[3]

    import subprocess
    attempted = False
    for parts in _SYNC_SCRIPTS:
        script = repo_root.joinpath(*parts)
        if not script.is_file():
            logger.warning("autosync_dev: sync script not found at %s", script)
            continue
        attempted = True
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=30, cwd=str(repo_root),
            )
            if result.returncode != 0:
                logger.warning("autosync_dev: %s exited %d. stderr=%s",
                               script.name, result.returncode, result.stderr.strip())
        except Exception as e:
            logger.warning("autosync_dev: failed to run %s: %s", script.name, e)
    return attempted
