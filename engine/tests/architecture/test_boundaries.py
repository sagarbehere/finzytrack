"""The engine's boundaries, from inside its own environment (ADR-0054 decision 5 and Consequences; INV-02).

The static import rules (only `ledger_engine/ledger/beancount_port.py` imports Beancount; the engine imports
nothing from the main sidecar; nothing outside `engine/` imports the engine) are one repo-wide scan,
`scripts/check_engine_boundary.py`, with its planted cases in `scripts/tests/test_engine_boundary.py`. Here: the
scan holds on the tree, and this environment is the engine's (it has Beancount and cannot import `app`).
"""

from __future__ import annotations

import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_the_import_boundaries_hold_on_the_tree() -> None:
    r = subprocess.run(  # noqa: S603 (fixed argv: this interpreter and a repo script)
        [sys.executable, str(REPO / "scripts" / "check_engine_boundary.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_engine_environment_cannot_import_the_main_sidecar() -> None:
    assert find_spec("app") is None


def test_the_engine_environment_has_beancount() -> None:
    # ADR-0054 decision 1: Beancount lives in the engine's environment.
    assert find_spec("beancount") is not None
