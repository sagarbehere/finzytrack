"""Meta-tests for the agent session hooks' git-hooks check (S0031, the M0 gate red team L2): the session-start hook
warns and the Stop hook fails when this clone does not run `.githooks`. Scratch repositories only."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parents[2] / ".claude/hooks"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"hook_{name}", HOOKS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HooksPathCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        empty = Path(self.tmp.name) / "gitconfig"
        empty.write_text("")
        self.env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        self.env.start()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.session, self.stop = load("session_start"), load("stop_checks")

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def set_hooks_path(self, value: str | None) -> None:
        if value is None:
            subprocess.run(["git", "-C", str(self.repo), "config", "--unset", "core.hooksPath"])
        else:
            subprocess.run(["git", "-C", str(self.repo), "config", "core.hooksPath", value], check=True)

    def test_both_hooks_see_a_missing_or_other_hooks_path(self) -> None:
        for value in (None, ".git/hooks", "/dev/null", ".githooks/", "githooks"):
            with self.subTest(value=value):
                self.set_hooks_path(value)
                self.assertIsNotNone(self.session.hooks_path_problem(self.repo))
                self.assertIsNotNone(self.stop.hooks_path_problem(self.repo))
        self.set_hooks_path(".githooks")
        self.assertIsNone(self.session.hooks_path_problem(self.repo))
        self.assertIsNone(self.stop.hooks_path_problem(self.repo))

    def test_session_start_warns_and_never_fails(self) -> None:
        out = io.StringIO()
        with mock.patch.object(self.session, "REPO", self.repo), contextlib.redirect_stdout(out):
            self.assertEqual(self.session.main(), 0)
        self.assertIn("WARNING: core.hooksPath", out.getvalue())
        self.assertIn("design/README.md", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(self.session.subprocess, "run", side_effect=OSError("no git")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(self.session.main(), 0)
        self.assertIn("WARNING", out.getvalue())
        self.set_hooks_path(".githooks")
        out = io.StringIO()
        with mock.patch.object(self.session, "REPO", self.repo), contextlib.redirect_stdout(out):
            self.assertEqual(self.session.main(), 0)
        self.assertNotIn("WARNING", out.getvalue())

    def test_stop_hook_fails_once_without_the_hooks(self) -> None:
        def run(event: dict) -> tuple[int, str]:
            err = io.StringIO()
            with mock.patch.object(self.stop, "REPO", self.repo), mock.patch.object(self.stop, "CHECKS", []), \
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), contextlib.redirect_stderr(err):
                return self.stop.main(), err.getvalue()

        code, err = run({})
        self.assertEqual(code, 2)
        self.assertIn("git hooks: core.hooksPath", err)
        self.assertEqual(run({"stop_hook_active": True})[0], 0)  # the second time it lets the session stop
        self.set_hooks_path(".githooks")
        self.assertEqual(run({}), (0, ""))


if __name__ == "__main__":
    unittest.main()
