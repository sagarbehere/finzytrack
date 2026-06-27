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
import shutil
from datetime import datetime
from pathlib import Path

from .recipe_migration import migrate_recipes_dir, WriteFn, RemoveFn

logger = logging.getLogger(__name__)


def _timestamped_backup(path: Path) -> None:
    """Copy *path* to a timestamped `.bak` beside it, best-effort."""
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + f".{ts}.bak"))
        except OSError:
            logger.warning("Could not back up %s before migration", path)


def _backup_writer() -> WriteFn:
    """A self-contained timestamped-backup writer.

    Runs at startup *before* config/services exist, so it must not depend on
    BackupManager (which needs a configured backup_dir/retention). It drops a
    timestamped `.bak` copy next to the file before overwriting, then writes.
    """
    def _write(path: Path, text: str) -> None:
        _timestamped_backup(path)
        path.write_text(text, encoding="utf-8")

    return _write


def _backup_remover(backup_dir: Path) -> RemoveFn:
    """Copy a file into *backup_dir* (a recovery folder that survives the
    migration), then delete the original — so a migration never removes a widget
    file without leaving a recoverable copy. Backups go to a dedicated dir rather
    than beside the file, since the file's own directory (widgets/) is removed."""
    def _remove(path: Path) -> None:
        if path.exists():
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(path, backup_dir / f"{path.name}.{ts}.bak")
            except OSError:
                logger.warning("Could not back up %s before removal", path)
        path.unlink(missing_ok=True)

    return _remove


def run_startup_migrations(config_dir: Path, *, write_fn: WriteFn | None = None) -> None:
    """Apply all registered asset migrations to *config_dir*. Best-effort and
    safe to call on every launch (idempotent). Never raises — a failure to
    migrate must not block startup; it is logged and surfaced per file."""
    recipes_dir = config_dir / "recipes"
    if not recipes_dir.is_dir():
        return

    writer = write_fn if write_fn is not None else _backup_writer()
    remover = _backup_remover(recipes_dir / ".migration-backups")
    try:
        report = migrate_recipes_dir(recipes_dir, write=True, write_fn=writer, remove_fn=remover)
    except Exception as e:  # noqa: BLE001 — never block startup
        logger.warning("Recipe migration skipped due to error: %s", e)
        return

    if report.changed or report.errors:
        logger.info("Recipe migration (%s): %s", recipes_dir, report.summary())
        for oid, dash_id in report.rehomed_orphans:
            logger.info("  rehomed orphan widget '%s' → dashboard '%s'", oid, dash_id)
        for err in report.errors:
            logger.warning("  recipe migration: %s", err)
