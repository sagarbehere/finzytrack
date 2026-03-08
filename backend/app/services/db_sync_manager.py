"""
DB Sync Manager - Orchestrates WHEN to export with debouncing.

This service manages the timing and orchestration of database exports,
implementing debouncing logic to prevent thrashing during bulk imports.
It delegates the actual export work to the provided exporter.
"""
import os
import asyncio
import logging
from typing import List, Any, Optional, Union

logger = logging.getLogger(__name__)


class DBSyncManager:
    """Manages database sync with debouncing (database-agnostic)."""

    def __init__(
        self,
        exporter: Any,
        ledger_file: str,
        delay: float = 5.0,
        db_type: str = "database"
    ):
        """
        Initialize DB sync manager.

        Args:
            exporter: Exporter instance with export_entries() method and export_path attribute
            ledger_file: Path to Beancount ledger file (for mtime checking)
            delay: Debounce delay in seconds (default 5.0)
            db_type: Database type name for logging (e.g., "sqlite")
        """
        self.exporter = exporter
        self.ledger_file = ledger_file
        self.delay = delay
        self.db_type = db_type
        self._pending_task: Optional[asyncio.Task] = None

    async def debounced_export(self, entries: List[Any]) -> None:
        """
        Export with debounce - waits X seconds after last change.

        If called multiple times rapidly, only the last call will trigger export
        after the delay period.

        Args:
            entries: Parsed Beancount entries from cache
        """
        # Cancel pending export if exists
        if self._pending_task and not self._pending_task.done():
            logger.debug(f"Cancelling pending {self.db_type} export task")
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass

        async def delayed_export():
            """Inner coroutine for delayed export."""
            try:
                logger.debug(f"{self.db_type} export debounce: waiting {self.delay}s")
                await asyncio.sleep(self.delay)

                # Check if export is actually needed
                if not self._needs_export():
                    logger.debug(f"{self.db_type} is up-to-date, skipping export")
                    return

                logger.info(f"Starting debounced {self.db_type} export")
                await self.exporter.export_entries(entries)
                logger.info(f"{self.db_type} auto-exported after ledger change")

            except asyncio.CancelledError:
                logger.debug(f"{self.db_type} export task cancelled")
                raise
            except Exception as e:
                logger.error(f"{self.db_type} auto-export failed: {e}", exc_info=True)

        # Create new task for delayed export
        self._pending_task = asyncio.create_task(delayed_export())

    def on_ledger_changed(self, entries: List[Any]) -> None:
        """
        Callback for BeancountManager observer pattern.

        This is called when the ledger cache is invalidated.

        Args:
            entries: Parsed Beancount entries from cache
        """
        logger.debug(f"Ledger changed, triggering debounced {self.db_type} export")
        # Create task without awaiting (fire and forget)
        asyncio.create_task(self.debounced_export(entries))

    def _needs_export(self) -> bool:
        """
        Check if database needs to be regenerated based on file modification time.

        Returns:
            True if export is needed, False otherwise
        """
        try:
            if not os.path.exists(self.exporter.export_path):
                logger.debug(f"{self.db_type} file doesn't exist, export needed")
                return True

            if not os.path.exists(self.ledger_file):
                logger.warning("Ledger file doesn't exist, skipping export")
                return False

            db_mtime = os.path.getmtime(self.exporter.export_path)
            ledger_mtime = os.path.getmtime(self.ledger_file)

            needs_export = ledger_mtime > db_mtime
            if needs_export:
                logger.debug(f"Ledger modified after {self.db_type}, export needed")
            else:
                logger.debug(f"{self.db_type} is up-to-date")

            return needs_export

        except Exception as e:
            logger.error(f"Error checking if export needed: {e}")
            return True  # Err on the side of exporting

    async def cancel_pending_sync(self) -> None:
        """
        Cancel any pending debounced export.

        Useful for graceful shutdown.
        """
        if self._pending_task and not self._pending_task.done():
            logger.info(f"Cancelling pending {self.db_type} sync for shutdown")
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
