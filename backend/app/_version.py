"""Single source of truth for the app version.

Reads /VERSION at the repo root in development, or from the bundle root
inside a PyInstaller package. The file is plain text, one line.
"""
import os
import sys
from pathlib import Path


def _read_version() -> str:
    candidates = []
    if getattr(sys, 'frozen', False):
        # PyInstaller bundle — VERSION is included via finzytrack.spec datas
        candidates.append(Path(sys._MEIPASS) / 'VERSION')
    # Development: repo root, two levels above this file (backend/app/_version.py)
    candidates.append(Path(__file__).resolve().parents[2] / 'VERSION')
    for path in candidates:
        if path.exists():
            return path.read_text().strip()
    raise FileNotFoundError(
        f'VERSION file not found in any of: {[str(p) for p in candidates]}'
    )


__version__ = _read_version()
