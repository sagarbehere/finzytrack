import os
import shutil
import logging
import tempfile
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, IO

logger = logging.getLogger(__name__)

class BackupError(Exception):
    """Custom exception for backup-related failures."""
    pass

class BackupManager:
    """Manages file backups with automatic cleanup and atomic writes."""

    def __init__(self, backup_dir: Path, retention_count: int):
        """Initializes the BackupManager.

        Args:
            backup_dir: The directory where backups will be stored.
            retention_count: The number of backups to retain for each file.
        """
        self.backup_dir = backup_dir
        self.retention_count = retention_count

    def _ensure_backup_dir(self) -> None:
        """Create the backup directory on first use."""
        if not self.backup_dir.exists():
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _create_backup(self, file_path: Path) -> Path:
        """Create a timestamped backup of the file in the backup directory."""
        self._ensure_backup_dir()
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_filename = f"{file_path.name}.{timestamp}.backup"
            backup_path = self.backup_dir / backup_filename

            shutil.copy(file_path, backup_path)
            logger.info(f"Created backup for {file_path} at {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create backup for {file_path}", exc_info=True)
            raise BackupError(f"Failed to create backup for {file_path}: {e}") from e

    def _cleanup_old_backups(self, original_filename: str) -> None:
        """Remove old backup files for a given original file beyond the retention limit."""
        try:
            backup_pattern = f"{original_filename}.*.backup"
            backup_files = sorted(
                self.backup_dir.glob(backup_pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if len(backup_files) > self.retention_count:
                files_to_remove = backup_files[self.retention_count:]
                for file_path in files_to_remove:
                    file_path.unlink()
                    logger.info(f"Removed old backup: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up old backups for {original_filename}: {e}", exc_info=True)

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Flush a directory's entries to disk (POSIX). No-op on platforms
        where directories can't be opened as file descriptors (Windows).
        """
        try:
            dir_fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return  # e.g. Windows — directory fsync isn't applicable
        try:
            try:
                os.fsync(dir_fd)
            except OSError as e:
                # Some filesystems (e.g. tmpfs in containers) refuse fsync
                # on directories. Logged, not fatal — the file data fsync
                # is the durability-critical part.
                logger.debug("Directory fsync on %s skipped: %s", directory, e)
        finally:
            os.close(dir_fd)

    @contextmanager
    def atomic_write(self, file_path_str: str, encoding: str = 'utf-8') -> Generator[IO, None, None]:
        """
        A context manager for atomic, backed-up file writes for both reading and writing.

        - Creates a backup of the original file.
        - Creates a temporary file and copies the original content into it.
        - Yields a file handle to this temporary file, ready for reading and writing.
        - Before swapping, ``fsync`` flushes the temp file's data to disk and
          (on POSIX) the parent directory's entry change is fsynced after the
          rename. This makes the write durable across power loss — without
          these fsyncs, ``os.replace`` only guarantees *visibility* atomicity,
          not durability, and a crash between rename and the kernel's next
          writeback can leave the target as the old content, a zero-byte
          file, or the new content depending on the filesystem.
        - On successful exit, the modified temporary file atomically replaces the original.
        - If an error occurs, the temporary file is discarded, leaving the original untouched.
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
                # Flush Python's buffer, then push the kernel page cache to
                # disk while we still have the fd open. Without this, the
                # rename below can land before the data does.
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, file_path)
            # The rename swap is now visible; flush the directory entry so
            # the swap itself survives a crash.
            self._fsync_dir(file_path.parent)
            success = True
            logger.info(f"Successfully wrote to {file_path}")

        except Exception:
            logger.error(f"Atomic write to {file_path} failed. Original file is safe.", exc_info=True)
            raise
        finally:
            if success:
                self._cleanup_old_backups(file_path.name)
            elif temp_path is not None:
                logger.warning(f"An error occurred during write operation. Cleaning up temporary file {temp_path}")
                temp_path.unlink(missing_ok=True)
