"""Meta-tests for the agent session hooks' git-hooks check (S0031, the M0 gate red team L2): the session-start hook
warns and the Stop hook fails when this clone does not run `.githooks`. Scratch repositories only."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parents[2] / ".claude/hooks"
SCRIPTS = Path(__file__).resolve().parents[1]
WAIT_BACKSTOP_S = 60.0  # a hang backstop only: the process waits end on their condition (BL-0098, S0043)


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

    def stop_hook(self, checks: list | None = None, event: dict | None = None, scan: bool = False) -> tuple[int, str]:
        """The hook on the scratch tree. Without `scan`, no privacy scan of changed files (these trees have no
        scripts/); with it, the scan runs as in the real hook (see `real_scripts`)."""
        err = io.StringIO()
        checks = [("counting", self.counting)] if checks is None else checks
        with contextlib.ExitStack() as stack:
            if not scan:
                stack.enter_context(mock.patch.object(self.stop, "changed_files", return_value=[]))
            with mock.patch.object(self.stop, "REPO", self.repo), mock.patch.object(self.stop, "CHECKS", checks), \
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event or {}))), \
                    contextlib.redirect_stderr(err):
                return self.stop.main(), err.getvalue()

    def real_scripts(self) -> None:
        """The scratch tree's scripts/ is the real repository's (a symlink, untracked, not a file, so not scanned),
        and the privacy scan reads an empty denylist instead of the owner's."""
        (self.repo / "scripts").symlink_to(SCRIPTS, target_is_directory=True)
        deny = Path(self.tmp.name) / "denylist.txt"
        deny.write_text("")
        patch = mock.patch.dict(os.environ, {"FT_PRIVACY_DENYLIST": str(deny)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_privacy_finding_in_a_changed_file_is_red(self) -> None:  # @regression-guard (S0034 verifier R2 F7)
        self.real_scripts()
        planted = "write to " + "someone" + "@" + "gmail" + ".com\n"  # an email address outside the example domains
        cases = {
            "an untracked file": lambda text: (self.repo / "notes.md").write_text(text),
            "a tracked file edited": lambda text: (self.repo / "a.txt").write_text(text),
        }
        for label, write in cases.items():
            with self.subTest(label):
                write(planted)
                code, err = self.stop_hook(scan=True)
                self.assertEqual(code, 2, err)
                self.assertIn("privacy scan of changed files", err)
                write("clean\n")
                self.assertEqual(self.stop_hook(scan=True), (0, ""))  # the same scan passes a clean change

    def test_only_the_scan_of_changed_files_has_no_history_limit(self) -> None:  # S0034 verifier round 2 F1
        """Its time grows with the changed files, so its history predicts no hang; the fixed checks keep theirs."""
        self.real_scripts()
        (self.repo / "a.txt").write_text("changed\n")
        timer, seen = self.stop.load_timer(), {}
        real = timer.timed

        def spy(repo, hook, label, cmd, **kw):
            seen[label] = kw.get("size_dependent", False)
            return real(repo, hook, label, cmd, **kw)

        with mock.patch.object(timer, "timed", spy):
            self.assertEqual(self.stop_hook(scan=True), (0, ""))
        self.assertEqual(seen, {"counting": False, "privacy scan of changed files": True})

    def test_the_key_writes_nothing_into_the_object_store(self) -> None:  # S0034 verifier round 2 F5
        """A large or private untracked file must not become a loose object of the clone each time it changes."""
        worktree = Path(self.tmp.name) / "wt"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "--detach", str(worktree)], check=True)
        odd = Path(self.tmp.name) / 'a:b "c"'  # a path git must read quoted as an alternate
        subprocess.run(["git", "clone", "-q", str(self.repo), str(odd)], check=True)

        def objects(store: Path) -> str:
            return subprocess.run(["git", "-C", str(store), "count-objects", "-v"], capture_output=True,
                                  text=True, check=True).stdout

        keys = set()
        for tree, store in ((self.repo, self.repo), (worktree, self.repo), (odd, odd)):
            before = objects(store)
            for i in range(2):
                with self.subTest(tree=tree.name, run=i):
                    (tree / "export.csv").write_bytes(os.urandom(100_000))
                    (tree / "a.txt").write_text(f"edit {i}\n")
                    key = self.stop.tree_key(tree)
                    self.assertIsNotNone(key)
                    keys.add(key)
                    self.assertEqual(objects(store), before)
        self.assertEqual(len(keys), 6)  # the key still follows the content
        (self.repo / "export.csv").write_bytes(b"same\n")
        (worktree / "export.csv").write_bytes(b"same\n")
        (self.repo / "a.txt").write_text("same\n")
        (worktree / "a.txt").write_text("same\n")
        self.assertEqual(self.stop.tree_key(self.repo), self.stop.tree_key(worktree))

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


class StopHookSignals(unittest.TestCase):
    """S0034 verifier round 2 F4: the harness ends the Stop hook (its timeout, the user interrupting) while a check
    runs. The checks run in sessions of their own, so the hook must stop them on the way out: no orphans."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "r"
        config = base / "gitconfig"
        config.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.repo), "config", "core.hooksPath", ".githooks"], check=True, env=self.env)
        (self.repo / ".claude/hooks").mkdir(parents=True)
        (self.repo / "scripts").mkdir()
        shutil.copy(HOOKS / "stop_checks.py", self.repo / ".claude/hooks/stop_checks.py")
        shutil.copy(SCRIPTS / "gate_timer.py", self.repo / "scripts/gate_timer.py")
        self.pids = base / "pids"
        # stands in for the meta-test runner: it starts a worker and both run long
        (self.repo / "scripts/run_meta_tests.py").write_text(
            "import os, subprocess, sys, time\n"
            "w = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(self.pids)!r}, 'w').write(f'{{os.getpid()}} {{w.pid}}')\n"
            "time.sleep(120)\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True, env=self.env)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def alive(pid: int) -> bool:
        """Running: an ended process its new parent has not reaped yet (a zombie) does not count (BL-0098, S0043)."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
        return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")

    @staticmethod
    def wait_until(condition) -> bool:
        """Poll until `condition` holds; False only past the hang backstop (WAIT_BACKSTOP_S), never a fixed window."""
        deadline = time.monotonic() + WAIT_BACKSTOP_S
        while not condition():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def test_ending_the_hook_stops_its_checks(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig.name):
                self.pids.unlink(missing_ok=True)
                hook = subprocess.Popen([sys.executable, str(self.repo / ".claude/hooks/stop_checks.py")],
                                        cwd=self.repo, env=self.env, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True)
                hook.stdin.write("{}")
                hook.stdin.close()
                started = self.wait_until(lambda: hook.poll() is not None
                                          or (self.pids.exists() and len(self.pids.read_text().split()) == 2))
                self.assertTrue(started and hook.poll() is None, "the hook's checks never started")
                pids = [int(x) for x in self.pids.read_text().split()]
                hook.send_signal(sig)
                try:
                    code = hook.wait(timeout=WAIT_BACKSTOP_S)
                    err = hook.stderr.read()
                finally:
                    hook.stderr.close()
                    self.wait_until(lambda: not any(self.alive(pid) for pid in pids))
                    left = [pid for pid in pids if self.alive(pid)]
                    for pid in left:  # this test's own processes, still running past the backstop
                        os.kill(pid, signal.SIGKILL)
                self.assertEqual(left, [], "a check outlived the hook")
                self.assertEqual(code, 2, err)
                self.assertIn("stop_checks was interrupted", err)


class ImplementersNeverStartAgents(unittest.TestCase):
    """Owner, 2026-10-26: an implementer never starts a verifier (or any agent); the orchestrator does (IMP-0029)."""

    def run_hook(self, payload: str) -> tuple[int, str]:
        r = subprocess.run([sys.executable, str(HOOKS / "guard_agent.py")], input=payload, text=True,
                           capture_output=True, timeout=30)
        return r.returncode, r.stderr

    def assert_blocked(self, payload: str) -> None:
        code, err = self.run_hook(payload)
        self.assertEqual(code, 2)
        self.assertIn("guard_agent: blocked", err)  # not just exit 2: a missing hook file also exits 2

    def bash(self, command: str, agent: str | None = "implementer") -> str:
        event: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": command}}
        if agent is not None:
            event["agent_type"] = agent
        return json.dumps(event)

    def test_an_implementer_spawning_any_agent_is_blocked(self) -> None:
        for tool in ("Agent", "Task", "SendMessage"):
            for kind in ("verifier", "Explore", "implementer", "general-purpose"):
                with self.subTest(tool=tool, kind=kind):
                    self.assert_blocked(json.dumps({"tool_name": tool, "agent_type": "implementer", "agent_id": "a1",
                                                    "tool_input": {"subagent_type": kind}}))

    def test_an_implementer_running_the_claude_cli_is_blocked(self) -> None:  # IMP-0029 verifier F1
        for command in ("claude -p --agent verifier hi", "cd x && claude -p hi", "~/.local/bin/claude -p hi",
                        "echo $(claude -p hi)", "npx @anthropic-ai/claude-code -p hi",
                        "python3 -c \"import subprocess; subprocess.run(['claude', '-p', 'hi'])\""):
            with self.subTest(command=command):
                self.assert_blocked(self.bash(command))

    def test_unreadable_input_is_blocked(self) -> None:
        for payload in ("not json", "[1]", "", json.dumps({"tool_name": "Agent", "agent_type": ["implementer"]}),
                        json.dumps({"tool_name": ["Agent"], "agent_type": "implementer"}),
                        json.dumps({"tool_name": "Bash", "agent_type": "implementer", "tool_input": "x"})):
            with self.subTest(payload=payload):
                self.assert_blocked(payload)

    def test_the_orchestrator_other_agents_and_ordinary_commands_pass(self) -> None:  # @regression-guard
        for payload in (json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "verifier"}}),
                        json.dumps({"tool_name": "Agent", "agent_type": "verifier", "agent_id": "a2"}),
                        self.bash("claude -p hi", agent=None), self.bash("claude -p hi", agent="verifier"),
                        self.bash("cat .claude/hooks/guard_bash.py && git add .claude/settings.json"),
                        self.bash("ls ~/.claude/projects"), self.bash("python3 scripts/run_meta_tests.py")):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook(payload)[0], 0)

    def test_the_hook_is_registered_for_every_route(self) -> None:
        settings = json.loads((HOOKS.parent / "settings.json").read_text())
        entries = [e for e in settings["hooks"]["PreToolUse"] if "guard_agent.py" in json.dumps(e)]
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["matcher"].split("|")), {"Agent", "Task", "SendMessage", "Bash"})

    def test_the_definition_also_denies_the_tools(self) -> None:
        head = (HOOKS.parent / "agents/implementer.md").read_text().split("---")[1]
        self.assertIn("disallowedTools: Agent, Task, SendMessage", head)


if __name__ == "__main__":
    unittest.main()
