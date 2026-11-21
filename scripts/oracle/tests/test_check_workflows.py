"""The workflow files parse as GitHub reads them (S0140, BL-0292): `scripts/check_workflows.py`.

Runs in the oracle environment, which pins the YAML library the check uses. The must-not-pass cases: S0129's shape
(89a61f03: a step name holding `: ` in a plain scalar, which GitHub could not parse), files that are not YAML, and
YAML that is not a workflow at its top level; each next to the nearest shape that must pass.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

import check_workflows as cw  # noqa: E402

GOOD = """\
name: ci
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - name: {name}
        run: echo ok
"""
# S0129 round 1's step name, as 89a61f03 pushed it (unquoted; `: ` ends a plain scalar in block context)
S0129_NAME = "Scratch volumes (S0129 round 1: the app folder's identity on FAT32, exFAT and ReFS, as engine-windows)"


def problems(text: str | bytes) -> list[str]:
    return cw.problems_of(text.encode("utf-8") if isinstance(text, str) else text)


class WorkflowFiles(unittest.TestCase):
    def test_the_repositorys_workflows_pass(self) -> None:
        files = cw.workflow_files(REPO / ".github" / "workflows")
        self.assertGreaterEqual({p.name for p in files}, {"ci.yml", "nightly.yml", "packaged.yml"})
        for path in files:
            with self.subTest(path=path.name):
                self.assertEqual(problems(path.read_bytes()), [])

    def test_s0129s_colon_in_a_plain_step_name_is_refused(self) -> None:
        found = problems(GOOD.format(name=S0129_NAME))
        self.assertEqual(len(found), 1, found)
        self.assertIn("line 10", found[0])
        self.assertIn("mapping values are not allowed here", found[0])

    def test_the_same_name_quoted_or_in_a_block_scalar_passes(self) -> None:
        self.assertEqual(problems(GOOD.format(name='"' + S0129_NAME + '"')), [])
        self.assertEqual(problems(GOOD.format(name="'" + S0129_NAME.replace("'", "''") + "'")), [])
        self.assertEqual(problems(GOOD.format(name=">-\n          " + S0129_NAME)), [])
        self.assertEqual(problems(GOOD.format(name="tests")), [])

    def test_a_file_that_is_not_yaml_is_refused(self) -> None:
        cases = {
            "unclosed flow sequence": "on: [push\njobs: {}\n",
            "tab indentation": "on:\n\tpush:\njobs:\n  a:\n    runs-on: x\n",
            "prose": "This is a README, not a workflow: it says so.\nIt has: colons: everywhere\n",
            "unclosed quote": 'name: "ci\non: push\njobs:\n  a: {}\n',
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertNotEqual(problems(text), [])
        with self.subTest("not UTF-8"):
            found = problems(GOOD.format(name="t\xe9sts").encode("latin-1"))
            self.assertEqual(len(found), 1, found)
            self.assertIn("UTF-8", found[0])

    def test_yaml_that_is_not_one_workflow_is_refused(self) -> None:
        cases = {
            "empty": "",
            "comments only": "# nothing here\n",
            "a list at the top": "- on: push\n- jobs: {}\n",
            "a scalar at the top": "ci\n",
            "no on": "jobs:\n  a:\n    runs-on: x\n",
            "on with no value": "on:\njobs:\n  a:\n    runs-on: x\n",
            "no jobs": "on: push\n",
            "jobs empty": "on: push\njobs: {}\n",
            "jobs a list": "on: push\njobs:\n  - a\n",
            "two documents": GOOD.format(name="tests") + "---\n" + GOOD.format(name="tests"),
            "a duplicate key": GOOD.format(name="tests") + "jobs:\n  b:\n    runs-on: x\n",
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertNotEqual(problems(text), [])

    def test_main_names_the_file_and_exits_1_and_exits_0_on_good_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "ok.yml").write_text(GOOD.format(name="tests"), encoding="utf-8")
            (folder / "also-ok.yaml").write_text(GOOD.format(name="tests"), encoding="utf-8")
            (folder / "notes.txt").write_text("not a workflow: ignored\n", encoding="utf-8")
            out, code = self.run_main(folder)
            self.assertEqual(code, 0, out)
            (folder / "bad.yml").write_text(GOOD.format(name=S0129_NAME), encoding="utf-8")
            out, code = self.run_main(folder)
            self.assertEqual(code, 1)
            self.assertIn("bad.yml", out)
            self.assertIn("line 10", out)
            self.assertNotIn("ok.yml", out)
            (folder / "bad.yml").unlink()
            (folder / "bad.yaml").write_bytes(b"on: [push\n")
            out, code = self.run_main(folder)
            self.assertEqual(code, 1)
            self.assertIn("bad.yaml", out)

    def test_a_folder_without_workflow_files_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, code = self.run_main(Path(tmp))
            self.assertEqual(code, 1)
            self.assertIn("no workflow file", out)
            out, code = self.run_main(Path(tmp) / "missing")
            self.assertEqual(code, 1)

    def test_the_pre_push_hook_runs_the_check(self) -> None:
        hook = (REPO / ".githooks" / "pre-push").read_text(encoding="ascii")
        line = ('run "workflow files parse" "$uv_bin" run --project scripts/oracle --locked python '
                "scripts/check_workflows.py\n")
        self.assertEqual(hook.count("\n" + line), 1)
        self.assertLess(hook.index('uv_bin="$('), hook.index(line))

    def run_main(self, folder: Path) -> tuple[str, int]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cw.main(["--folder", str(folder)])
        return err.getvalue(), code


if __name__ == "__main__":
    unittest.main()
