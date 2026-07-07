import os
import shutil
import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, IO, Optional

from app.core.atomic_backup import (
    BACKUP_SUFFIX,
    fsync_dir,
    timestamped_backup,
)

logger = logging.getLogger(__name__)

class BackupError(Exception):
    """Custom exception for backup-related failures."""
    pass

class BackupManager:
    """Manages file backups with automatic cleanup and atomic writes.

    Backups are stored under ``backup_dir``. When ``base_dir`` is given, each
    file's backups live in a subdirectory that mirrors the file's path relative
    to ``base_dir`` (e.g. ``config/recipes/dashboards/`` → ``<backup_dir>/config/
    recipes/dashboards/``). This keeps retention per *source path* rather than per
    *basename*, so two different files sharing a name (e.g. a dashboard and a
    widget both called ``spending.json``) never share a retention bucket. Files
    outside ``base_dir`` (e.g. a ledger at an arbitrary path) fall back to the
    flat ``backup_dir``.
    """

    def __init__(self, backup_dir: Path, retention_count: int, base_dir: Optional[Path] = None):
        """Initializes the BackupManager.

        Args:
            backup_dir: The directory where backups will be stored.
            retention_count: The number of backups to retain for each file.
            base_dir: Optional root; when set, backups are namespaced by the
                source file's path relative to it (see class docstring).
        """
        self.backup_dir = backup_dir
        self.retention_count = retention_count
        self.base_dir = base_dir

    def _backup_dir_for(self, file_path: Path) -> Path:
        """The directory a given file's backups live in — a path-mirrored
        subdirectory under ``backup_dir`` when the file is inside ``base_dir``,
        else the flat ``backup_dir``."""
        if self.base_dir is not None:
            try:
                rel_parent = file_path.resolve().parent.relative_to(self.base_dir.resolve())
                return self.backup_dir / rel_parent
            except ValueError:
                pass  # file is outside base_dir → flat fallback
        return self.backup_dir

    def _create_backup(self, file_path: Path) -> Path:
        """Create a timestamped backup of the file in its namespaced backup dir."""
        backup_path = timestamped_backup(file_path, self._backup_dir_for(file_path))
        if backup_path is None:
            raise BackupError(f"Failed to create backup for {file_path}")
        logger.info("Created backup for %s at %s", file_path, backup_path)
        return backup_path

    def _cleanup_old_backups(self, file_path: Path) -> None:
        """Remove old backups of *file_path* beyond the retention limit. Sorted
        by filename (which embeds the backup timestamp), so ordering is
        independent of filesystem mtimes."""
        try:
            dest_dir = self._backup_dir_for(file_path)
            backup_pattern = f"{file_path.name}.*{BACKUP_SUFFIX}"
            backup_files = sorted(dest_dir.glob(backup_pattern), reverse=True)

            for stale in backup_files[self.retention_count:]:
                stale.unlink()
                logger.info("Removed old backup: %s", stale)
        except Exception as e:
            logger.warning(f"Failed to clean up old backups for {file_path.name}: {e}", exc_info=True)

    @contextmanager
    def atomic_write(self, file_path_str: str, encoding: str = 'utf-8') -> Generator[IO, None, None]:
        """
        A context manager for atomic, backed-up file writes for both reading and writing.

        - Creates a backup of the original file.
        - Creates a temporary file and copies the original content into it, so a
          caller may *read* the current content (read-modify-write) as well as
          overwrite it.
        - Yields a file handle to this temporary file, positioned at 0.
        - **On exit the file is truncated to the caller's final write position.**
          This guarantees a full overwrite: writing content *shorter* than the
          original never leaves trailing bytes from the old content, so a caller
          does NOT need to call ``f.truncate()`` itself. (Callers may still do so
          harmlessly.) This closes a class of corruption bug where a forgotten
          truncate left a shorter JSON/YAML/ledger with a stale tail.
        - Before swapping, ``fsync`` flushes the temp file's data to disk and
          (on POSIX) the parent directory's entry change is fsynced after the
          rename. This makes the write durable across power loss — without
          these fsyncs, ``os.replace`` only guarantees *visibility* atomicity,
          not durability, and a crash between rename and the kernel's next
          writeback can leave the target as the old content, a zero-byte
          file, or the new content depending on the filesystem.
        - On successful exit, the modified temporary file atomically replaces the original.
        - If an error occurs, the temporary file is discarded, leaving the original untouched.

        Note: truncation is to the file position the caller leaves, which for
        every write pattern in this codebase (write full content from the start;
        or read → seek(0) → rewrite) is the end of the new content. This matches
        the "every write is a full rewrite" architecture (backend/CLAUDE.md).
        """
        file_path = Path(file_path_str)
        temp_path = None
        success = False

        try:
            if file_path.exists():
                self._create_backup(file_path)

            fd, temp_path_str = tempfile.mkstemp(dir=file_path.parent, prefix=f".{file_path.name}")
            os.close(fd)  # mkstemp returns an fd we don't use; the open() below is the writer
            temp_path = Path(temp_path_str)

            if file_path.exists():
                shutil.copy(file_path, temp_path)

            with open(temp_path, 'r+', encoding=encoding) as f:
                f.seek(0)
                yield f
                # Guarantee a full overwrite: drop any bytes of the original that
                # the caller's (possibly shorter) new content didn't cover. Makes
                # the primitive safe-by-default — no call site can corrupt a file
                # by forgetting to truncate. See the docstring.
                f.truncate()
                # Flush Python's buffer, then push the kernel page cache to
                # disk while we still have the fd open. Without this, the
                # rename below can land before the data does.
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, file_path)
            # The rename swap is now visible; flush the directory entry so
            # the swap itself survives a crash.
            fsync_dir(file_path.parent)
            success = True
            logger.info(f"Successfully wrote to {file_path}")

        except Exception:
            logger.error(f"Atomic write to {file_path} failed. Original file is safe.", exc_info=True)
            raise
        finally:
            if success:
                self._cleanup_old_backups(file_path)
            elif temp_path is not None:
                logger.warning(f"An error occurred during write operation. Cleaning up temporary file {temp_path}")
                temp_path.unlink(missing_ok=True)
