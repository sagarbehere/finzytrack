"""The sidecar's wait for the engine lock (S0053; `design/specs/engine-jobs.md` §5.1, §5.5; ADR-0064 amendment of
2026-10-28, simplification B).

One lock file, `app_local_data/locks/engine.lock`, never deleted. Every engine takes it with a whole-file `flock`
after its handshake and holds it for its life (`ledger_engine.protocol.server`). At start, before its first engine,
the sidecar takes it itself, waiting, and releases it at once: each engine must hold it on its own, so that an engine
of a crashed session keeps holding it. So an engine of an earlier session (left by a crash, Quit anyway or a quit)
has finished or died before this session does any ledger work. The wait has no time limit (§5.4): a process stuck
in the kernel keeps the lock until the disk answers, and the user is told to quit and reopen (§5.2, §15).

POSIX only for now: Windows' lock and its Job Object are designed with BL-0087 (M1b, spec §15), and there the wait
returns at once.
"""

from __future__ import annotations

import os
from pathlib import Path

LOCKS_FOLDER = "locks"
ENGINE_LOCK_FILE = "engine.lock"


def engine_lock_path(app_local_data: Path) -> Path:
    """`app_local_data/locks/engine.lock`, beside the ledger-set locks (spec §5.1, §6.1 step 3)."""
    return app_local_data / LOCKS_FOLDER / ENGINE_LOCK_FILE


def wait_engine_lock(path: Path) -> None:
    """Take the engine lock, waiting for as long as another process holds it, and release it at once. Creates the
    folder and the file if they are missing; never deletes them."""
    if os.name != "posix":  # pragma: no cover (Windows)
        return  # TODO(BL-0087): the Windows lock (LockFileEx), with the engine's nested Job Object.
    import fcntl  # POSIX only

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
