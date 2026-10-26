"""The ledger engine's gates (S0035, BL-0084): staged engine files are formatted and linted at commit, failing
closed without the engine's ruff; the push and the manual CI run the engine's own checks.

The pre-commit cases run the real hook in a scratch git repository under a temporary folder, with the user's git
configuration switched off. The repo-wide checks the hook runs first (staged-file guard, privacy scan, generated
headers) are stand-ins that pass there, since they are tested on their own; `ruff` is a stand-in script that records
its arguments, so the cases test the hook's engine block and nothing else.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASSING = "import sys\nsys.exit(0)\n"
# A stand-in ruff: appends its arguments to ruff.log beside it; exits 1 for the subcommand named in FAIL_SUBCOMMAND.
FAKE_RUFF = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
with open(Path(__file__).with_name("ruff.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(1 if os.environ.get("FAIL_SUBCOMMAND") == sys.argv[1] else 0)
"""


class PreCommitEngineBlock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "gitconfig").write_text("")
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(base / "gitconfig"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "FAIL_SUBCOMMAND"):
            self.env.pop(k, None)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "next")
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        shutil.copy2(REPO / ".githooks/pre-commit", hooks / "pre-commit")
        for name in ("check_staged.py", "privacy_scan.py", "check_generated.py"):
            self.write(f"scripts/{name}", PASSING)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def write(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def fake_ruff(self, package: str) -> Path:
        ruff = self.repo / package / ".venv/bin/ruff"
        ruff.parent.mkdir(parents=True, exist_ok=True)
        ruff.write_text(FAKE_RUFF)
        ruff.chmod(ruff.stat().st_mode | stat.S_IXUSR)
        return ruff.with_name("ruff.log")

    def hook(self, *staged: str, fail: str = "") -> subprocess.CompletedProcess[str]:
        for rel in staged:
            self.write(rel, "x = 1\n")
        self.git("add", "--", *staged)
        env = {**self.env, "FAIL_SUBCOMMAND": fail} if fail else self.env
        return subprocess.run(["bash", ".githooks/pre-commit"], cwd=self.repo, env=env, capture_output=True,
                              text=True, check=False)

    def test_a_staged_engine_file_without_the_engine_ruff_fails_closed(self) -> None:
        r = self.hook("engine/src/ledger_engine/x.py")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("engine/.venv/bin/ruff missing", r.stderr)

    def test_the_backend_ruff_does_not_stand_in_for_the_engine_ruff(self) -> None:
        log = self.fake_ruff("backend")
        r = self.hook("engine/src/ledger_engine/x.py", "backend/src/app/y.py")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("engine/.venv/bin/ruff missing", r.stderr)
        self.assertNotIn("engine/", log.read_text())

    def test_staged_engine_files_are_formatted_and_linted_with_the_engine_config(self) -> None:
        log = self.fake_ruff("engine")
        r = self.hook("engine/src/ledger_engine/x.py", "engine/tests/test_y.py", "engine/uv.lock")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = log.read_text().splitlines()
        files = "engine/src/ledger_engine/x.py engine/tests/test_y.py"
        self.assertEqual(calls, [f"format --check --config engine/ruff.toml {files}",
                                 f"check --config engine/ruff.toml {files}"])

    def test_a_format_or_lint_failure_fails_the_commit(self) -> None:
        for sub, label in (("format", "ruff format (engine)"), ("check", "ruff lint (engine)")):
            with self.subTest(sub):
                self.fake_ruff("engine")
                r = self.hook("engine/src/ledger_engine/x.py", fail=sub)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(f"pre-commit: FAILED: {label}", r.stderr)

    def test_no_staged_engine_python_needs_no_engine_ruff(self) -> None:
        """`@regression-guard`: passes before the engine block too; it keeps the block from reaching files that are
        not engine Python (a lockfile, a doc, a `.py` under another folder named engine)."""
        r = self.hook("engine/uv.lock", "engine/README.md", "docs/engine/x.py")
        self.assertEqual(r.returncode, 0, r.stderr)


class EngineChecksAreWired(unittest.TestCase):
    """The push and the manual CI run the engine's checks (the hook's bytes are pinned in test_hook_pins)."""

    def test_pre_push_runs_the_engine_checks_when_the_engine_exists(self) -> None:
        text = (REPO / ".githooks/pre-push").read_text()
        block = text[text.index("if [[ -f engine/pyproject.toml ]]; then\n"):]
        block = block[:block.index("\nfi\n")]
        for line in ('run "ruff format (engine)" bash -c "cd engine && .venv/bin/ruff format --check --config '
                     'ruff.toml ."',
                     'run "ruff lint (engine)" bash -c "cd engine && .venv/bin/ruff check --config ruff.toml ."',
                     'run "pyright (engine)" bash -c "cd engine && .venv/bin/pyright"',
                     'run "engine tests" bash -c "cd engine && .venv/bin/python -m pytest -q -x"'):
            self.assertIn("\n  " + line + "\n", block + "\n")
        # before the final checks: the time report and the tree re-check stay last
        self.assertLess(text.index('run "engine tests"'), text.index('run "check times"'))

    def test_ci_builds_the_engine_environment_and_runs_an_engine_job(self) -> None:
        ci = (REPO / ".github/workflows/ci.yml").read_text()
        jobs: dict[str, str] = {}
        names = [line[2:-1] for line in ci.splitlines() if line.startswith("  ") and not line.startswith("   ")
                 and line.endswith(":")]
        self.assertIn("engine", names)
        for name in names:
            start = ci.index(f"\n  {name}:\n")
            rest = ci[start + 1:]
            nxt = [rest.index(f"\n  {n}:\n") for n in names if f"\n  {n}:\n" in rest and n != name]
            jobs[name] = rest[:min(nxt) + 1] if nxt else rest
        repo_checks = jobs["repo-checks"]
        self.assertLess(repo_checks.index("run: uv sync --locked --directory engine\n"),
                        repo_checks.index("run: python3 scripts/check_generated.py --all"))
        self.assertLess(repo_checks.index("run: uv sync --locked --directory engine\n"),
                        repo_checks.index("run: python3 scripts/check_licences.py"))
        backend = jobs["backend"]
        self.assertLess(backend.index("run: uv sync --locked --directory ../engine\n"),
                        backend.index("run: .venv/bin/pytest -q"))
        engine = jobs["engine"]
        self.assertIn("working-directory: engine\n", engine)
        self.assertIn("timeout-minutes:", engine)
        steps = ["run: uv sync --locked\n", "run: .venv/bin/ruff format --check --config ruff.toml .\n",
                 "run: .venv/bin/ruff check --config ruff.toml .\n", "run: .venv/bin/pyright\n",
                 "run: .venv/bin/python -m pytest -q\n"]
        at = [engine.index(s) for s in steps]
        self.assertEqual(at, sorted(at))
        self.assertNotIn("continue-on-error", engine)


if __name__ == "__main__":
    unittest.main()
