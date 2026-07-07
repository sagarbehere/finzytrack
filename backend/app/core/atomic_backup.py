"""Dependency-free backup + atomic-write primitives.

Mechanics only — no retention or location *policy* lives here. Shared by:
  - ``BackupManager`` (central ``data/backups`` dir + per-file retention), and
  - the startup recipe migration (``app/migrations/runner.py``), whose in-place
    backups under ``config/recipes/`` must run *before* app services — hence
    before ``BackupManager`` — exist.

Both call these so every backup is named identically and every overwrite is
durable, regardless of which path produced it. See dev-docs/upgrades.md and
backend/CLAUDE.md (Ledger Write Architecture).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Every backup file ends with this — one convention across both backup paths.
BACKUP_SUFFIX = ".backup"
#: Microsecond precision so rapid successive backups of one file don't collide.
_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"


def backup_filename(name: str) -> str:
    """``<name>.<timestamp>.backup`` — the shared backup naming convention."""
    return f"{name}.{datetime.now().strftime(_TIMESTAMP_FORMAT)}{BACKUP_SUFFIX}"


def timestamped_backup(src: Path, dest_dir: Path, *, name: str | None = None) -> Path | None:
    """Best-effort timestamped copy of *src* into *dest_dir*.

    Returns the backup path, or ``None`` if *src* is absent or the copy failed
    (logged, never raised — a backup is a safety net, not the operation itself).
    Pass *name* to store under a key other than ``src.name`` (e.g. a path-derived
    key that avoids basename collisions in a shared directory).
    """
    if not src.exists():
        return None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / backup_filename(name or src.name)
        shutil.copy2(src, dest)
        return dest
    except OSError:
        logger.warning("Could not back up %s into %s", src, dest_dir)
        return None


def fsync_dir(directory: Path) -> None:
    """Flush a directory's entries to disk (POSIX). No-op where a directory
    can't be opened as an fd (e.g. Windows) or refuses fsync (e.g. tmpfs in
    containers) — the file-data fsync is the durability-critical part."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(dir_fd)
        except OSError as e:
            logger.debug("Directory fsync on %s skipped: %s", directory, e)
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Durably, atomically overwrite *path* with *text*.

    Writes a temp file in the same directory, fsyncs its data, atomically
    renames it over the target, then fsyncs the directory so the swap itself
    survives a crash. This is a *full overwrite* (no read-modify-write); the
    original is left untouched if anything fails. For the read-modify-write
    case (edit part of an existing file), use ``BackupManager.atomic_write``.
    """
    path = Path(path)
    fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
