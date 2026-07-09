"""Applying recipe migrations with backups (the gated apply path).

Migrations are no longer applied automatically at startup. Instead the startup
task framework (app/startup_tasks) detects pending migrations read-only, the
user consents, and then `apply_recipe_migration` runs — with a timestamped
backup of every changed or removed file, so nothing is mutated without a
recoverable copy. See dev-docs/upgrades.md and §4.12.

The backup/write mechanics come from the shared, dependency-free primitives in
`app.core.atomic_backup` (the same ones BackupManager uses), so a migration
backup is named identically to every other backup and each rewrite is durable —
without depending on BackupManager, which may not exist this early in startup.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.atomic_backup import atomic_write_text, timestamped_backup
from .recipe_migration import MigrationReport, migrate_recipes_dir, WriteFn, RemoveFn

logger = logging.getLogger(__name__)

# Backups of removed widget files go to a dedicated recovery folder under
# recipes/ (their own directory is deleted, so they can't stay beside the file).
BACKUP_DIRNAME = ".migration-backups"


def _backup_writer() -> WriteFn:
    """A writer that snapshots the original in place, then durably overwrites it.
    The timestamped backup lands beside the file; the write is atomic + fsynced."""
    def _write(path: Path, text: str) -> None:
        timestamped_backup(path, path.parent)  # best-effort; None for new files
        atomic_write_text(path, text)

    return _write


def _backup_remover(backup_dir: Path) -> RemoveFn:
    """Copy a file into *backup_dir* (a recovery folder that survives the
    migration), then delete the original — so a migration never removes a widget
    file without leaving a recoverable copy."""
    def _remove(path: Path) -> None:
        timestamped_backup(path, backup_dir)  # best-effort snapshot before delete
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
            logger.warning("  recipe migration: %s — %s", err["path"], err["reason"])
    return report
