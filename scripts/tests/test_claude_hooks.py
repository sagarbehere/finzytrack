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
import time
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



class StopHookCacheAndTimeLimits(unittest.TestCase):
    """S0034: the Stop hook does not re-prove a proven tree, stops a hung check, and never crashes (a crashed or
    harness-killed Stop hook lets the session stop green). Scratch repositories only."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "r"
        empty = base / "gitconfig"
        empty.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
        self.env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        self.env.start()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "core.hooksPath", ".githooks"], check=True)
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "a.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True)
        self.count = base / "count"
        self.stop = load("stop_checks")
        self.counting = [sys.executable, "-c", f"open({str(self.count)!r}, 'a').write('x')"]

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def runs(self) -> int:
        return len(self.count.read_text()) if self.count.exists() else 0

    def stop_hook(self, checks: list | None = None, event: dict | None = None) -> tuple[int, str]:
        err = io.StringIO()
        checks = [("counting", self.counting)] if checks is None else checks
        # the privacy scan of changed files is the real repository's script; these trees have none
        with mock.patch.object(self.stop, "REPO", self.repo), mock.patch.object(self.stop, "CHECKS", checks), \
                mock.patch.object(self.stop, "changed_files", return_value=[]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event or {}))), contextlib.redirect_stderr(err):
            return self.stop.main(), err.getvalue()

    def test_a_proven_tree_is_not_checked_again(self) -> None:
        self.assertEqual(self.stop_hook(), (0, ""))
        self.assertEqual(self.runs(), 1)
        self.assertEqual(self.stop_hook(), (0, ""))
        self.assertEqual(self.runs(), 1)  # same tree: green at once

    def test_any_change_to_the_tree_is_checked_again(self) -> None:
        changes = [
            ("a tracked file edited", lambda: (self.repo / "a.txt").write_text("b\n")),
            ("an untracked file added", lambda: (self.repo / "new.txt").write_text("n\n")),
            ("an untracked file edited", lambda: (self.repo / "new.txt").write_text("m\n")),
            ("a tracked file deleted", lambda: (self.repo / "a.txt").unlink()),
            ("an untracked file staged and a deletion staged (what is tracked changes)", lambda: subprocess.run(
                ["git", "-C", str(self.repo), "add", "-A"], check=True)),
        ]
        self.stop_hook()
        expected = 1
        for label, change in changes:
            with self.subTest(label):
                change()
                code, _ = self.stop_hook()
                self.assertEqual(code, 0)
                expected += 1
                self.assertEqual(self.runs(), expected)
        (self.repo / "ignored.txt").write_text("i\n")  # ignored files are not part of the tree
        self.stop_hook()
        self.assertEqual(self.runs(), expected)

    def test_staging_a_content_change_alone_is_not_a_new_tree(self) -> None:
        (self.repo / "a.txt").write_text("b\n")
        self.stop_hook()
        subprocess.run(["git", "-C", str(self.repo), "add", "a.txt"], check=True)
        self.stop_hook()
        self.assertEqual(self.runs(), 1)

    def test_the_python_version_is_part_of_the_key(self) -> None:
        self.stop_hook()
        with mock.patch.object(self.stop.sys, "version", "9.9.9 (other)"):
            self.stop_hook()
        self.assertEqual(self.runs(), 2)

    def test_the_real_index_and_the_tree_are_untouched(self) -> None:
        (self.repo / "a.txt").write_text("staged\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "a.txt"], check=True)
        (self.repo / "a.txt").write_text("unstaged\n")
        (self.repo / "u.txt").write_text("untracked\n")
        index = (self.repo / ".git/index").read_bytes()
        status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True,
                                text=True, check=True).stdout
        self.stop_hook()
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True,
                                        text=True, check=True).stdout, status)

    def test_a_failure_is_never_cached(self) -> None:
        failing = [("counting", self.counting), ("bad", [sys.executable, "-c", "raise SystemExit(1)"])]
        for _ in range(2):
            code, err = self.stop_hook(failing)
            self.assertEqual(code, 2)
            self.assertIn("bad", err)
        self.assertEqual(self.runs(), 2)
        self.assertEqual(self.stop_hook(failing, {"stop_hook_active": True})[0], 0)  # stop_hook_active as before
        self.assertEqual(self.runs(), 3)

    def test_a_tree_that_changed_during_the_checks_is_not_cached(self) -> None:
        side = self.repo / "side.txt"
        writer = [sys.executable, "-c", f"open({str(side)!r}, 'w').write('s')"]
        writes = [("counting", self.counting), ("writer", writer)]
        self.assertEqual(self.stop_hook(writes)[0], 0)
        side.unlink()  # the tree is back to what the checks started on, which they never saw whole
        self.stop_hook()
        self.assertEqual(self.runs(), 2)

    def test_a_file_that_stops_being_tracked_is_checked_again(self) -> None:  # S0034 verifier F3
        # `git grep` (check_capabilities.py) reads tracked files only: same content on disk, other result
        self.stop_hook()
        subprocess.run(["git", "-C", str(self.repo), "rm", "-q", "--cached", "a.txt"], check=True)
        self.stop_hook()
        self.assertEqual(self.runs(), 2)

    def test_a_tracked_file_that_matches_gitignore_is_part_of_the_key(self) -> None:  # S0034 verifier F5
        (self.repo / "ignored.txt").write_text("tracked anyway\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", "ignored.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "forced"], check=True)
        self.stop_hook()
        (self.repo / "ignored.txt").write_text("edited\n")
        self.stop_hook()
        self.assertEqual(self.runs(), 2)

    def test_the_backstop_wins_over_a_longer_history_limit(self) -> None:  # S0034 verifier F1
        times = self.repo / ".git/finzytrack/times-stop.json"
        times.parent.mkdir(parents=True)
        times.write_text(json.dumps({"sleepy": [100.0] * 5}))  # a 300 s limit from its history
        sleepy = [("sleepy", [sys.executable, "-c", "import time; time.sleep(60)"])]
        start = time.monotonic()
        with mock.patch.object(self.stop, "BACKSTOP_S", 2):
            code, err = self.stop_hook(sleepy)
        self.assertEqual(code, 2)
        self.assertLess(time.monotonic() - start, 20)
        self.assertIn("backstop", err)

    def test_the_key_check_after_the_run_stays_inside_the_backstop(self) -> None:  # S0034 verifier F4
        real, calls = self.stop.tree_key, []

        def slow(repo, deadline=None):
            calls.append(1)
            if len(calls) == 2:  # stands in for a slow `git add -A` after the checks
                time.sleep(max(0.0, deadline - time.monotonic()) + 0.5 if deadline else 3)
            return real(repo, deadline)

        start = time.monotonic()
        with mock.patch.object(self.stop, "BACKSTOP_S", 2), mock.patch.object(self.stop, "tree_key", slow):
            code, _ = self.stop_hook()
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - start, 2 + 1.5)
        self.assertEqual(self.stop_hook()[0], 0)
        self.assertEqual(self.runs(), 2)  # no key was stored past the backstop

    def test_an_interrupt_is_red_not_a_crash(self) -> None:  # S0034 verifier F5
        with mock.patch.object(self.stop, "run_checks", side_effect=KeyboardInterrupt()):
            code, err = self.stop_hook()
        self.assertEqual(code, 2)
        self.assertIn("the tree is red", err)

    def test_a_missing_hooks_path_still_fails_on_a_cached_tree(self) -> None:
        self.stop_hook()
        subprocess.run(["git", "-C", str(self.repo), "config", "--unset", "core.hooksPath"], check=True)
        code, err = self.stop_hook()
        self.assertEqual(code, 2)
        self.assertIn("core.hooksPath", err)

    def test_a_hung_check_is_stopped_and_red(self) -> None:
        timer = self.stop.load_timer()
        times = self.repo / ".git/finzytrack/times-stop.json"
        times.parent.mkdir(parents=True)
        times.write_text(json.dumps({"sleepy": [0.1] * 5}))
        sleepy = [("sleepy", [sys.executable, "-c", "import time; time.sleep(60)"])]
        with mock.patch.object(timer, "FLOOR_S", 1.0):
            code, err = self.stop_hook(sleepy)
        self.assertEqual(code, 2)
        self.assertIn("sleepy: took over", err)
        self.assertIn("hung?", err)
        self.assertEqual(self.stop_hook([("counting", self.counting)])[0], 0)
        self.assertEqual(self.runs(), 1)  # the hung run stored no key

    def test_the_backstop_stops_a_check_with_no_history(self) -> None:
        sleepy = [("sleepy", [sys.executable, "-c", "import time; time.sleep(60)"])]
        with mock.patch.object(self.stop, "BACKSTOP_S", 1):
            code, err = self.stop_hook(sleepy)
        self.assertEqual(code, 2)
        self.assertIn("backstop", err)

    def test_the_hook_never_crashes(self) -> None:
        cases = {
            "a check that cannot start": dict(checks=[("gone", [str(self.repo / "no-such-tool")])]),
            "the timer raises": dict(patch=("run_checks", RuntimeError("boom"))),
            "git is missing": dict(patch=("hooks_path_problem", OSError("no git"))),
            "the key cannot be computed": dict(patch=("tree_key", subprocess.TimeoutExpired("git", 1))),
        }
        for label, case in cases.items():
            with self.subTest(label):
                patches = [mock.patch.object(self.stop, case["patch"][0], side_effect=case["patch"][1])] \
                    if "patch" in case else []
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    code, err = self.stop_hook(case.get("checks"))
                self.assertEqual(code, 2, err)
                self.assertIn("the tree is red", err)

    def test_a_timer_that_passes_a_failing_check_is_red(self) -> None:
        timer = self.stop.load_timer()
        with mock.patch.object(timer, "run_check", return_value=timer.Outcome("passed", 0.0)):
            code, err = self.stop_hook()
        self.assertEqual(code, 2)
        self.assertIn("passed a failing check", err)

    def test_the_hook_backstop_is_below_the_harness_timeout(self) -> None:
        settings = json.loads((HOOKS.parent / "settings.json").read_text())
        (stop,) = [h for entry in settings["hooks"]["Stop"] for h in entry["hooks"]]
        self.assertIn("stop_checks.py", stop["command"])
        self.assertLess(self.stop.BACKSTOP_S + 60, stop["timeout"])

    def test_the_real_checks(self) -> None:
        labels = [label for label, _ in self.stop.CHECKS]
        self.assertEqual(labels, ["check-script meta-tests", "invariants", "capability map"])
        self.assertEqual(self.stop.CHECKS[0][1], ["python3", "scripts/run_meta_tests.py"])


if __name__ == "__main__":
    unittest.main()
