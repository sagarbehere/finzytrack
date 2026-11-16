"""The app folder of the engine's start (S0102, BL-0244; owner, 2026-11-11: "BL-0244 slice in M1a (Recommended)";
`design/specs/engine-jobs.md` §5.1, §6.1 step 8).

The sidecar makes the app folder and syncs its parent at start, and records its identity (`port.FolderId`:
`(st_dev, st_ino)`, and on Linux its creation time too, BL-0262; links followed, as the save and recover-all follow
them); every engine is started with it (`--app-folder-id DEV:INO` or `DEV:INO:BORN`, with
`--engine-lock <app folder>/locks/engine.lock`). Nothing of ours writes in a folder whose entry the start did not make
durable: the engine lock (`server.hold_engine_lock`) checks the descriptor it then makes `locks/` through, and the ops
that write or sync in the app folder (`OpSpec.writes_app_folder`) run only while `changed` is False.
"""

from __future__ import annotations

import os

from ledger_engine.fs.port import FileSystem, FolderId, same_folder
from ledger_engine.fs.this_os import UnsupportedPlatform, for_this_os

type Identity = FolderId


class AppFolderChangedError(OSError):
    """The app folder is missing, or another object stands at its path, since the engine's start."""


def folder_of_lock(lock: str) -> str:
    """The app folder the engine lock `<app folder>/locks/engine.lock` lives in."""
    return os.path.dirname(os.path.dirname(os.path.abspath(lock)))


def parse_identity(text: str) -> Identity:
    """`DEV:INO` or `DEV:INO:BORN`, non-negative integers (`--app-folder-id`; BORN, the creation time in ns, on
    Linux: BL-0262)."""
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(p.isascii() and p.isdigit() for p in parts):
        msg = f"{text!r} is not DEV:INO or DEV:INO:BORN"
        raise ValueError(msg)
    if len(parts) == 2:
        return (int(parts[0]), int(parts[1]))
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def changed(lock: str | None, identity: Identity | None, fs: FileSystem | None = None) -> bool:
    """Whether the app folder of the engine lock `lock` is no longer the object of the start (`identity`): gone, not a
    folder, or another folder. Fail closed: no lock, no identity, or a folder whose identity cannot be read through its
    descriptor (a permission taken away, a loop of links: S0102 round 1, red team F6, verifier V1) is changed too, so
    nothing is written in a folder we cannot judge. On an operating system without a port, `UnsupportedPlatform` is
    raised, never taken for a changed folder (BL-0261: the boot said "the app folder is missing" there)."""
    if lock is None or identity is None:
        return True
    try:
        with (fs or for_this_os()).open_dir(folder_of_lock(lock)) as h:
            return not same_folder(h, identity)
    except UnsupportedPlatform:
        raise
    except (OSError, ValueError):
        return True
