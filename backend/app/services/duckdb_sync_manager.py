"""
DuckDB Sync Manager - Orchestrates WHEN to export with debouncing.

This service manages the timing and orchestration of DuckDB exports,
implementing debouncing logic to prevent thrashing during bulk imports.
It delegates the actual export work to DuckDBExporter.
"""
import os
import asyncio
import logging
from typing import List, Any, Optional

from app.services.duckdb_exporter import DuckDBExporter

logger = logging.getLogger(__name__)


class DuckDBSyncManager:
    """Manages DuckDB sync with debouncing."""

    def __init__(
        self,
        duckdb_exporter: DuckDBExporter,
        ledger_file: str,
        delay: float = 5.0
    ):
        """
        Initialize DuckDB sync manager.

        Args:
            duckdb_exporter: DuckDBExporter instance (the worker)
            ledger_file: Path to Beancount ledger file (for mtime checking)
            delay: Debounce delay in seconds (default 5.0)
        """
        self.exporter = duckdb_exporter
        self.ledger_file = ledger_file
        self.delay = delay
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
            logger.debug("Cancelling pending DuckDB export task")
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass

        async def delayed_export():
            """Inner coroutine for delayed export."""
            try:
                logger.debug(f"DuckDB export debounce: waiting {self.delay}s")
                await asyncio.sleep(self.delay)

                # Check if export is actually needed
                if not self._needs_export():
                    logger.debug("DuckDB is up-to-date, skipping export")
                    return

                logger.info("Starting debounced DuckDB export")
                await self.exporter.export_entries(entries)
                logger.info("DuckDB auto-exported after ledger change")

            except asyncio.CancelledError:
                logger.debug("DuckDB export task cancelled")
                raise
            except Exception as e:
                logger.error(f"DuckDB auto-export failed: {e}", exc_info=True)

        # Create new task for delayed export
        self._pending_task = asyncio.create_task(delayed_export())

    def on_ledger_changed(self, entries: List[Any]) -> None:
        """
        Callback for BeancountManager observer pattern.

        This is called when the ledger cache is invalidated.

        Args:
            entries: Parsed Beancount entries from cache
        """
        logger.debug("Ledger changed, triggering debounced DuckDB export")
        # Create task without awaiting (fire and forget)
        asyncio.create_task(self.debounced_export(entries))

    def _needs_export(self) -> bool:
        """
        Check if DuckDB needs to be regenerated based on file modification time.

        Returns:
            True if export is needed, False otherwise
        """
        try:
            if not os.path.exists(self.exporter.duckdb_path):
                logger.debug("DuckDB file doesn't exist, export needed")
                return True

            if not os.path.exists(self.ledger_file):
                logger.warning("Ledger file doesn't exist, skipping export")
                return False

            duckdb_mtime = os.path.getmtime(self.exporter.duckdb_path)
            ledger_mtime = os.path.getmtime(self.ledger_file)

            needs_export = ledger_mtime > duckdb_mtime
            if needs_export:
                logger.debug(f"Ledger modified after DuckDB, export needed")
            else:
                logger.debug(f"DuckDB is up-to-date")

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
            logger.info("Cancelling pending DuckDB sync for shutdown")
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
