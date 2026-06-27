"""Versioned asset-migration runner (§4.12).

A general registry of per-asset-class migrations, applied on startup after the
config is located/seeded and before recipes load. Today it holds a single
asset class (recipes, v1→v2); future breaking changes append entries.

Safety guarantees (all required by §4.12):
  - idempotent (already-at-target files are skipped),
  - malformed files are skipped-and-reported, never crash startup,
  - active-config writes go through an atomic+backup writer when available,
  - a one-line summary is logged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .recipe_migration import migrate_recipes_dir, WriteFn

logger = logging.getLogger(__name__)


def _backup_writer() -> WriteFn | None:
    """An atomic+backup write function for the active config, if BackupManager
    is importable. Falls back to a plain write otherwise."""
    try:
        from app.core.backup_manager import BackupManager
    except Exception:  # pragma: no cover - defensive
        return None

    bm = BackupManager()

    def _write(path: Path, text: str) -> None:
        with bm.atomic_write(str(path)) as f:
            f.seek(0)
            f.write(text)
            f.truncate()

    return _write


def run_startup_migrations(config_dir: Path, *, write_fn: WriteFn | None = None) -> None:
    """Apply all registered asset migrations to *config_dir*. Best-effort and
    safe to call on every launch (idempotent). Never raises — a failure to
    migrate must not block startup; it is logged and surfaced per file."""
    recipes_dir = config_dir / "recipes"
    if not recipes_dir.is_dir():
        return

    writer = write_fn if write_fn is not None else _backup_writer()
    try:
        report = migrate_recipes_dir(recipes_dir, write=True, write_fn=writer)
    except Exception as e:  # noqa: BLE001 — never block startup
        logger.warning("Recipe migration skipped due to error: %s", e)
        return

    if report.changed or report.errors:
        logger.info("Recipe migration (%s): %s", recipes_dir, report.summary())
        for err in report.errors:
            logger.warning("  recipe migration: %s", err)
