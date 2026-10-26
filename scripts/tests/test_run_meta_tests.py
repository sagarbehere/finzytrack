"""Meta-tests for scripts/run_meta_tests.py (S0034): the parallel runner runs exactly the tests `python3 -m unittest
discover -s scripts/tests` runs, and fails whenever unittest would, and more: a module process that ends without
its result, a module that runs other tests than were discovered, no tests at all."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts/run_meta_tests.py"
PY = sys.executable

# The IDs `python3 -m unittest discover -s <dir>` collects, read through unittest's own command line (the real
# TestProgram and its argument parsing, with `-c`'s import path, which is `-m`'s) and a runner that lists instead
# of running.
LIST_AS_UNITTEST = textwrap.dedent("""
    import sys, unittest
    class Lister:
        def run(self, test):
            def ids(t):
                if isinstance(t, unittest.TestSuite):
                    for x in t:
                        yield from ids(x)
                else:
                    yield t.id()
            print("\\n".join(ids(test)))
            return unittest.TestResult()
    unittest.main(module=None, argv=["python3 -m unittest", "discover", "-s", sys.argv[1]], testRunner=Lister(),
                  exit=False)
""")

PASSING = """
import unittest
class A(unittest.TestCase):
    def test_one(self): pass
    def test_two(self):
        for i in range(3):
            with self.subTest(i=i): pass
    @unittest.skip("not here")
    def test_skipped(self): pass
    @unittest.expectedFailure
    def test_known(self): self.fail()
class B(unittest.TestCase):
    def test_three(self): pass
"""


def unittest_ids(start: Path) -> list[str]:
    r = subprocess.run([PY, "-c", LIST_AS_UNITTEST, str(start)], cwd=REPO, capture_output=True, text=True,
                       check=True, timeout=300)
    return sorted(line for line in r.stdout.splitlines() if line.strip())


def runner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(RUNNER), *args], cwd=REPO, capture_output=True, text=True, timeout=300)


class SameTestsAsUnittestDiscover(unittest.TestCase):
    def test_the_real_suite(self) -> None:
        """The runner discovers the IDs unittest discovers in scripts/tests (this test among them)."""
        listed = runner("--list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        ids = sorted(line for line in listed.stdout.splitlines() if line.strip())
        self.assertEqual(ids, unittest_ids(REPO / "scripts/tests"))
        self.assertIn(f"{Path(__file__).stem}.SameTestsAsUnittestDiscover.test_the_real_suite", ids)
        self.assertGreater(len(ids), 500)

    def test_the_hooks_and_ci_run_the_runner(self) -> None:
        self.assertIn('run "check-script meta-tests" python3 scripts/run_meta_tests.py\n',
                      (REPO / ".githooks/pre-push").read_text())
        self.assertIn("run: python3 scripts/run_meta_tests.py -v\n", (REPO / ".github/workflows/ci.yml").read_text())
        self.assertIn('["python3", "scripts/run_meta_tests.py"]', (REPO / ".claude/hooks/stop_checks.py").read_text())


class Fixtures(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.start = Path(self.tmp.name) / "tests"
        self.start.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, name: str, text: str) -> None:
        path = self.start / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))

    def verdicts(self) -> tuple[int, int]:
        """(unittest discover's exit code, the runner's exit code) on the fixture folder."""
        u = subprocess.run([PY, "-m", "unittest", "discover", "-s", str(self.start), "-q"], cwd=self.tmp.name,
                           capture_output=True, text=True, timeout=300)
        return u.returncode, runner("--start", str(self.start)).returncode

    def test_passing_modules_pass_and_run_every_test(self) -> None:
        self.put("test_fx_a.py", PASSING)
        self.put("test_fx_b.py", PASSING.replace("class B", "class C"))
        self.put("pkg/__init__.py", "")
        self.put("pkg/test_fx_c.py", PASSING)
        self.put("helper_fx.py", PASSING)  # not a test module: discover does not run it, nor does the runner
        self.assertEqual(self.verdicts(), (0, 0))
        r = runner("--start", str(self.start), "-v")
        listed = sorted(runner("--list", "--start", str(self.start)).stdout.split())
        self.assertEqual(listed, unittest_ids(self.start))
        self.assertEqual(len(listed), 15)
        self.assertIn("15 tests in 3 modules (6 processes", r.stderr)
        self.assertIn("skipped=3", r.stderr)

    def test_load_tests_is_honoured(self) -> None:
        self.put("test_fx_load.py", PASSING + textwrap.dedent("""
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([A("test_one")])
        """))
        self.assertEqual(self.verdicts(), (0, 0))
        self.assertEqual(runner("--list", "--start", str(self.start)).stdout.split(), ["test_fx_load.A.test_one"])

    def test_every_kind_of_failure_fails(self) -> None:
        cases = {
            "a failing test": "import unittest\nclass A(unittest.TestCase):\n    def test_x(self): self.fail('no')\n",
            "an erroring test": "import unittest\nclass A(unittest.TestCase):\n    def test_x(self): 1/0\n",
            "a failing subtest": ("import unittest\nclass A(unittest.TestCase):\n    def test_x(self):\n"
                                  "        with self.subTest(1): self.fail()\n"),
            "an unexpected success": ("import unittest\nclass A(unittest.TestCase):\n    @unittest.expectedFailure\n"
                                      "    def test_x(self): pass\n"),
            "a setUpClass error": ("import unittest\nclass A(unittest.TestCase):\n    @classmethod\n"
                                   "    def setUpClass(cls): raise RuntimeError\n    def test_x(self): pass\n"),
            "a module that cannot be imported": "import no_such_module_fx\n",
            "a syntax error": "def (:\n",
            # `python3 -m unittest` puts the current folder on the import path, not scripts/; neither may the runner
            "a module only the runner's own folder would provide": "import gate_timer\n",
        }
        for label, text in cases.items():
            with self.subTest(label):
                for p in self.start.glob("*.py"):
                    p.unlink()
                self.put("test_fx_ok.py", PASSING)
                self.put("test_fx_bad.py", text)
                u, r = self.verdicts()
                self.assertNotEqual(u, 0, "the fixture should fail under unittest too")
                self.assertEqual(r, 1)

    def test_a_fixture_that_skips_its_tests_passes_as_under_unittest(self) -> None:  # S0034 verifier round 2 F6
        """A setUpClass or setUpModule raising SkipTest skips its tests without starting them; unittest passes."""
        cases = {
            "setUpClass": """
                import unittest
                class A(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        raise unittest.SkipTest("tool not installed")
                    def test_x(self): pass
                class B(unittest.TestCase):
                    def test_y(self): pass
            """,
            "setUpModule": """
                import unittest
                def setUpModule():
                    raise unittest.SkipTest("tool not installed")
                class A(unittest.TestCase):
                    def test_x(self): pass
                class B(unittest.TestCase):
                    def test_y(self): pass
            """,
            "setUpClass in a module with module fixtures": """
                import unittest
                def setUpModule(): pass
                class A(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        raise unittest.SkipTest("tool not installed")
                    def test_x(self): pass
                class B(unittest.TestCase):
                    def test_y(self): pass
            """,
        }
        for label, text in cases.items():
            with self.subTest(label):
                for p in self.start.glob("*.py"):
                    p.unlink()
                self.put("test_fx_cls.py", text)
                self.assertEqual(self.verdicts(), (0, 0))

    def test_a_skipping_fixture_does_not_excuse_other_tests(self) -> None:  # S0034 verifier round 2 F6
        """Only the tests the parent discovered for the skipped class are excused: drift still fails."""
        count = Path(self.tmp.name) / "count"
        self.put("test_fx_drift.py", textwrap.dedent(f"""
            import pathlib, unittest
            p = pathlib.Path({str(count)!r})
            n = int(p.read_text()) if p.exists() else 0
            p.write_text(str(n + 1))
            class A(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise unittest.SkipTest("tool not installed")
                def test_x(self): pass
            if n:
                A.test_extra = lambda self: None
        """))
        r = runner("--start", str(self.start))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the 1 discovered", r.stderr)

    def test_no_tests_fails(self) -> None:
        self.put("test_fx_empty.py", "import unittest\n")
        self.assertNotEqual(runner("--start", str(self.start)).returncode, 0)
        self.assertNotEqual(runner("--start", str(self.start / "missing")).returncode, 0)

    def test_a_module_process_that_ends_early_fails(self) -> None:
        """A test that ends its process with status 0 hides the rest of its module (unittest itself would pass)."""
        self.put("test_fx_ok.py", PASSING)
        self.put("test_fx_exit.py", ("import os, unittest\nclass A(unittest.TestCase):\n"
                                     "    def test_x(self): os._exit(0)\n    def test_y(self): self.fail()\n"))
        r = runner("--start", str(self.start))
        self.assertEqual(r.returncode, 1)
        self.assertIn("without a result", r.stderr)

    def test_a_module_that_runs_other_tests_than_discovered_fails(self) -> None:
        """The module's tests depend on how often it was imported: the parent sees one, the module's process two."""
        count = Path(self.tmp.name) / "count"
        self.put("test_fx_drift.py", textwrap.dedent(f"""
            import pathlib, unittest
            p = pathlib.Path({str(count)!r})
            n = int(p.read_text()) if p.exists() else 0
            p.write_text(str(n + 1))
            class A(unittest.TestCase):
                def test_x(self): pass
            if n:
                A.test_extra = lambda self: None
        """))
        r = runner("--start", str(self.start))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the 1 discovered", r.stderr)


if __name__ == "__main__":
    unittest.main()
