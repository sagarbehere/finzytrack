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
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = backup_dir
        self.retention_count = retention_count

    def _create_backup(self, file_path: Path) -> Path:
        """Create a timestamped backup of the file in the backup directory."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_filename = f"{file_path.name}.{timestamp}.backup"
            backup_path = self.backup_dir / backup_filename
            
            shutil.copy2(file_path, backup_path)
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

    @contextmanager
    def atomic_write(self, file_path_str: str, encoding: str = 'utf-8') -> Generator[IO, None, None]:
        """
        A context manager for atomic, backed-up file writes for both reading and writing.

        - Creates a backup of the original file.
        - Creates a temporary file and copies the original content into it.
        - Yields a file handle to this temporary file, ready for reading and writing.
        - On successful exit, the modified temporary file atomically replaces the original.
        - If an error occurs, the temporary file is discarded, leaving the original untouched.
        """
        file_path = Path(file_path_str)
        temp_file_handle = None
        temp_path = None
        
        try:
            if file_path.exists():
                self._create_backup(file_path)

            fd, temp_path_str = tempfile.mkstemp(dir=file_path.parent, prefix=f".{file_path.name}")
            temp_path = Path(temp_path_str)
            
            if file_path.exists():
                shutil.copy2(file_path, temp_path)

            with open(temp_path, 'r+', encoding=encoding) as f:
                temp_file_handle = f
                f.seek(0)
                yield f
            
            if file_path.exists():
                shutil.copystat(file_path, temp_path)
            os.rename(temp_path, file_path)
            logger.info(f"Successfully wrote to {file_path}")
            temp_file_handle = None

        except Exception as e:
            logger.error(f"Atomic write to {file_path} failed. Original file is safe.", exc_info=True)
            raise
        finally:
            if temp_file_handle is not None and temp_path is not None:
                logger.warning(f"An error occurred during write operation. Cleaning up temporary file {temp_path}")
                temp_path.unlink(missing_ok=True)
            elif temp_path is not None and not temp_path.exists():
                 self._cleanup_old_backups(file_path.name)
