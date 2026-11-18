"""A path the engine sent, cut for a text (`app.platform.engine_paths`; S0131, BL-0295): its last name and its folder,
by the separator of the OS the engine runs on. Both OSes' cases run everywhere (the cut takes the platform)."""

from __future__ import annotations

import pytest

from app.platform.engine_paths import folder_of, last_name


@pytest.mark.parametrize(
    ("platform", "path", "name", "folder"),
    [
        ("darwin", "/Books/a/main.bean", "main.bean", "/Books/a"),
        ("linux", "/srv/Books/main.bean", "main.bean", "/srv/Books"),
        ("darwin", "/main.bean", "main.bean", ""),  # the root's own entry: no folder to name (as before S0131)
        ("win32", "D:\\Books\\a\\main.bean", "main.bean", "D:\\Books\\a"),
        ("win32", "\\\\nas\\share\\Books\\main.bean", "main.bean", "\\\\nas\\share\\Books"),
        ("win32", "\\\\?\\C:\\Books\\main.bean", "main.bean", "\\\\?\\C:\\Books"),
        ("win32", "C:\\main.bean", "main.bean", "C:\\"),
        ("darwin", "/Books/a\\b.bean", "a\\b.bean", "/Books"),  # `\` is a name's character on POSIX
    ],
)
def test_a_path_is_cut_at_its_oss_separator(platform: str, path: str, name: str, folder: str) -> None:
    """Must not pass: a Windows path cut at `/` only (its whole text named as a file, or no folder found), or a POSIX
    name holding `\\` cut there."""
    assert last_name(path, platform) == name
    assert folder_of(path, platform) == folder
