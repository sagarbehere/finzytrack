"""Applying recipe migrations with backups (the gated apply path).

Migrations are no longer applied automatically at startup. Instead the startup
task framework (app/startup_tasks) detects pending migrations read-only, the
user consents, and then `apply_recipe_migration` runs — with a timestamped
backup of every changed or removed file, so nothing is mutated without a
recoverable copy. See dev-docs/upgrades.md and §4.12.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from .recipe_migration import MigrationReport, migrate_recipes_dir, WriteFn, RemoveFn

logger = logging.getLogger(__name__)

# Backups of removed widget files go to a dedicated recovery folder under
# recipes/ (their own directory is deleted, so they can't stay beside the file).
BACKUP_DIRNAME = ".migration-backups"


def _timestamped_backup(path: Path) -> None:
    """Copy *path* to a timestamped `.bak` beside it, best-effort."""
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + f".{ts}.bak"))
        except OSError:
            logger.warning("Could not back up %s before migration", path)


def _backup_writer() -> WriteFn:
    """A self-contained timestamped-backup writer (no BackupManager dependency:
    it may run before services exist). Backs up in place, then writes."""
    def _write(path: Path, text: str) -> None:
        _timestamped_backup(path)
        path.write_text(text, encoding="utf-8")

    return _write


def _backup_remover(backup_dir: Path) -> RemoveFn:
    """Copy a file into *backup_dir* (a recovery folder that survives the
    migration), then delete the original — so a migration never removes a widget
    file without leaving a recoverable copy."""
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


def apply_recipe_migration(recipes_dir: Path) -> MigrationReport:
    """Apply the recipe v1→v2 migration to *recipes_dir*, backing up every
    changed or removed file. Idempotent (already-v2 files are skipped). Returns
    the report. Raises only on truly unexpected errors (per-file problems are
    captured in `report.errors`)."""
    report = migrate_recipes_dir(
        recipes_dir,
        write=True,
        write_fn=_backup_writer(),
        remove_fn=_backup_remover(recipes_dir / BACKUP_DIRNAME),
    )
    if report.changed or report.errors:
        logger.info("Recipe migration (%s): %s", recipes_dir, report.summary())
        for oid, dash_id in report.rehomed_orphans:
            logger.info("  rehomed orphan widget '%s' → dashboard '%s'", oid, dash_id)
        for err in report.errors:
            logger.warning("  recipe migration: %s", err)
    return report
