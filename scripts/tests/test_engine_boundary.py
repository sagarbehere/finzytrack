"""Meta-tests for scripts/check_engine_boundary.py (ADR-0054 decision 5; INV-02; ARCHITECTURE.md §2.0, §2.4 rule 4).

The rules, from the spec:
1. `beancount` and `beanquery` are imported only inside `engine/` and by the dev tooling that runs in its own
   Beancount environment and never ships (`scripts/oracle/`, and `corpora/`, whose plugin fixtures run inside
   Beancount's loader).
2. Inside `engine/`, only `src/ledger_engine/ledger/beancount_port.py` imports them (INV-02's single gateway);
   the engine's tests may too (Beancount is their oracle).
3. Nothing in `engine/` imports the main sidecar's package `app`.
4. Nothing outside `engine/` imports the engine's package `ledger_engine` (the boundary is the protocol).
Each rule sits beside the planted case that must not pass (EP-29).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_engine_boundary as ceb  # noqa: E402

GATEWAY = "engine/src/ledger_engine/ledger/beancount_port.py"
IMPORTS = (
    "import beancount\n",
    "import beancount.loader as loader\n",
    "from beancount.core import data\n",
    "from beanquery import query\n",
    "import importlib\nimportlib.import_module('beancount.parser')\n",
    "from importlib import import_module\nimport_module('beanquery')\n",
    "__import__('beancount')\n",
    "def f():\n    import beancount\n",
    "try:\n    import beancount\nexcept ImportError:\n    pass\n",
    "import os, beancount\n",
)

EVASIONS = (
    "from importlib import import_module as im\nim('beancount.loader')\n",
    "import importlib\nimportlib.import_module(name='beancount')\n",
    "__import__(name='beancount.core')\n",
    "exec('import beancount')\n",
    "exec('from beancount import loader')\n",
    "eval(\"__import__('beancount')\")\n",
    "import runpy\nrunpy.run_module('beancount.scripts.check')\n",
    "from runpy import run_module\nrun_module(mod_name='beancount.scripts.check')\n",
    "import pkgutil\npkgutil.resolve_name('beancount.loader:load_file')\n",
    "import pkgutil\npkgutil.resolve_name('beancount:loader')\n",
)
DYNAMIC = (
    "import importlib\nimportlib.import_module(name)\n",
    "from importlib import import_module as im\nim(name)\n",
    "load = __import__\nload(name)\n",
    "__import__(name)\n",
    "import runpy\nrunpy.run_path(path)\n",
    "import runpy\n",
    "import pkgutil\npkgutil.resolve_name(name)\n",
    "exec(code)\n",
    "eval(code)\n",
    "import builtins\nbuiltins.exec(code)\n",
    "f = getattr(importlib, 'import_module')\n",
    "spec.loader.exec_module(module)\n",
)


class Boundary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def plant(self, rel: str, text: str) -> list[str]:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return ceb.violations(self.root, [rel])

    def test_beancount_in_the_main_sidecar_or_scripts_is_refused(self) -> None:
        for where in ("backend/src/app/planted.py", "backend/tests/test_planted.py", "scripts/planted.py",
                      "shell/sidecar/planted.py", "planted.py", "scripts/oracle_like/planted.py"):
            for text in IMPORTS:
                with self.subTest(where=where, text=text):
                    self.assertTrue(self.plant(where, text), f"{where}: {text!r} passed")

    def test_beancount_inside_the_engine_outside_the_gateway_is_refused(self) -> None:
        for where in ("engine/src/ledger_engine/protocol/planted.py", "engine/src/ledger_engine/ledger/overlay.py",
                      "engine/src/ledger_engine/beancount_port.py", "engine/src/planted.py", "engine/planted.py"):
            for text in IMPORTS:
                with self.subTest(where=where, text=text):
                    self.assertTrue(self.plant(where, text), f"{where}: {text!r} passed")

    def test_the_gateway_and_the_oracle_environments_may_import_beancount(self) -> None:
        for where in (GATEWAY, "engine/tests/test_vectors.py", "scripts/oracle/planted.py",
                      "scripts/oracle/tests/test_planted.py", "corpora/ledgers/write/plugin/tagger.py"):
            for text in IMPORTS:
                with self.subTest(where=where, text=text):
                    self.assertEqual(self.plant(where, text), [])

    def test_the_engine_importing_the_main_sidecar_is_refused(self) -> None:
        for where in ("engine/src/ledger_engine/planted.py", "engine/tests/test_planted.py", GATEWAY):
            for text in ("import app\n", "from app.platform import clock\n", "import app.main\n",
                         "import importlib\nimportlib.import_module('app.api')\n"):
                with self.subTest(where=where, text=text):
                    self.assertTrue(self.plant(where, text))

    def test_the_main_sidecar_importing_the_engine_is_refused(self) -> None:
        for where in ("backend/src/app/platform/planted.py", "backend/tests/test_planted.py", "scripts/planted.py"):
            for text in ("import ledger_engine\n", "from ledger_engine.protocol import messages\n"):
                with self.subTest(where=where, text=text):
                    self.assertTrue(self.plant(where, text))

    def test_the_engine_may_import_itself(self) -> None:
        self.assertEqual(self.plant("engine/src/ledger_engine/planted.py", "from ledger_engine.protocol import x\n"), [])

    def test_similar_names_and_relative_imports_pass(self) -> None:
        for text in ("import application\n", "import apps\n", "from .app import x\n", "import beancountish\n",
                     "from . import beancount\n", "import ledger_engines\n"):
            with self.subTest(text=text):
                self.assertEqual(self.plant("backend/src/app/planted.py", text), [])
                self.assertEqual(self.plant("engine/src/ledger_engine/planted.py", text), [])

    def test_literal_imports_in_other_shapes_are_seen_everywhere(self) -> None:
        """S0035 independent verifier F5: a literal Beancount name reached by an aliased or keyword import call,
        exec/eval of a literal, runpy or pkgutil.resolve_name is an import too."""
        for text in EVASIONS:
            for where in ("backend/src/app/planted.py", "scripts/planted.py", "engine/tests/test_planted.py"):
                with self.subTest(where=where, text=text):
                    found = self.plant(where, text)
                    if where.startswith("engine/tests/"):
                        self.assertEqual(found, [], f"{where}: {text!r} refused")  # the engine's tests may
                    else:
                        self.assertTrue(found, f"{where}: {text!r} passed")
            with self.subTest(where="the engine's tests importing app", text=text):
                self.assertTrue(self.plant("engine/tests/test_planted.py", text.replace("beancount", "app")))

    def test_any_dynamic_import_or_exec_in_the_engine_source_is_refused(self) -> None:
        """EP-30, conservative: inside engine/src only the gateway may import, run or evaluate code by name, so a
        computed name cannot carry Beancount (or `app`) past the scan."""
        for text in DYNAMIC:
            for where in ("engine/src/ledger_engine/protocol/planted.py", "engine/src/ledger_engine/ledger/overlay.py"):
                with self.subTest(where=where, text=text):
                    self.assertTrue(self.plant(where, text), f"{where}: {text!r} passed")
            with self.subTest(where=GATEWAY, text=text):
                self.assertEqual(self.plant(GATEWAY, text), [])
            with self.subTest(where="engine/tests", text=text):
                self.assertEqual(self.plant("engine/tests/test_planted.py", text), [])

    def test_ordinary_engine_code_passes(self) -> None:
        for text in ("from importlib.metadata import version\nversion('beancount')\n", "import re\nre.compile('x')\n",
                     "x = {'exec': 1}\n", "def evaluate(): pass\n", "import json\njson.loads('1')\n"):
            with self.subTest(text=text):
                self.assertEqual(self.plant("engine/src/ledger_engine/protocol/planted.py", text), [])

    def test_an_unparsable_file_fails_closed(self) -> None:
        self.assertTrue(self.plant("backend/src/app/planted.py", "def (:\n"))

    def test_the_repository_holds(self) -> None:
        self.assertEqual(ceb.main([]), 0)


if __name__ == "__main__":
    unittest.main()
