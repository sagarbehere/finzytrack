"""A path the engine sent, cut for a text the user reads (S0131, BL-0295): its last name and the folder that holds it.

The engine sends each OS's own absolute paths unchanged (`app.platform.engine_protocol.is_absolute`): on macOS and
Linux they are cut at `/` (a `\\` there is a character of a name); on Windows at `\\` or `/`, after the drive or UNC
root, as `ntpath` cuts them. Before S0131 every text cut at `/`, so on Windows a text named a file by its whole path.
"""

from __future__ import annotations

import ntpath
import sys


def last_name(path: str, platform: str = sys.platform) -> str:
    """The last name of `path`: `main.bean` of `/L/main.bean`, and of `C:\\L\\main.bean` on Windows."""
    if platform == "win32":
        return ntpath.basename(path)
    return path.rpartition("/")[2]


def folder_of(path: str, platform: str = sys.platform) -> str:
    """The folder that holds `path`. On macOS and Linux "" for an entry of the root (`/main.bean`), as the texts cut it
    before S0131; on Windows the drive's or share's root itself (`C:\\`)."""
    if platform == "win32":
        return ntpath.dirname(path)
    return path.rpartition("/")[0]
