"""Architecture contracts of the main sidecar (ARCHITECTURE.md §2.4; ADR-0054; INV-12 layering, INV-02).

Each contract is checked on the real tree and, beside it, on a copy with a planted violation that the spec
says must not pass (EP-29). The copy shadows the installed package through PYTHONPATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from importlib.util import find_spec
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"


def lint_imports(src: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(src)}
    return subprocess.run(  # noqa: S603 (fixed argv: the venv's own lint-imports)
        [str(LINT_IMPORTS), "--config", str(BACKEND / ".importlinter"), "--no-cache"],
        cwd=src.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    shutil.copytree(BACKEND / "src", src, ignore=shutil.ignore_patterns("__pycache__"))
    return src


def plant(src: Path, module: str, text: str) -> None:
    parts = module.split(".")
    pkg = src.joinpath(*parts[:-1])
    for i in range(1, len(parts)):
        init = src.joinpath(*parts[:i], "__init__.py")
        init.parent.mkdir(parents=True, exist_ok=True)
        init.touch()
    (pkg / f"{parts[-1]}.py").write_text(text)


def test_contracts_hold_on_the_tree() -> None:
    r = lint_imports(BACKEND / "src")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    ("module", "text", "contract"),
    [
        # ADR-0054: no Beancount anywhere in the main sidecar, whichever package.
        ("app.platform.planted", "import beancount\n", "never imports Beancount"),
        ("app.domain.planted", "from beancount.core import data\n", "never imports Beancount"),
        ("app.planted", "import beanquery\n", "never imports Beancount"),
        # Rule 3: domain is pure.
        ("app.domain.planted", "import os\n", "domain is pure"),
        ("app.domain.planted", "from pathlib import Path\n", "domain is pure"),
        ("app.domain.planted", "import pydantic\n", "domain is pure"),
        ("app.domain.planted", "import app.platform.adapters.fs\n", "Kernel layers"),
        # Rule 1: a lower layer never imports a higher one, including optional layers once they exist.
        ("app.domain.planted", "import app.platform\n", "Kernel layers"),
        ("app.platform.planted", "import app.api\n", "Kernel layers"),
        ("app.platform.planted", "import app.main\n", "Kernel layers"),  # app.main is a layer since S0030
        ("app.mirror.planted", "import app.workspaces\n", "Kernel layers"),
        ("app.workspaces.planted", "import app.boot\n", "Kernel layers"),
        # §2.3, ADR-0022, EP-10: content (the demo generator) is pure, with no clock, randomness or file system.
        ("app.content.demo.planted", "import random\n", "content is pure"),
        ("app.content.demo.planted", "import time\n", "content is pure"),
        ("app.content.planted", "from pathlib import Path\n", "content is pure"),
        ("app.content.planted", "import os\n", "content is pure"),
        ("app.content.demo.planted", "import app.platform\n", "content is pure"),
        ("app.content.demo.planted", "import app.boot.lifecycle\n", "content is pure"),
        # EP-15, EP-26 (S0039): only the registered-event log writes log lines; nothing else reaches a handler.
        ("app.planted", "import logging\n", "One logger"),
        ("app.platform.adapters.planted", "from logging import getLogger\n", "One logger"),
        ("app.platform.planted", "import structlog\n", "One logger"),
        ("app.domain.planted", "from structlog.contextvars import bind_contextvars\n", "One logger"),
        ("app.platform.observability_twin", "import logging.handlers\n", "One logger"),
    ],
)
def test_planted_violation_breaks_its_contract(tree: Path, module: str, text: str, contract: str) -> None:
    for extra in ("app.api.routes", "app.workspaces.registry", "app.boot.lifecycle", "app.platform.adapters.fs"):
        plant(tree, extra, "")
    plant(tree, module, text)
    r = lint_imports(tree)
    assert r.returncode != 0, f"planted {module}: {text!r} passed"
    # Every contract's name is printed with KEPT or BROKEN; the planted one must be the one that broke.
    # " BROKEN" may be followed by "(n ignored imports)" (S0039: the one-logger contract ignores its own package).
    broken = [line for line in r.stdout.splitlines() if " BROKEN" in line]
    assert any(contract in line for line in broken), r.stdout


# S0027 verifier F3: every forbidden entry of the purity contracts is planted on its own, so deleting one entry
# from `.importlinter` fails here. The lists are the spec (ARCHITECTURE.md §2.4 rules 3 and 7), not read from
# the contract file.
RULE_3_FORBIDDEN = (
    "fastapi",
    "pydantic",
    "sqlite3",
    "httpx",
    "portalocker",
    "os",
    "pathlib",
    "subprocess",
    "socket",
    "time",
    "random",
    "secrets",
    "uuid",
)
CONTENT_FORBIDDEN = (
    "app.platform",
    "app.mirror",
    "app.workspaces",
    "app.boot",
    "app.api",
    "app.main",
    *RULE_3_FORBIDDEN,
)
# S0040: the engine supervision table decides and the client does the I/O (EP-01): no adapter, no log writer, no
# process, thread, file or clock module. Every time comes in on an event.
SUPERVISION_FORBIDDEN = (
    "app.platform.adapters",
    "app.platform.observability.log",
    "app.platform.observability.redaction",
    "os",
    "pathlib",
    "subprocess",
    "threading",
    "queue",
    "socket",
    "signal",
    "time",
    "random",
    "secrets",
    "uuid",
    "datetime",
)


@pytest.mark.parametrize(
    ("source", "forbidden", "contract"),
    [
        *(("app.domain.planted", m, "domain is pure") for m in RULE_3_FORBIDDEN),
        *(("app.content.demo.planted", m, "content is pure") for m in CONTENT_FORBIDDEN),
        *(("app.platform.engine_supervision", m, "supervision table is pure") for m in SUPERVISION_FORBIDDEN),
    ],
)
def test_each_forbidden_entry_is_enforced(tree: Path, source: str, forbidden: str, contract: str) -> None:
    if forbidden.startswith("app."):
        plant(tree, f"{forbidden}.target", "")
    plant(tree, source, f"import {forbidden}\n")
    r = lint_imports(tree)
    broken = [line for line in r.stdout.splitlines() if line.rstrip().endswith("BROKEN")]
    assert any(contract in line for line in broken), f"{source} importing {forbidden} passed: {r.stdout}"


def test_allowed_direction_passes(tree: Path) -> None:
    # Must pass: a higher layer importing a lower one (guards against contracts that refuse everything).
    plant(tree, "app.api.routes", "import app.platform\nimport app.domain\n")
    plant(tree, "app.platform.clock", "import time\nimport app.domain\n")
    plant(tree, "app.mirror.reader", "import app.platform\n")
    plant(tree, "app.boot.lifecycle", "import app.workspaces\n")  # §2.3: boot may import kernel packages
    plant(tree, "app.workspaces.registry", "import app.mirror\nimport app.platform\n")  # §2.3: may import mirror
    plant(tree, "app.content.demo.planted", "import app.domain\nimport hashlib\n")  # §2.3: content may use domain
    plant(tree, "app.platform.observability.planted", "import logging\nimport structlog\n")  # its one home
    # S0040: the table may use the port's types, the codec's constants and the event registry (data).
    plant(
        tree,
        "app.platform.engine_supervision",
        "import math\nimport app.platform.engine\nimport app.platform.engine_protocol\n"
        "import app.platform.observability.events\n",
    )
    r = lint_imports(tree)
    assert r.returncode == 0, r.stdout + r.stderr


def lock_packages(lock_text: str) -> set[str]:
    return {p["name"] for p in tomllib.loads(lock_text).get("package", [])}


def test_beancount_is_not_importable_in_this_environment() -> None:
    # ADR-0054 decision 5: the main environment cannot import beancount.
    assert find_spec("beancount") is None
    assert find_spec("beanquery") is None


def test_lockfile_has_no_beancount() -> None:
    names = lock_packages((BACKEND / "uv.lock").read_text())
    assert "finzytrack-backend" in names  # the lock was parsed, not empty
    assert not names & {"beancount", "beanquery"}


def test_lock_check_sees_a_planted_beancount() -> None:
    planted = '[[package]]\nname = "beancount"\nversion = "3.2.0"\n'
    assert "beancount" in lock_packages(planted)
