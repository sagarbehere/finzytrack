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

import contextlib
import os
from pathlib import Path

from app.platform.adapters.app_folder import AppFolder

LOCKS_FOLDER = "locks"
ENGINE_LOCK_FILE = "engine.lock"


def engine_lock_path(app_local_data: Path) -> Path:
    """`app_local_data/locks/engine.lock` (spec §5.1; the one lock of the save since ADR-0068 D3)."""
    return app_local_data / LOCKS_FOLDER / ENGINE_LOCK_FILE


def wait_engine_lock(folder: AppFolder) -> None:
    """Take the engine lock in `folder`, waiting for as long as another process holds it, and release it at once.
    Creates the `locks/` folder and the file if they are missing (never the app's folder above it, made at start by
    `app.main`: S0100, BL-0163 (a)); never deletes them. Both are made through a descriptor on the app folder that
    is still the object of the start (`AppFolder.open`; S0102, BL-0244): a folder removed or replaced since raises
    `AppFolderChangedError`, with nothing made."""
    if os.name != "posix":  # pragma: no cover (Windows)
        return  # TODO(BL-0087): the Windows lock (LockFileEx), with the engine's nested Job Object.
    import fcntl  # POSIX only

    dfd = folder.open()
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(LOCKS_FOLDER, dir_fd=dfd)
        fd = os.open(f"{LOCKS_FOLDER}/{ENGINE_LOCK_FILE}", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
