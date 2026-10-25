"""Meta-tests for the self-mutation runner (ADR-0057 adjustment 2; INV-24 for the copy).

Expected outcomes come from ADR-0057's rule text: a mutant the tests catch is killed, one they miss
survives (exit 1); a failing baseline or a mutant whose find text is not exactly once in its file is an
error (exit 2), never a kill or a survivor; a timeout counts as killed; the run works on a copy of the
working tree (tracked plus untracked-not-ignored files) and never writes into the checkout.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mutants  # noqa: E402

CHECK = "def accepts(x):\n    return x > 0\n"
# The suite asks both "does it fire" (positive) and "does it fire on what it must not" (negative).
TESTS = (
    "import sys\n"
    "from check import accepts\n"
    "sys.exit(0 if accepts(1) and not accepts(0) else 1)\n"
)
# Only the positive case: a loosening mutant survives this suite.
WEAK_TESTS = "import sys\nfrom check import accepts\nsys.exit(0 if accepts(1) else 1)\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(root: Path, files: dict[str, str], *, commit: bool = True) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base", "--no-verify")
    return repo


def tree_bytes(repo: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


def plan_file(root: Path, body: str) -> Path:
    p = root / "mutants.toml"
    p.write_text(body)
    return p


def run_main(repo: Path, plan: Path, *extra: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mutants.main([str(plan), "--repo", str(repo), *extra])
    return code, out.getvalue() + err.getvalue()


LOOSEN = """
cmd = "python3 test_check.py"
[[mutant]]
id = "loosen"
file = "check.py"
find = "return x > 0"
replace = "return True"
"""


class Outcomes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mutant_the_tests_catch_is_killed(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"loosen\s+killed")

    def test_mutant_the_tests_miss_survives_and_fails_the_run(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": WEAK_TESTS})
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"loosen\s+survived")

    def test_failing_baseline_is_an_error_and_kills_nothing(self) -> None:
        # Must not pass: a command that always fails would otherwise "kill" every mutant.
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": "raise SystemExit(1)\n"})
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 2, out)
        self.assertNotIn("killed", out)
        self.assertIn("baseline", out)

    def test_find_text_absent_is_invalid(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        plan = plan_file(self.root, LOOSEN.replace("return x > 0", "return x >= 0"))
        code, out = run_main(repo, plan)
        self.assertEqual(code, 2, out)
        self.assertNotIn("killed", out)
        self.assertNotIn("survived", out)

    def test_find_text_twice_is_invalid(self) -> None:
        twice = CHECK + "\ndef also(x):\n    return x > 0\n"
        repo = make_repo(self.root, {"check.py": twice, "test_check.py": TESTS})
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 2, out)
        self.assertNotIn("killed", out)
        self.assertNotIn("survived", out)

    def test_mutants_are_independent(self) -> None:
        # The first mutant breaks the suite; the second is a no-op on its own. If the second saw the
        # first's edit it would be reported killed.
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        plan = plan_file(self.root, LOOSEN + """
[[mutant]]
id = "harmless"
file = "check.py"
find = "def accepts(x):"
replace = "def accepts(x):  # comment only"
""")
        code, out = run_main(repo, plan)
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"loosen\s+killed")
        self.assertRegex(out, r"harmless\s+survived")

    def test_timeout_counts_as_killed(self) -> None:
        check = "LOOP = False\ndef accepts(x):\n    while LOOP:\n        pass\n    return x > 0\n"
        repo = make_repo(self.root, {"check.py": check, "test_check.py": TESTS})
        plan = plan_file(self.root, """
cmd = "python3 test_check.py"
timeout = 2
[[mutant]]
id = "hang"
file = "check.py"
find = "LOOP = False"
replace = "LOOP = True"
""")
        code, out = run_main(repo, plan)
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"hang\s+killed \(timeout\)")


class Copy(unittest.TestCase):
    """INV-24: the run happens on a copy; the checkout is never written."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_uncommitted_and_untracked_work_is_in_the_copy(self) -> None:
        repo = make_repo(self.root, {"README": "x\n"})
        (repo / "check.py").write_text(CHECK)  # untracked, not ignored
        (repo / "test_check.py").write_text(TESTS)
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 0, out)

    def test_ignored_files_are_not_copied(self) -> None:
        repo = make_repo(self.root, {
            ".gitignore": "secret.txt\n",
            "check.py": CHECK,
            "test_check.py": "import os, sys\nfrom check import accepts\n"
                             "sys.exit(0 if not os.path.exists('secret.txt') and accepts(1) and not accepts(0) else 1)\n",
        })
        (repo / "secret.txt").write_text("must not be copied\n")
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 0, out)  # the baseline passes only if secret.txt is absent from the copy

    def test_checkout_is_byte_identical_after_a_writing_command(self) -> None:
        # The command rewrites a tracked file and adds one. Each run also needs a fresh copy: if the
        # mutant ran in the baseline's copy, check.py would already be "broken" and the mutant invalid.
        writer = (
            "import pathlib, sys\n"
            "from check import accepts\n"
            "ok = accepts(1) and not accepts(0)\n"
            "pathlib.Path('check.py').write_text('broken')\n"
            "pathlib.Path('new.txt').write_text('x')\n"
            "sys.exit(0 if ok else 1)\n"
        )
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": writer})
        (repo / "wip.py").write_text("uncommitted\n")
        before = tree_bytes(repo)
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"loosen\s+killed")
        self.assertEqual(tree_bytes(repo), before)

    def test_mutant_outside_the_copy_is_refused(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        outside = self.root / "outside.py"
        outside.write_text(CHECK)
        for target in (str(outside), "../outside.py"):
            with self.subTest(target=target):
                plan = plan_file(self.root, LOOSEN.replace('file = "check.py"', f'file = "{target}"'))
                code, out = run_main(repo, plan)
                self.assertEqual(code, 2, out)
                self.assertEqual(outside.read_text(), CHECK)

    def test_tracked_symlink_to_outside_is_refused(self) -> None:
        # A symlink is copied as a symlink, so writing the mutant through it would edit the real target.
        outside = self.root / "outside.py"
        outside.write_text(CHECK)
        repo = make_repo(self.root, {"test_check.py": TESTS}, commit=False)
        (repo / "check.py").symlink_to(outside)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base", "--no-verify")
        code, out = run_main(repo, plan_file(self.root, LOOSEN))
        self.assertEqual(code, 2, out)
        self.assertIn("outside the copy", out)
        self.assertEqual(outside.read_text(), CHECK)

    def test_work_folder_holds_no_copies_afterwards(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        work = self.root / "work"
        code, out = run_main(repo, plan_file(self.root, LOOSEN), "--work", str(work))
        self.assertEqual(code, 0, out)
        self.assertEqual(list(work.iterdir()), [])

    def test_work_folder_inside_the_checkout_is_refused(self) -> None:
        repo = make_repo(self.root, {"check.py": CHECK, "test_check.py": TESTS})
        code, out = run_main(repo, plan_file(self.root, LOOSEN), "--work", str(repo / "w"))
        self.assertEqual(code, 2, out)
        self.assertFalse((repo / "w").exists())


if __name__ == "__main__":
    unittest.main()
