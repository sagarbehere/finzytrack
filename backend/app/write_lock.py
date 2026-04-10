"""
Per-user write lock for serialising ledger mutations.

Uses ``threading.Lock`` (not ``asyncio.Lock``) because
``LedgerManager._write_entries()`` is synchronous and FastAPI runs sync
route handlers / dependencies in a threadpool.  An ``asyncio.Lock`` would
not protect against concurrent threadpool threads.
"""

import logging
import threading
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


class WriteLockManager:
    """Thin wrapper around ``threading.Lock`` with debug logging."""

    def __init__(self, user_id: str = "local") -> None:
        self._lock = threading.Lock()
        self._user_id = user_id

    @contextmanager
    def acquire(self, operation: str = "write") -> Generator[None, None, None]:
        logger.debug("[%s] Acquiring write lock for %s", self._user_id, operation)
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()
            logger.debug("[%s] Released write lock for %s", self._user_id, operation)
