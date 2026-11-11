"""The app folder of the engine's start (S0102, BL-0244; owner, 2026-11-11: "BL-0244 slice in M1a (Recommended)";
`design/specs/engine-jobs.md` §5.1, §6.1 step 8).

The sidecar makes the app folder and syncs its parent at start, and records its identity `(st_dev, st_ino)` (links
followed, as the save and recover-all follow them); every engine is started with it (`--app-folder-id DEV:INO`, with
`--engine-lock <app folder>/locks/engine.lock`). Nothing of ours writes in a folder whose entry the start did not make
durable: the engine lock (`server.hold_engine_lock`) checks the descriptor it then makes `locks/` through, and the ops
that write or sync in the app folder (`OpSpec.writes_app_folder`) run only while `changed` is False.
"""

from __future__ import annotations

import os

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import FileSystem

type Identity = tuple[int, int]


class AppFolderChangedError(OSError):
    """The app folder is missing, or another object stands at its path, since the engine's start."""


def folder_of_lock(lock: str) -> str:
    """The app folder the engine lock `<app folder>/locks/engine.lock` lives in."""
    return os.path.dirname(os.path.dirname(os.path.abspath(lock)))


def parse_identity(text: str) -> Identity:
    """`DEV:INO`, two non-negative integers (`--app-folder-id`)."""
    dev, sep, ino = text.partition(":")
    if not (sep and dev.isdigit() and ino.isdigit()):
        msg = f"{text!r} is not DEV:INO"
        raise ValueError(msg)
    return (int(dev), int(ino))


def changed(lock: str | None, identity: Identity | None, fs: FileSystem | None = None) -> bool:
    """Whether the app folder of the engine lock `lock` is no longer the object of the start (`identity`): gone, not a
    folder, or another folder. No lock or no identity: True (nothing is known, so nothing is written). A folder that
    cannot be opened for another reason (a permission taken away) is not called changed: the op then meets that error
    itself and says so."""
    if lock is None or identity is None:
        return True
    try:
        with (fs or for_this_os()).open_dir(folder_of_lock(lock)) as h:
            return h.identity() != identity
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
