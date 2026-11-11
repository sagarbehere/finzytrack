"""Meta-tests for the capability-map check (`scripts/check_capabilities.py`), for BL-0039: a cut or deferred row
must cite the ADR that decided it, and the ADR must exist. PROCESS.md §7: cut and deferred rows stay in the map
"with their ADR". S0105: every path a row's `code` and `tests` name exists (BL-0184), and a `built` row's tagged test
is a real test, not a comment, a helper, a fixme or a skipped test (BL-0077). Each passing case sits beside the case
that must not pass (EP-29)."""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_capabilities as cc  # noqa: E402

ADRS = {"ADR-0030"}


def row(status: str, adrs: list[str], **extra: str) -> dict:
    base = {"id": "CAP-X-Y", "area": "Test", "capability": "a fictional row (a real ID here would count as its tagged test)", "v04": "cut", "status": status,
            "release_blocking": "no", "adrs": adrs}
    return {**base, **extra}


class CutAndDeferredRows(unittest.TestCase):
    def test_a_cut_or_deferred_row_citing_an_existing_adr_passes(self) -> None:
        for status in ("cut", "deferred"):
            with self.subTest(status=status):
                self.assertEqual(cc.check([row(status, ["ADR-0030"])], ADRS, None)[0], [])

    def test_rows_that_must_not_pass(self) -> None:
        cases = {
            "cut without an ADR": row("cut", []),
            "deferred without an ADR": row("deferred", []),
            "cut without the field": {k: v for k, v in row("cut", []).items() if k != "adrs"},
            "cut citing a missing ADR": row("cut", ["ADR-9999"]),
        }
        for name, r in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(cc.check([r], ADRS, None)[0], [])

    def test_a_planned_row_needs_no_adr(self) -> None:
        self.assertEqual(cc.check([row("planned", [], milestone="M8", release_blocking="yes")], ADRS, None)[0], [])

    def test_the_committed_map_passes(self) -> None:
        caps = tomllib.loads((cc.ROOT / "design/capabilities.toml").read_text())["capability"]
        adr_ids = {p.name[:8] for p in (cc.ROOT / "design/decisions").glob("ADR-*.md")}
        self.assertEqual(cc.check(caps, adr_ids, None)[0], [])


class PathsExist(unittest.TestCase):
    """BL-0184 (S0070 verifier r1 F4): a row naming deleted files passed with exit 0."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        (self.root / "engine/src").mkdir(parents=True)
        (self.root / "engine/src/a.py").write_text("")
        (self.root / "engine/tests").mkdir(parents=True)
        (self.root / "engine/tests/test_a.py").write_text("")
        (self.root.parent / "outside.py").write_text("")  # an existing file outside the repository

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def errors(self, **fields: object) -> list[str]:
        r = {**row("planned", [], milestone="M8", release_blocking="yes"), **fields}
        return cc.check([r], ADRS, None, root=self.root, tags={})[0]

    def test_existing_files_and_folders_pass(self) -> None:
        self.assertEqual(self.errors(code=["engine/src/a.py", "engine/src/"], tests=["engine/tests/test_a.py",
                                                                                     "engine/tests/"]), [])

    def test_paths_that_must_not_pass(self) -> None:
        cases = {
            "a deleted code file": {"code": ["engine/src/gone.py"]},
            "a deleted test file": {"tests": ["engine/tests/test_gone.py"]},
            "a missing folder": {"tests": ["engine/tests/gone/"]},
            "a folder written without its slash": {"tests": ["engine/tests"]},
            "a file written as a folder": {"code": ["engine/src/a.py/"]},
            "an absolute path": {"code": [str(self.root / "engine/src/a.py")]},
            "a path leaving the repository": {"code": ["../outside.py"]},
            "a path through ..": {"code": ["engine/../engine/src/a.py"]},  # exists, but is not the row's own path
            "an empty path": {"code": [""]},
            "not a list": {"code": "engine/src/a.py"},
            "not a string": {"tests": [3]},
        }
        for name, fields in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.errors(**fields), [], name)

    def test_paths_are_compared_with_gits_tracked_files(self) -> None:  # S0105 round 1, L2
        tracked = {"engine/src/a.py", "engine/tests/test_a.py"}
        r = {**row("planned", [], milestone="M8", release_blocking="yes")}
        self.assertEqual(cc.path_errors({**r, "code": ["engine/src/a.py", "engine/src/"]}, self.root, tracked), [])
        (self.root / "engine/src/ignored.py").write_text("")  # on disk, not tracked (an ignored or new file)
        for p in ("Engine/src/a.py", "engine/src/A.py", "engine/src/ignored.py", "Engine/src/", "engine/srcx/"):
            with self.subTest(path=p):
                self.assertNotEqual(cc.path_errors({**r, "code": [p]}, self.root, tracked), [], p)

    def test_every_row_of_the_committed_map_names_existing_paths(self) -> None:
        caps = tomllib.loads((cc.ROOT / "design/capabilities.toml").read_text())["capability"]
        tracked = cc.tracked_files(cc.ROOT)
        self.assertIsNotNone(tracked)
        self.assertGreater(sum(len(c.get("code", [])) + len(c.get("tests", [])) for c in caps), 50)
        self.assertEqual([e for c in caps for e in cc.path_errors(c, cc.ROOT, tracked)], [])


PY_HEAD = "import pytest\nimport unittest\n\n"
AT = "@"  # kept apart from the mark, so this file holds no real tag


def mark(cap: str = "CAP-X-Y") -> str:
    return f'pytest.mark.cap("{cap}")'


class TaggedTestsAreRealTests(unittest.TestCase):
    """BL-0077 (S0031, M0 gate red team M7): a git grep for the ID in any test path accepted a comment or a fixme."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def tagged(self, rel: str, text: str) -> list[str]:
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(text)
        return cc.real_tags(self.root, [rel]).get("CAP-X-Y", [])

    def built(self, rel: str, text: str) -> list[str]:
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(text)
        r = row("built", [], milestone="M2", release_blocking="yes", v04="keep")
        return cc.check([r], ADRS, None, root=self.root, tags=cc.real_tags(self.root, [rel]))[0]

    def test_real_tags_pass(self) -> None:
        cases = {
            "a decorated test function": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\ndef test_x():\n    pass\n"),
            "a decorated test class": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                       + "\nclass TestX:\n    def test_y(self):\n        pass\n"),
            "a decorated method": ("backend/tests/test_a.py", PY_HEAD + "class TestX:\n    " + AT + mark()
                                   + "\n    def test_y(self):\n        pass\n"),
            "a module pytestmark": ("backend/tests/test_a.py", PY_HEAD + f"pytestmark = {mark()}\n\n"
                                    "def test_x():\n    pass\n"),
            "a pytestmark list": ("backend/tests/test_a.py", PY_HEAD + f'pytestmark = [pytest.mark.inv("INV-01"), {mark()}]\n\n'
                                  "def test_x():\n    pass\n"),
            "a unittest method in scripts": ("scripts/tests/test_a.py", PY_HEAD + "class A(unittest.TestCase):\n    " + AT
                                             + mark() + "\n    def test_y(self):\n        pass\n"),
            "a parametrize over a name": ("backend/tests/test_a.py", PY_HEAD + "CASES = [1, 2]\n\n\n" + AT + mark() + "\n"
                                          + AT + 'pytest.mark.parametrize("n", CASES)\ndef test_x(n):\n    pass\n'),
            "a vitest describe": ("frontend/tests/unit/a.test.ts", "describe('[CAP-X-Y] the thing', () => {\n"
                                  "  it('works', () => {})\n})\n"),
            "a vitest test, double quotes": ("frontend/tests/unit/a.test.ts", 'test("[CAP-X-Y][INV-01] works", () => {})\n'),
            "an engine test (BL-0252, owner 2026-11-11)": ("engine/tests/ledger/test_a.py", PY_HEAD + AT + mark()
                                                           + "\ndef test_x():\n    pass\n"),
            "a unittest class through a base of the file": ("scripts/tests/test_a.py", PY_HEAD + "class Base(unittest.TestCase):\n"
                                                             "    pass\n\n\nclass A(Base):\n    " + AT + mark()
                                                             + "\n    def test_y(self):\n        pass\n"),
            "a later, tagged definition": ("backend/tests/test_a.py", PY_HEAD + "def test_x():\n    pass\n\n\n" + AT
                                           + mark() + "\ndef test_x():\n    pass\n"),
            "a tagged describe with a test in a nested describe": ("frontend/tests/unit/a.test.ts",
                                                                   "describe('[CAP-X-Y] x', () => {\n  describe('y', () => {\n"
                                                                   "    it('a', () => {})\n  })\n})\n"),
            "a Playwright describe": ("frontend/e2e/a.spec.ts", "test.describe(`[CAP-X-Y] flow`, () => {\n"
                                      "  test('a', async () => {})\n})\n"),
        }
        for name, (rel, text) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.tagged(rel, text), [rel], name)
                self.assertEqual(self.built(rel, text), [], name)

    def test_tags_that_must_not_pass(self) -> None:
        fixme = 'pytest.mark.fixme(id="BL-0001", due="M2", reason="r")'
        cases = {
            "a comment": ("backend/tests/test_a.py", PY_HEAD + "# CAP-X-Y\ndef test_x():\n    pass\n"),
            "an engine comment": ("engine/tests/test_a.py", PY_HEAD + "# CAP-X-Y\ndef test_x():\n    pass\n"),
            "a skipped engine test": ("engine/tests/test_a.py", PY_HEAD + AT + mark() + "\n" + AT
                                      + 'pytest.mark.skip(reason="later")\ndef test_x():\n    pass\n'),
            "an engine file outside its tests": ("engine/src/test_a.py", PY_HEAD + AT + mark() + "\ndef test_x():\n    pass\n"),
            "a docstring": ("backend/tests/test_a.py", PY_HEAD + 'def test_x():\n    """CAP-X-Y"""\n'),
            "a string": ("backend/tests/test_a.py", PY_HEAD + 'def test_x():\n    assert "CAP-X-Y"\n'),
            "a mark kept in a variable": ("backend/tests/test_a.py", PY_HEAD + f"m = {mark()}\n\ndef test_x():\n    pass\n"),
            "a tag on a helper": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\ndef helper():\n    pass\n"),
            "a tag on a class with no test": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                              + "\nclass TestX:\n    def helper(self):\n        pass\n"),
            "a tag on a class pytest does not collect": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                                         + "\nclass Checks:\n    def test_y(self):\n        pass\n"),
            "a pytestmark with no test": ("backend/tests/test_a.py", PY_HEAD + f"pytestmark = {mark()}\n"),
            "another ID": ("backend/tests/test_a.py", PY_HEAD + AT + mark("CAP-X-YZ") + "\ndef test_x():\n    pass\n"),
            "a keyword argument": ("backend/tests/test_a.py", PY_HEAD + AT + 'pytest.mark.cap(id="CAP-X-Y")'
                                   + "\ndef test_x():\n    pass\n"),
            "a fixme test": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\n" + AT + fixme
                             + "\ndef test_x():\n    pass\n"),
            "a fixme elsewhere in the file": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                              + "\ndef test_x():\n    pass\n\n" + AT + fixme
                                              + "\ndef test_y():\n    pass\n"),
            "a skipped test": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\n" + AT
                               + 'pytest.mark.skip(reason="later")\ndef test_x():\n    pass\n'),
            "an xfail test": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\n" + AT
                              + "pytest.mark.xfail\ndef test_x():\n    pass\n"),
            "a file pytest does not collect": ("backend/tests/helpers.py", PY_HEAD + AT + mark()
                                               + "\ndef test_x():\n    pass\n"),
            "a test file outside the test folders": ("design/test_a.py", PY_HEAD + AT + mark()
                                                     + "\ndef test_x():\n    pass\n"),
            "a file that does not parse": ("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\ndef test_x(:\n"),
            "a TypeScript line comment": ("frontend/tests/unit/a.test.ts", "// describe('[CAP-X-Y] x', () => {})\n"
                                          "test('a', () => {})\n"),
            "a TypeScript block comment": ("frontend/tests/unit/a.test.ts", "/* describe('[CAP-X-Y] x', () => {}) */\n"
                                           "test('a', () => {})\n"),
            "a TypeScript string": ("frontend/tests/unit/a.test.ts", "const s = \"describe('[CAP-X-Y] x'\"\n"
                                    "test('a', () => {})\n"),
            "the ID without brackets": ("frontend/tests/unit/a.test.ts", "describe('CAP-X-Y x', () => {})\n"),
            "a test titled with the ID without brackets": ("frontend/tests/unit/a.test.ts", "test('CAP-X-Y x', () => {})\n"),
            "a test call inside a string": ("frontend/tests/unit/a.test.ts", "const s = \"test('[CAP-X-Y] x', () => {})\"\n"
                                            "test('a', () => {})\n"),
            "a skipped describe": ("frontend/tests/unit/a.test.ts", "describe.skip('[CAP-X-Y] x', () => {\n"
                                   "  it('a', () => {})\n})\n"),
            "a todo test": ("frontend/tests/unit/a.test.ts", "test.todo('[CAP-X-Y] x')\n"),
            "a fixme test (vitest)": ("frontend/tests/unit/a.test.ts",
                                      "fixme({ id: 'BL-0001', due: 'M2', reason: 'r' }, '[CAP-X-Y] x', () => {})\n"),
            "a helper call with the title": ("frontend/tests/unit/a.test.ts", "label('[CAP-X-Y] x')\ntest('a', () => {})\n"),
            "a TypeScript file that is not a test": ("frontend/src/a.ts", "describe('[CAP-X-Y] x', () => {})\n"),
            # S0105 round 1, F3: the switched-off rules apply to e2e files too
            "a skipped Playwright test under a tagged describe": ("frontend/e2e/a.spec.ts", "test.describe('[CAP-X-Y] flow', "
                                                                  "() => {\n  test('b', async () => {})\n  test.skip('a', async () => {})\n})\n"),
            # round 1, L1: the cheap shapes
            "an empty tagged describe": ("frontend/tests/unit/a.test.ts", "describe('[CAP-X-Y] flow', () => {})\n"
                                         "test('other', () => {})\n"),
            "a tagged describe whose only call is in a string": ("frontend/tests/unit/a.test.ts",
                                                                 "describe('[CAP-X-Y] flow', () => {\n  const s = 'it('\n})\n"
                                                                 "test('other', () => {})\n"),
            "a tagged describe in a function": ("frontend/tests/unit/a.test.ts", "function never() {\n  describe("
                                                "'[CAP-X-Y] flow', () => { it('a', () => {}) })\n}\ntest('other', () => {})\n"),
            "a tagged test in a function": ("frontend/tests/unit/a.test.ts", "const later = () => {\n  test('[CAP-X-Y] a', "
                                            "() => {})\n}\ntest('other', () => {})\n"),
            "a pytest-style class under scripts (unittest runs it not)": ("scripts/tests/test_a.py", PY_HEAD
                                                                          + "class TestX:\n    " + AT + mark()
                                                                          + "\n    def test_y(self):\n        pass\n"),
            "a module function under scripts": ("scripts/tests/test_a.py", PY_HEAD + AT + mark() + "\ndef test_x():\n    pass\n"),
            "a tagged test shadowed by a later one": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                                      + "\ndef test_x():\n    pass\n\n\ndef test_x():\n    pass\n"),
            "a tagged method shadowed by a later one": ("backend/tests/test_a.py", PY_HEAD + "class TestX:\n    " + AT
                                                        + mark() + "\n    def test_y(self):\n        pass\n\n"
                                                        "    def test_y(self):\n        pass\n"),
            "a tagged class shadowed by a later one": ("backend/tests/test_a.py", PY_HEAD + AT + mark()
                                                       + "\nclass TestX:\n    def test_y(self):\n        pass\n\n\n"
                                                       "class TestX:\n    def test_y(self):\n        pass\n"),
        }
        for name, (rel, text) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.tagged(rel, text), [], name)
                self.assertNotEqual(self.built(rel, text), [], name)
                (self.root / rel).unlink()

    def test_the_folders_searched(self) -> None:
        # the new rule refuses more than the git grep did, never less; engine/ added with the owner's approval
        # (BL-0252, 2026-11-11: "Count engine tests (Recommended)")
        self.assertEqual(self.tagged("backend/tests/test_a.py", PY_HEAD + AT + mark() + "\ndef test_x():\n    pass\n"),
                         ["backend/tests/test_a.py"])
        self.assertEqual(cc.TEST_DIRS, ("backend", "engine", "frontend", "shell", "scripts/tests"))

    def test_the_engine_registers_the_cap_mark(self) -> None:  # BL-0252: --strict-markers refuses an unknown mark
        cfg = tomllib.loads((cc.ROOT / "engine/pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
        self.assertTrue(any(m.startswith("cap(id):") for m in cfg["markers"]), cfg["markers"])
        self.assertIn("--strict-markers", cfg["addopts"])

    def test_the_real_tags_are_found_in_the_repository(self) -> None:
        tags = cc.real_tags(cc.ROOT, cc.candidate_files(cc.ROOT))
        self.assertIn("backend/tests/workspaces/test_manifest.py", tags.get("CAP-WS-PORTABLE", []))
        self.assertIn("backend/tests/platform/test_recover_all.py", tags.get("CAP-WS-RECOVER-FIRST", []))


if __name__ == "__main__":
    unittest.main()
