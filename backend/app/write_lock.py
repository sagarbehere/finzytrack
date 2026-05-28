"""
Per-user write lock for serialising ledger mutations.

Two layers of locking:

1. ``threading.Lock`` — fast intra-process path. Required because
   ``LedgerManager._write_entries()`` is synchronous and FastAPI runs sync
   route handlers / dependencies in a threadpool. An ``asyncio.Lock`` would
   not protect against concurrent threadpool threads.

2. ``portalocker`` exclusive file lock on a sidecar ``.lock`` file —
   inter-process path. Catches the case where two instances of FinzyTrack
   itself run against the same user's data directory (dev uvicorn + the
   desktop launcher, accidental double-launch, future hosted multi-worker
   deployment). The lock file is per-user (lives in the user's data dir).

What this does NOT protect against:
- External writers that don't cooperate with our lock (e.g. ``$EDITOR``
  editing ``.beancount`` directly). File locks are advisory in
  ``portalocker``'s default mode and only work between processes that both
  call ``portalocker.lock(...)``.
- A user deleting the lock file by hand.

Document these limitations to users and operators when relevant.
"""

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

import portalocker

logger = logging.getLogger(__name__)


class WriteLockManager:
    """Two-layer write lock: intra-process ``threading.Lock`` + inter-process
    ``portalocker`` exclusive lock on a sidecar file.

    The file lock is optional: tests and unit-test fixtures can construct
    a manager without a path and get the original intra-process-only
    behaviour. Production wiring always supplies the path so multi-process
    races are caught.
    """

    def __init__(
        self,
        user_id: str = "local",
        lock_file: Optional[Path] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._user_id = user_id
        self._lock_file = Path(lock_file) if lock_file is not None else None
        if self._lock_file is not None:
            # Ensure the parent directory exists; the lock file itself is
            # created lazily on first acquire.
            self._lock_file.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def acquire(self, operation: str = "write") -> Generator[None, None, None]:
        logger.debug("[%s] Acquiring write lock for %s", self._user_id, operation)
        self._lock.acquire()
        try:
            if self._lock_file is None:
                yield
                return
            # Open the sidecar in append mode so it auto-creates without
            # truncating any prior content (we never write to it).
            with open(self._lock_file, "a") as fp:
                portalocker.lock(fp, portalocker.LOCK_EX)
                try:
                    yield
                finally:
                    portalocker.unlock(fp)
        finally:
            self._lock.release()
            logger.debug("[%s] Released write lock for %s", self._user_id, operation)
