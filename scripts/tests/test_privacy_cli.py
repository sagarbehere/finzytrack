"""CLI meta-tests for the privacy scan (INV-23; S0033, seeded-defect run 1 E01, E02).

The rule tests in `test_privacy_scan.py` call `scan_text` directly, so a scan whose command line always exits 0, or
whose `--staged` mode drops a file type, passed them. Here the script runs as the hooks run it, in a scratch git
repository, and each mode must exit 1 on a planted finding and 0 without one. (The `--message` and `--tag-message`
modes are run the same way in `test_push_gate.py`, class `Messages`.) These tests guard behaviour that already
worked (`@regression-guard`): the run-1 mutants (exit 0 always, `--staged` without `.md`) fail them.

The planted address is assembled at runtime so this file stays clean for the scanner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "privacy_scan.py"
FINDING = "contact: " + "someone.real" + "@" + "gmail.com\n"
CLEAN = "contact: the owner, by the issue tracker\n"


class PrivacyScanExitCodes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "gitconfig").write_text("")
        self.repo = base / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(base / "gitconfig"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com", "FT_PRIVACY_DENYLIST": str(base / "no-denylist")}
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS"):
            self.env.pop(k, None)
        self.git("init", "-q", "-b", "next")
        self.write("README.md", "hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def write(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def scan(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args, "--quiet"], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def assertExit(self, expected: int, *args: str) -> None:
        r = self.scan(*args)
        self.assertEqual(r.returncode, expected, f"{args}: {r.stderr[-600:]}")

    def test_staged_finds_a_finding_in_markdown_and_code(self) -> None:
        for name in ("docs/notes.md", "app.py"):
            with self.subTest(name=name):
                self.write(name, FINDING)
                self.git("add", name)
                self.assertExit(1, "--staged")
                self.git("rm", "-q", "--cached", name)
                (self.repo / name).unlink()

    def test_staged_reads_the_index_not_the_working_tree(self) -> None:
        self.write("notes.md", FINDING)
        self.git("add", "notes.md")
        self.write("notes.md", CLEAN)
        self.assertExit(1, "--staged")

    def test_all_range_and_paths_find_a_committed_finding(self) -> None:
        self.write("design/notes.md", FINDING)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "notes")
        tip = self.git("rev-parse", "HEAD").strip()
        first = self.git("rev-parse", "HEAD~1").strip()
        self.assertExit(1, "--all")
        self.assertExit(1, "--range", f"{first}..{tip}")
        self.assertExit(1, "design/notes.md")

    def test_every_mode_passes_a_clean_change(self) -> None:
        self.write("design/notes.md", CLEAN)
        self.git("add", "-A")
        self.assertExit(0, "--staged")
        self.git("commit", "-q", "-m", "notes")
        tip = self.git("rev-parse", "HEAD").strip()
        first = self.git("rev-parse", "HEAD~1").strip()
        self.assertExit(0, "--all")
        self.assertExit(0, "--range", f"{first}..{tip}")
        self.assertExit(0, "design/notes.md")


if __name__ == "__main__":
    unittest.main()
