"""Single source of truth for the app version.

Reads /VERSION at the repo root in development, or from the bundle root
inside a PyInstaller package. The file is plain text, one line.

A build may also carry a BUILD_INFO file holding the commit it was built from.
When present it is appended as semver build metadata — `0.2.1+3894c8a` — so a
test build handed to a user identifies itself instead of being indistinguishable
from every other build of the same version. Release builds ship without
BUILD_INFO, so `vX.Y.Z` displays as a clean `X.Y.Z`. The commit cannot live in
/VERSION itself: committing that file would change the SHA it names. See
`desktop/build.py:write_build_info`.

The version is display-only (About screen, root and health endpoints); nothing
parses or compares it, so the suffix is safe to carry.
"""
import os
import sys
from pathlib import Path


def _candidate_dirs() -> list[Path]:
    """Where VERSION / BUILD_INFO may live, most specific first."""
    dirs = []
    if getattr(sys, 'frozen', False):
        # PyInstaller bundle — both files included via finzytrack.spec datas
        dirs.append(Path(sys._MEIPASS))
    # Development: repo root, two levels above this file (backend/app/_version.py)
    dirs.append(Path(__file__).resolve().parents[2])
    return dirs


def _read_version() -> str:
    for directory in _candidate_dirs():
        path = directory / 'VERSION'
        if path.exists():
            return path.read_text().strip()
    raise FileNotFoundError(
        'VERSION file not found in any of: '
        f'{[str(d / "VERSION") for d in _candidate_dirs()]}'
    )


def _read_build_stamp() -> str:
    """The commit this build came from; '' for release builds and dev runs."""
    for directory in _candidate_dirs():
        path = directory / 'BUILD_INFO'
        if path.exists():
            return path.read_text().strip()
    return ''


def _compose(version: str, stamp: str) -> str:
    return f'{version}+{stamp}' if stamp else version


__version__ = _compose(_read_version(), _read_build_stamp())
