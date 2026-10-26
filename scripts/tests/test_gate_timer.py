"""Meta-tests for scripts/gate_timer.py (S0034): a check's time limit is a hang detector set from its own history,
never a budget; a check that did not finish, could not start or failed never passes. Scratch repositories only."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_timer as gt  # noqa: E402

PY = sys.executable
REPO = Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def signal_handlers_kept():
    """`gate_timer.main` installs SIGTERM/SIGHUP/SIGINT handlers; an in-process call must not leave them here."""
    kept = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        yield
    finally:
        for sig, handler in kept.items():
            signal.signal(sig, handler)


BACKSTOP_S = 60.0  # a hang backstop only: the waits below end on their condition (BL-0098)


def wait_until(condition, what: str) -> None:
    """Poll `condition` until it holds; fail only past the hang backstop."""
    deadline = time.monotonic() + BACKSTOP_S
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} (still after the {BACKSTOP_S:.0f} s backstop; hung?)")
        time.sleep(0.05)


def running(pid: int) -> bool:
    """The process exists and has not ended. An ended process its new parent has not reaped yet (a zombie) does not
    count, though `os.kill(pid, 0)` alone sees it as alive (BL-0098)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def ended_or_stopped(test: unittest.TestCase, pid: int, what: str) -> None:
    """Wait until `pid` has ended; past the backstop, kill it (this test started it) and fail."""
    try:
        wait_until(lambda: not running(pid), what)
    except AssertionError as exc:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        test.fail(str(exc))


class Limits(unittest.TestCase):
    def test_no_limit_without_enough_green_history(self) -> None:
        self.assertIsNone(gt.limit({}, "x"))
        self.assertIsNone(gt.limit({"x": [100.0] * (gt.MIN_SAMPLES - 1)}, "x"))
        self.assertIsNotNone(gt.limit({"x": [100.0] * gt.MIN_SAMPLES}, "x"))

    def test_limit_is_k_times_the_median_of_the_last_n_with_a_floor(self) -> None:
        old = [1000.0] * 10
        recent = [100.0, 110.0, 90.0, 400.0, 105.0]  # median 105; the old values and the outlier do not count
        self.assertEqual(gt.N, 5)
        self.assertEqual(gt.limit({"x": old + recent}, "x"), gt.K * 105.0)
        self.assertEqual(gt.limit({"x": [1.0, 1.0, 1.0]}, "x"), gt.FLOOR_S)  # a tiny check does not trip on noise
        self.assertGreater(gt.K, 1)
        self.assertGreater(gt.FLOOR_S, 0)

    def test_the_numbers_are_pinned_and_process_md_says_them(self) -> None:  # @regression-guard (S0034 verifier F2)
        self.assertEqual((gt.K, gt.N, gt.MIN_SAMPLES, gt.FLOOR_S), (8, 5, 3, 60.0))
        process = (REPO / "design/process/PROCESS.md").read_text(encoding="utf-8")
        self.assertIn(f"over {gt.K}\u00d7 the median of its last {gt.N} green runs in the clone (at least "
                      f"{gt.FLOOR_S:.0f} s)", process)
        self.assertIn(f"with fewer than {gt.MIN_SAMPLES} green runs it has no limit", process)

    def test_bad_history_values_are_ignored(self) -> None:
        self.assertIsNone(gt.limit({"x": "fast"}, "x"))
        self.assertIsNone(gt.limit({"x": [True, -5, "1", None]}, "x"))
        self.assertEqual(gt.usual({"x": [2, 4.0, 6]}, "x"), 4.0)

    def test_commit_ids_do_not_split_a_history(self) -> None:
        a = gt.check_key("privacy scan (pushed commits 8434091d..9844295a)")
        b = gt.check_key("privacy scan (pushed commits 0123456789abcdef0123456789abcdef01234567..abcdef1)")
        self.assertEqual(a, b)
        self.assertNotEqual(gt.check_key("invariants"), gt.check_key("capability map"))

    def test_record_keeps_the_last_ones(self) -> None:
        h: dict = {}
        for i in range(gt.KEEP + 5):
            h = gt.record(h, "x", float(i))
        self.assertEqual(len(h["x"]), gt.KEEP)
        self.assertEqual(h["x"][-1], float(gt.KEEP + 4))


class RunCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_exit_status_decides(self) -> None:
        self.assertEqual(gt.run_check([PY, "-c", "pass"], self.dir, 30, True).status, "passed")
        for code in ("raise SystemExit(1)", "raise SystemExit(2)", "import os; os.kill(os.getpid(), 9)"):
            with self.subTest(code=code):
                self.assertEqual(gt.run_check([PY, "-c", code], self.dir, 30, True).status, "failed")

    def test_a_check_that_cannot_start_is_an_error_not_a_crash(self) -> None:
        out = gt.run_check([str(self.dir / "no-such-tool")], self.dir, 30, True)
        self.assertEqual(out.status, "error")
        self.assertFalse(out.passed)

    def test_a_hung_check_and_what_it_started_are_stopped(self) -> None:
        pid_file = self.dir / "grandchild.pid"
        code = (f"import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; "
                f"time.sleep(60)']); open({str(pid_file)!r}, 'w').write(str(p.pid)); time.sleep(60)")
        start = time.monotonic()
        out = gt.run_check([PY, "-c", code], self.dir, 2, True)
        self.assertEqual(out.status, "hung")
        self.assertFalse(out.passed)
        self.assertLess(time.monotonic() - start, 30)
        # killed with the group; its parent died too, so a new parent reaps it, which may take a moment (BL-0098)
        ended_or_stopped(self, int(pid_file.read_text()), "the hung check's child kept running")

    def test_output_is_captured(self) -> None:
        out = gt.run_check([PY, "-c", "print('hello'); import sys; print('err', file=sys.stderr)"], self.dir, 30, True)
        self.assertIn("hello", out.output)
        self.assertIn("err", out.output)


class Timed(unittest.TestCase):
    """`timed` in a scratch repository: the history lives in its git directory, and only greens are recorded."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.times = self.repo / ".git/finzytrack/times-t.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def history(self) -> dict:
        return json.loads(self.times.read_text()) if self.times.exists() else {}

    def test_only_green_runs_are_recorded_outside_the_tree(self) -> None:
        self.assertTrue(gt.timed(self.repo, "t", "ok", [PY, "-c", "pass"], capture=True).passed)
        self.assertFalse(gt.timed(self.repo, "t", "bad", [PY, "-c", "raise SystemExit(1)"], capture=True).passed)
        self.assertEqual(list(self.history()), ["ok"])
        status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain", "--ignored"],
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status, "")

    def test_a_check_far_over_its_history_is_stopped_and_reported_with_the_numbers(self) -> None:
        self.times.parent.mkdir(parents=True)
        self.times.write_text(json.dumps({"slow": [0.2] * gt.N}))
        with mock.patch.object(gt, "FLOOR_S", 1.0):
            out = gt.timed(self.repo, "t", "slow", [PY, "-c", "import time; time.sleep(60)"], capture=True)
        self.assertEqual(out.status, "hung")
        self.assertIn(f"took over {gt.K}x its usual 0.2 s", out.message)
        self.assertIn("hung?", out.message)
        self.assertEqual(self.history(), {"slow": [0.2] * gt.N})  # a hung run is never recorded

    def test_the_deadline_caps_a_check_with_no_history(self) -> None:
        out = gt.timed(self.repo, "t", "new", [PY, "-c", "import time; time.sleep(60)"], capture=True,
                       deadline=time.monotonic() + 1)
        self.assertEqual(out.status, "hung")
        self.assertIn("backstop", out.message)

    def test_slow_but_under_the_limit_passes(self) -> None:
        self.times.parent.mkdir(parents=True)
        self.times.write_text(json.dumps({"s": [0.1] * gt.N}))
        with mock.patch.object(gt, "FLOOR_S", 1.5):
            out = gt.timed(self.repo, "t", "s", [PY, "-c", "import time; time.sleep(0.6)"], capture=True)
        self.assertTrue(out.passed)  # 6x its usual time, but under the floor: slowness alone never fails

    def test_a_corrupt_history_is_no_history(self) -> None:
        self.times.parent.mkdir(parents=True)
        self.times.write_text("{not json")
        self.assertTrue(gt.timed(self.repo, "t", "ok", [PY, "-c", "pass"], capture=True).passed)
        self.assertEqual(list(self.history()), ["ok"])


class Cli(unittest.TestCase):
    """The pre-push hook's view: `gate_timer.py run` exits 0 only for a check that ran and passed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.script = Path(gt.__file__).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([PY, str(self.script), *args], cwd=self.repo, capture_output=True, text=True,
                              env={**os.environ, **(env or {})}, timeout=120)

    def test_run_exit_codes(self) -> None:
        self.assertEqual(self.cli("run", "pre-push", "ok", "true").returncode, 0)
        for cmd in (["false"], [PY, "-c", "raise SystemExit(3)"], [str(self.repo / "missing")],
                    [PY, "-c", "import os; os.kill(os.getpid(), 15)"]):
            with self.subTest(cmd=cmd):
                self.assertNotEqual(self.cli("run", "pre-push", "x", *cmd).returncode, 0)
        self.assertNotEqual(self.cli("run", "pre-push", "no command").returncode, 0)
        self.assertNotEqual(self.cli("run").returncode, 0)

    def test_the_check_keeps_its_arguments_and_output(self) -> None:
        r = self.cli("run", "pre-push", "echo", PY, "-c", "import sys; print(sys.argv[1:])", "a b", "--", "-v")
        self.assertEqual(r.returncode, 0)
        self.assertIn("['a b', '--', '-v']", r.stdout)

    def test_report_lists_this_run_and_the_total(self) -> None:
        env = {"FT_GATE_RUN": "push-1"}
        times = self.repo / ".git/finzytrack/times-pre-push.json"
        times.parent.mkdir(parents=True)
        times.write_text(json.dumps({"first check": [1.0, 2.0, 3.0, 4.0, 2.5]}))  # usual 2.5 s (S0034 verifier F5)
        self.cli("run", "pre-push", "first check", PY, "-c", "import time; time.sleep(0.5)", env=env)
        self.cli("run", "pre-push", "second check", "false", env=env)
        r = self.cli("report", "pre-push", env=env)
        self.assertEqual(r.returncode, 0)
        lines = r.stderr.splitlines()
        first = next(line for line in lines if line.endswith("  first check"))
        second = next(line for line in lines if line.endswith("  second check"))
        total = next(line for line in lines if line.endswith("  total"))
        self.assertRegex(first, r"^ +\d+\.\d s  \(usual 2\.5 s\)  first check$")
        self.assertRegex(second, r"^ +\d+\.\d s  \(no usual yet\)  FAILED  second check$")
        self.assertRegex(total, r"^ +\d+\.\d s  \(usual 2\.5 s, over the checks with a usual time\)  total$")
        seconds = [float(x.split()[0]) for x in (first, second, total)]
        self.assertGreaterEqual(seconds[0], 0.5)
        self.assertAlmostEqual(seconds[2], seconds[0] + seconds[1], delta=0.11)
        other = self.cli("report", "pre-push", env={"FT_GATE_RUN": "push-2"})
        self.assertEqual(other.returncode, 0)
        self.assertNotIn("first check", other.stderr)

    def test_report_never_fails(self) -> None:
        log = self.repo / ".git/finzytrack/last-run-pre-push.json"
        log.parent.mkdir(parents=True)
        for text in ("{bad", json.dumps({"run": "p", "checks": [{"seconds": "x"}], "at": time.time()})):
            log.write_text(text)
            with self.subTest(text=text):
                self.assertEqual(self.cli("report", "pre-push", env={"FT_GATE_RUN": "p"}).returncode, 0)

    def test_main_never_passes_when_the_timer_breaks(self) -> None:
        with mock.patch.object(gt, "timed", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gt.main(["run", "pre-push", "x", "true"]), 1)


class SizeDependent(unittest.TestCase):
    """S0034 verifier round 2 F1: a check whose time grows with its input (the pushed range, the changed files, the
    new lock entries) shares one history across input sizes, so its history predicts no hang: it has no history
    limit, only the caller's backstop. A fixed-size check keeps its limit."""

    LABEL = "privacy scan (pushed commits and messages 8434091d..9844295a)"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.times = self.repo / ".git/finzytrack/times-pre-push.json"
        self.times.parent.mkdir(parents=True)
        self.times.write_text(json.dumps({gt.check_key(self.LABEL): [0.1] * gt.N}))  # a limit of FLOOR_S

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_long_input_is_never_stopped_while_a_fixed_size_check_still_is(self) -> None:
        slow = [PY, "-c", "import time; time.sleep(2.5)"]
        with mock.patch.object(gt, "FLOOR_S", 1.0):
            sized = gt.timed(self.repo, "pre-push", self.LABEL, slow, capture=True, size_dependent=True)
            fixed = gt.timed(self.repo, "pre-push", self.LABEL, slow, capture=True)
        self.assertTrue(sized.passed, sized.message)
        self.assertEqual(fixed.status, "hung")

    def test_the_backstop_still_stops_a_size_dependent_check(self) -> None:
        out = gt.timed(self.repo, "pre-push", self.LABEL, [PY, "-c", "import time; time.sleep(60)"], capture=True,
                       size_dependent=True, deadline=time.monotonic() + 1)
        self.assertEqual(out.status, "hung")
        self.assertIn("backstop", out.message)

    def test_the_cli_marker(self) -> None:
        """`run <hook> <label> --size-dependent <command...>`: the marker is the timer's, not the command's."""
        cmd = [PY, "-c", "import sys, time; time.sleep(2.5); sys.exit(sys.argv[1:] != ['x'])", "x"]
        with mock.patch.object(gt, "FLOOR_S", 1.0), signal_handlers_kept(), contextlib.chdir(self.repo), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(gt.main(["run", "pre-push", self.LABEL, gt.SIZE_DEPENDENT, *cmd]), 0, err.getvalue())
            self.assertEqual(gt.main(["run", "pre-push", self.LABEL, *cmd]), 1)
            self.assertIn("hung?", err.getvalue())
            self.assertEqual(gt.main(["run", "pre-push", self.LABEL, gt.SIZE_DEPENDENT]), 2)  # no command: usage
            self.assertIn(f"no command after {gt.SIZE_DEPENDENT}", err.getvalue())

    def test_the_report_says_which_checks_have_no_limit(self) -> None:
        env = {**os.environ, "FT_GATE_RUN": "push-1"}
        script = str(Path(gt.__file__).resolve())
        for args in ([self.LABEL, gt.SIZE_DEPENDENT, "true"], ["invariants", "true"]):
            subprocess.run([PY, script, "run", "pre-push", *args], cwd=self.repo, env=env, check=True, timeout=120)
        lines = subprocess.run([PY, script, "report", "pre-push"], cwd=self.repo, env=env, capture_output=True,
                               text=True, check=True, timeout=120).stderr.splitlines()
        sized = next(line for line in lines if line.endswith(self.LABEL))
        fixed = next(line for line in lines if line.endswith("  invariants"))
        self.assertRegex(sized, r"^ +\d+\.\d s  \(usual 0\.1 s; no hang limit, its time grows with its input\)  ")
        self.assertRegex(fixed, r"^ +\d+\.\d s  \(no usual yet\)  invariants$")

    def test_the_pre_push_marks_exactly_the_checks_whose_time_grows_with_the_push(self) -> None:
        """Marked: the checks of each pushed range or tag, and the crates.io re-read of new lock entries (one
        request a second). Not marked: every check of the whole tree, whose history follows the tree's growth."""
        runs = [line.strip() for line in (REPO / ".githooks/pre-push").read_text().splitlines()
                if line.strip().startswith('run "')]
        marked = {m.group(1) for line in runs if (m := re.match(rf'run "([^"]*)" {gt.SIZE_DEPENDENT} ', line))}
        grows = {m.group(1) for line in runs if (m := re.match(r'run "([^"]*)"', line))
                 and ("$range" in m.group(1) or "${range#tag }" in m.group(1) or m.group(1).startswith("cargo "))}
        self.assertEqual(len(grows), 5)
        self.assertEqual(marked, grows)


class Signals(unittest.TestCase):
    """S0034 verifier round 2 F3: an aborted push (Ctrl-C, SIGTERM, a closed terminal) stops the check's process
    group, which runs in its own session and so gets no terminal signal itself, and the timer fails. BL-0098 (S0043):
    the waits end on a condition with a hang backstop, never after a fixed time, and a signal that lands while the
    check is starting or being stopped still stops it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_signal_to_the_timer_stops_the_check_and_fails(self) -> None:  # @regression-guard (reworked, BL-0098)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig.name):
                pid_file = Path(self.tmp.name) / f"{sig.name}.pid"
                code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
                timer = subprocess.Popen([PY, str(Path(gt.__file__).resolve()), "run", "pre-push", "sig", PY, "-c",
                                          code], cwd=self.repo, stderr=subprocess.DEVNULL)
                wait_until(lambda: timer.poll() is not None or (pid_file.exists() and pid_file.read_text()),
                           "the check never started")
                self.assertIsNone(timer.poll(), "the timer ended before its check started")
                child = int(pid_file.read_text())
                timer.send_signal(sig)
                self.assertNotEqual(timer.wait(timeout=BACKSTOP_S), 0)
                ended_or_stopped(self, child, f"{sig.name}: the check kept running after the timer exited")

    def test_a_signal_while_the_check_is_starting_still_stops_it(self) -> None:
        """The signal lands after the check's process exists but before `run_check` holds it (under load the check
        can start and write its pid before the timer returns from Popen)."""
        real = subprocess.Popen
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            started: list[subprocess.Popen] = []

            def popen(*args, sig=sig, started=started, **kwargs):
                started.append(real(*args, **kwargs))
                os.kill(os.getpid(), sig)
                return started[-1]

            with self.subTest(sig.name), signal_handlers_kept():
                gt.stop_checks_on_signals()
                try:
                    with mock.patch.object(gt.subprocess, "Popen", popen), self.assertRaises(KeyboardInterrupt):
                        gt.run_check([PY, "-c", "import time; time.sleep(120)"], self.repo, None, True)
                    self.assertIsNotNone(started[0].poll(), f"{sig.name}: the check kept running")
                finally:
                    for p in started:
                        if p.poll() is None:
                            p.kill()
                            p.wait()

    def test_a_second_signal_while_stopping_does_not_cut_the_stop_short(self) -> None:
        """A second Ctrl-C or SIGTERM during the stop's grace time: a check that ignores SIGTERM still gets SIGKILL,
        and the interrupt is not lost."""
        ready = Path(self.tmp.name) / "ready"
        code = (f"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(ready)!r}, 'w')"
                f".write('1'); time.sleep(120)")
        real = os.killpg

        def killpg(pgid: int, sig: int) -> None:
            real(pgid, sig)
            if sig == signal.SIGTERM:
                os.kill(os.getpid(), signal.SIGTERM)

        with signal_handlers_kept():
            gt.stop_checks_on_signals()
            proc = subprocess.Popen([PY, "-c", code], start_new_session=True)
            try:
                wait_until(lambda: proc.poll() is not None or (ready.exists() and ready.read_text()),
                           "the check never started")
                with mock.patch.object(gt.os, "killpg", killpg), mock.patch.object(gt, "GRACE_S", 0.5), \
                        self.assertRaises(KeyboardInterrupt):
                    gt.stop_group(proc)
                try:
                    self.assertEqual(proc.wait(timeout=BACKSTOP_S), -signal.SIGKILL)
                except subprocess.TimeoutExpired:
                    self.fail("the check kept running: the second signal cut the stop short")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    unittest.main()
