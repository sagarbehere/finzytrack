"""Meta-tests for the acceptance-suite harness (S0030; review:27 §1(d); INV-24, BL-0024).

The harness runs each Playwright worker against its own temp root, demo workspace, main sidecar and LLM stub. Its
checks (the data dirs, the port, process ownership, the time zone profiles) each sit beside the cases that must
not pass (EP-29). The end-to-end test starts the real sidecar from the backend's venv.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "acceptance"))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))

import guards  # noqa: E402
import launcher as harness  # noqa: E402

BACKEND_PY = REPO / "backend" / ".venv" / "bin" / "python"


def pid_alive(pid: int) -> bool:
    """Signal 0 only probes; the pid is the one the run's own sidecar reported."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone(pid: int, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end and pid_alive(pid):
        threading.Event().wait(0.05)
    return not pid_alive(pid)


class EnvMixin(unittest.TestCase):
    """A private live folder and legacy worktree, so the forbidden roots include folders the test can see."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name).resolve()
        # `base` is where the test may let the harness work; it must not contain the forbidden folders.
        self.base, self.live, self.legacy = top / "work", top / "live", top / "legacy"
        for d in (self.base, self.live, self.legacy):
            d.mkdir()
        self.old = {k: os.environ.get(k) for k in ("FT_LIVE_DATA", "FT_LEGACY_ROOT")}
        os.environ.update({"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy)})

    def tearDown(self) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


class TempRootTest(EnvMixin):
    def test_the_root_is_a_fresh_private_folder_outside_every_forbidden_place(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            self.assertTrue(root.path.is_dir())
            self.assertEqual(stat.S_IMODE(root.path.stat().st_mode), 0o700)
            self.assertTrue(root.path.name.startswith("ft-acceptance-"))
            self.assertEqual(list(root.path.iterdir()), [])
        finally:
            root.remove()

    def test_a_root_is_never_created_in_a_forbidden_place(self) -> None:
        # Must not pass: TMPDIR (or a parent) in the repo, the live data folder, the legacy worktree, or a folder
        # that contains one of them. Nothing may be created there, not even an empty folder.
        firm = Path("/", "System", "Volumes", "Data")  # the macOS firmlink prefix, from parts (privacy scan)
        firm_live = [Path(str(firm) + str(self.live))] if sys.platform == "darwin" and firm.is_dir() else []
        for parent in (REPO, REPO / "build", self.live, self.legacy, Path(str(self.live).upper()), Path("/"),
                       *firm_live):
            with self.subTest(parent=parent):
                before = sorted(p.name for p in parent.iterdir()) if parent.is_dir() else None
                with self.assertRaises(harness.Refused):
                    harness.TempRoot(parent=parent)
                after = sorted(p.name for p in parent.iterdir()) if parent.is_dir() else None
                self.assertEqual(before, after)

    def test_make_dir_creates_a_fresh_child_once(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            d = root.make_dir("app-local-data")
            self.assertEqual(d, root.path / "app-local-data")
            self.assertTrue(d.is_dir())
            self.assertIs(root.check_own(d), d)
            with self.assertRaises(harness.Refused):  # not fresh any more
                root.make_dir("app-local-data")
        finally:
            root.remove()

    def test_make_dir_takes_one_plain_name(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            for bad in ("", ".", "..", "a/b", "../x", "/abs", "a\0b", "a\\b", "A/../b", ".hidden"):
                with self.subTest(name=bad), self.assertRaises(harness.Refused):
                    root.make_dir(bad)
            self.assertEqual(list(root.path.iterdir()), [])
        finally:
            root.remove()

    def test_check_own_accepts_only_folders_the_harness_made(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            made = root.make_dir("app-cache")
            (root.path / "planted").mkdir()  # inside the root, but not made by the harness
            (made / "nested").mkdir()
            (root.path / "link").symlink_to(made)
            (root.path / "out").symlink_to(self.live)
            outside = self.base / "outside"
            outside.mkdir()
            for bad in (root.path, root.path / "planted", made / "nested", root.path / "link", root.path / "out",
                        outside, self.live, Path(str(made).upper()), made / ".." / "app-cache",
                        root.path / "never-made"):
                with self.subTest(path=bad), self.assertRaises(harness.Refused):
                    root.check_own(bad)
            root.check_own(made)
        finally:
            root.remove()

    def test_a_made_folder_that_was_replaced_is_refused(self) -> None:  # @regression-guard (S0036)
        """A made folder replaced at the same path is refused when the check can tell (S0036).

        What the check cannot tell: a folder removed and then made again with mkdir. On Linux ext4 the new folder
        gets the freed inode at once, so device and inode are the same (macOS APFS does not reuse it at once).
        That case cannot reach live data: the new folder is a plain folder inside this run's own root. A link is
        refused by its type, a mount or another file system by its device. Change times are not compared: the
        app writes into these folders.
        """
        root = harness.TempRoot(parent=self.base)
        try:
            d = root.make_dir("workspace")
            other = root.path / "other"
            other.mkdir()  # exists while d exists, so its inode differs from d's on every file system
            d.rmdir()
            other.rename(d)  # same path, another folder
            with self.assertRaises(harness.Refused):
                root.check_own(d)
            d.rmdir()
            d.symlink_to(self.live)  # same path, now a link to the live data folder
            with self.assertRaises(harness.Refused):
                root.check_own(d)
        finally:
            root.remove()

    def test_a_made_folder_on_another_device_is_refused(self) -> None:  # @regression-guard (S0036)
        """A mount over a made folder keeps its path but changes its device; mounting needs root, so the device
        number `os.lstat` reports for the folder is changed instead (S0036)."""
        root = harness.TempRoot(parent=self.base)
        try:
            d = root.make_dir("workspace")
            real = os.lstat

            def lstat(p: os.PathLike[str] | str) -> os.stat_result:
                st = real(p)
                if Path(p) != d:
                    return st
                fields = list(st)
                fields[stat.ST_DEV] = st.st_dev + 1
                return os.stat_result(fields)

            with mock.patch.object(harness.os, "lstat", lstat), self.assertRaises(harness.Refused):
                root.check_own(d)
            root.check_own(d)
        finally:
            root.remove()

    def test_a_link_that_took_the_made_folders_inode_is_refused(self) -> None:  # @regression-guard (S0036)
        """On ext4 a link made right after the folder was removed can get the folder's inode, so only its type
        tells it apart; `os.lstat` reports the made folder's device and inode for the link here."""
        root = harness.TempRoot(parent=self.base)
        try:
            d = root.make_dir("workspace")
            made = os.lstat(d)
            d.rmdir()
            d.symlink_to(self.live)
            real = os.lstat

            def lstat(p: os.PathLike[str] | str) -> os.stat_result:
                st = real(p)
                if Path(p) != d:
                    return st
                fields = list(st)
                fields[stat.ST_DEV], fields[stat.ST_INO] = made.st_dev, made.st_ino
                return os.stat_result(fields)

            with mock.patch.object(harness.os, "lstat", lstat), self.assertRaises(harness.Refused):
                root.check_own(d)
            d.unlink()
        finally:
            root.remove()

    def test_remove_deletes_only_its_own_root(self) -> None:
        sibling = self.base / "sibling"
        sibling.mkdir()
        (sibling / "keep.txt").write_text("keep")
        root = harness.TempRoot(parent=self.base)
        root.make_dir("workspace")
        root.remove()
        self.assertFalse(root.path.exists())
        self.assertEqual((sibling / "keep.txt").read_text(), "keep")

    def test_remove_refuses_a_root_that_was_swapped_for_a_link(self) -> None:
        root = harness.TempRoot(parent=self.base)
        (self.live / "ledger.beancount").write_text("x")
        os.rmdir(root.path)
        root.path.symlink_to(self.live)
        with self.assertRaises(harness.Refused):
            root.remove()
        self.assertEqual((self.live / "ledger.beancount").read_text(), "x")
        root.path.unlink()


class PortTest(unittest.TestCase):
    def test_the_owner_port_is_the_guard_hooks_owner_port(self) -> None:
        self.assertEqual(str(harness.OWNER_PORT), guards.OWNER_PORT)

    def test_check_port_accepts_unprivileged_ports_other_than_the_owners(self) -> None:
        for good in (1025, 49152, 65535, 8000, 8002):
            self.assertEqual(harness.check_port(good), good)

    def test_check_port_refuses_the_owners_port_and_anything_not_a_usable_port(self) -> None:
        for bad in (8001, 0, 80, 1024, 65536, -1, True, "5000", 5000.0, None):
            with self.subTest(port=bad), self.assertRaises(harness.Refused):
                harness.check_port(bad)  # type: ignore[arg-type]

    def test_a_handshake_line_gives_port_and_version(self) -> None:
        self.assertEqual(harness.parse_handshake('FT_READY {"port":51234,"version":"0.4.0.dev0"}\n'),
                         (51234, "0.4.0.dev0"))

    def test_a_handshake_that_is_not_exactly_the_contract_is_refused(self) -> None:
        for bad in ('', 'READY {"port":51234,"version":"v"}', 'FT_READY {"port":8001,"version":"v"}',
                    'FT_READY {"port":"51234","version":"v"}', 'FT_READY {"port":51234}',
                    'FT_READY {"port":51234,"version":"v","host":"0.0.0.0"}', 'FT_READY {"port":true,"version":"v"}',
                    'x FT_READY {"port":51234,"version":"v"}', 'FT_READY {"port":51234,"version":"v"} trailing',
                    'FT_READY [51234]', 'FT_READY {"port":0,"version":"v"}', 'FT_READY {"port":51234,"version":3}'):
            with self.subTest(line=bad), self.assertRaises(harness.Refused):
                harness.parse_handshake(bad)


class ChildrenTest(unittest.TestCase):
    def sleeper(self) -> list[str]:
        return [sys.executable, "-c", "import sys; sys.stdin.read()"]

    def test_stop_stops_a_child_it_started(self) -> None:
        kids = harness.Children()
        proc = kids.spawn(self.sleeper(), stdin=subprocess.PIPE)
        self.assertIsNone(proc.poll())
        kids.stop(proc)
        self.assertIsNotNone(proc.poll())

    def test_stop_refuses_any_process_it_did_not_start(self) -> None:
        kids = harness.Children()
        stranger = subprocess.Popen(self.sleeper(), stdin=subprocess.PIPE)  # noqa: S603
        try:
            for bad in (stranger, stranger.pid, os.getpid(), None):
                with self.subTest(target=bad), self.assertRaises(harness.Refused):
                    kids.stop(bad)  # type: ignore[arg-type]
            self.assertIsNone(stranger.poll())  # still running: nothing was signalled
        finally:
            assert stranger.stdin is not None
            stranger.stdin.close()
            stranger.wait(timeout=10)

    def test_stop_all_stops_every_child_and_only_them(self) -> None:
        kids = harness.Children()
        procs = [kids.spawn(self.sleeper(), stdin=subprocess.PIPE) for _ in range(2)]
        kids.stop_all()
        self.assertTrue(all(p.poll() is not None for p in procs))

    def test_the_harness_never_signals_a_process_except_through_its_own_handles(self) -> None:
        # EP-30: no pid, name or port lookup anywhere in the harness, so it cannot stop the owner's app.
        source = (REPO / "scripts/acceptance/launcher.py").read_text(encoding="utf-8")
        banned = re.compile(r"\bos\.kill\b|killpg|\bpkill\b|killall|\blsof\b|psutil|\bfuser\b|os\.waitpid|pidfd")
        self.assertEqual(banned.findall(source), [])


class LaunchSpecTest(EnvMixin):
    def test_the_sidecar_gets_port_0_and_only_folders_the_harness_made(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            dirs = harness.Dirs.make(root)
            frozen = datetime(2026, 4, 1, 4, 30, tzinfo=UTC)
            argv = harness.backend_argv(root, dirs, frozen)
            self.assertEqual(argv[argv.index("--port") + 1], "0")
            self.assertEqual(argv.count("--port"), 1)
            for flag, d in (("--app-local-data", dirs.app_local_data), ("--app-cache", dirs.app_cache),
                            ("--workspace", dirs.workspace)):
                self.assertEqual(argv[argv.index(flag) + 1], str(d))
            self.assertEqual(datetime.fromisoformat(argv[argv.index("--frozen-clock") + 1]), frozen)
            foreign = harness.Dirs(home=dirs.home, app_local_data=self.live, app_cache=dirs.app_cache,
                                   workspace=dirs.workspace)
            with self.assertRaises(harness.Refused):
                harness.backend_argv(root, foreign, frozen)
        finally:
            root.remove()

    def test_the_child_environment_is_small_and_its_home_is_inside_the_root(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            dirs = harness.Dirs.make(root)
            env = harness.child_env(root, dirs, "America/Los_Angeles")
            self.assertEqual(env["TZ"], "America/Los_Angeles")
            self.assertEqual(env["HOME"], str(dirs.home))
            self.assertLessEqual(set(env), {"PATH", "TZ", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE", "SYSTEMROOT"})
            self.assertNotIn("FT_LIVE_DATA", env)
        finally:
            root.remove()


class ProfileTest(unittest.TestCase):
    def test_los_angeles_is_evening_while_utc_is_already_the_next_month(self) -> None:
        at, today = harness.profile("America/Los_Angeles")
        self.assertEqual(at.astimezone(UTC), datetime(2026, 4, 1, 4, 30, tzinfo=UTC))
        self.assertEqual(today, date(2026, 3, 31))
        self.assertLess(at.utcoffset(), timedelta(0))

    def test_kolkata_is_after_midnight_while_utc_is_still_the_previous_month(self) -> None:
        at, today = harness.profile("Asia/Kolkata")
        self.assertEqual(at.astimezone(UTC), datetime(2026, 3, 31, 19, 0, tzinfo=UTC))
        self.assertEqual(today, date(2026, 4, 1))
        self.assertGreater(at.utcoffset(), timedelta(0))

    def test_the_default_zone_is_west_of_utc(self) -> None:
        at, _ = harness.profile(harness.DEFAULT_TZ)
        self.assertLess(at.utcoffset(), timedelta(0))

    def test_a_zone_without_a_profile_is_refused(self) -> None:
        # Must not pass: UTC (hides every "UTC today" bug) and zones the suite has no frozen instant for.
        for bad in ("UTC", "Etc/UTC", "Europe/London", "", "america/los_angeles", "Not/AZone"):
            with self.subTest(tz=bad), self.assertRaises(harness.Refused):
                harness.profile(bad)


class LlmStubTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frozen = datetime(2026, 4, 1, 4, 30, tzinfo=UTC)
        self.stub = harness.LlmStub(self.frozen)
        self.stub.start()

    def tearDown(self) -> None:
        self.stub.stop()

    def call(self, method: str, path: str, body: object | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.stub.port, timeout=10)
        data = None if body is None else json.dumps(body)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        conn.close()
        return out

    def chat(self, text: str, **extra: object) -> tuple[int, dict]:
        return self.call("POST", "/v1/chat/completions",
                         {"model": "stub-model", "messages": [{"role": "user", "content": text}], **extra})

    def test_it_listens_on_loopback_on_a_port_of_its_own(self) -> None:
        self.assertEqual(self.stub.host, "127.0.0.1")
        harness.check_port(self.stub.port)

    def test_models_lists_the_stub_model(self) -> None:
        status, body = self.call("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in body["data"]], ["stub-model"])

    def test_a_scripted_reply_is_an_openai_chat_completion(self) -> None:
        self.assertEqual(self.call("PUT", "/__harness/script", {"rules": [
            {"match": "categorize", "reply": "Expenses:Food"}]})[0], 200)
        status, body = self.chat("Please categorize: Corner Bakery")
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"], {"role": "assistant", "content": "Expenses:Food"})
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["created"], int(self.frozen.timestamp()))  # the frozen clock, not the real one

    def test_a_scripted_tool_call(self) -> None:
        call = {"id": "call-1", "type": "function", "function": {"name": "propose_rule", "arguments": "{}"}}
        self.call("PUT", "/__harness/script", {"rules": [{"match": "rule", "tool_calls": [call]}]})
        status, body = self.chat("make a rule")
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["tool_calls"], [call])
        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")

    def test_an_unscripted_call_fails_loudly_and_is_recorded(self) -> None:
        status, body = self.chat("anything")
        self.assertEqual(status, 500)
        self.assertIn("no scripted reply", body["error"]["message"])
        _, calls = self.call("GET", "/__harness/calls")
        self.assertEqual(len(calls["unscripted"]), 1)
        self.assertEqual(len(calls["calls"]), 1)

    def test_streaming_is_refused_until_it_is_scripted(self) -> None:
        self.call("PUT", "/__harness/script", {"rules": [{"match": "", "reply": "x"}]})
        status, _ = self.chat("hi", stream=True)
        self.assertEqual(status, 400)

    def test_a_malformed_script_is_refused(self) -> None:
        for bad in ({}, {"rules": "x"}, {"rules": [{"reply": "x"}]}, {"rules": [{"match": "a"}]},
                    {"rules": [{"match": "a", "reply": "x", "tool_calls": []}]}, {"rules": [{"match": 1, "reply": "x"}]}):
            with self.subTest(script=bad):
                self.assertEqual(self.call("PUT", "/__harness/script", bad)[0], 400)

    def test_unknown_paths_are_404(self) -> None:
        self.assertEqual(self.call("GET", "/v1/embeddings")[0], 404)


def event_line(event: str, **fields: object) -> str:
    return json.dumps({"ts": "2026-04-01T04:30:00.000Z", "level": "info", "event": event, "source": "backend",
                       **fields}) + "\n"


class EngineTest(EnvMixin):
    """The harness starts a real engine per worker (S0038; BL-0083): the sidecar gets the repo's engine venv, and a
    run whose engine did not start is refused, not handed to a worker."""

    def test_the_sidecar_gets_the_repos_engine_venv(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            argv = harness.backend_argv(root, harness.Dirs.make(root), datetime(2026, 4, 1, 4, 30, tzinfo=UTC))
            self.assertEqual(argv.count("--engine-venv"), 1)
            self.assertEqual(Path(argv[argv.index("--engine-venv") + 1]), REPO / "engine" / ".venv")
        finally:
            root.remove()

    def test_engine_state_reads_the_first_engine_verdict(self) -> None:
        ready = event_line("engine.ready", pid=4242, protocol=1, beancount_version="3.2.0")
        failed = event_line("engine.start_failed", kind="start_failed", detail="no python")
        self.assertEqual(harness.engine_state("not json\n" + ready), ("ready", 4242))
        self.assertEqual(harness.engine_state(failed + ready), ("failed", "start_failed: no python"))
        self.assertIsNone(harness.engine_state(event_line("engine.stderr", line="hello")))
        self.assertIsNone(harness.engine_state(""))
        self.assertIsNone(harness.engine_state(ready[:-10]))  # a line still being written

    def test_an_engine_ready_line_without_a_real_pid_is_not_ready(self) -> None:
        for pid in ("4242", None, True, 0, -1):
            with self.subTest(pid=pid):
                self.assertIsNone(harness.engine_state(event_line("engine.ready", pid=pid)))

    def wait(self, text: str | None, alive: bool = True, timeout: float = 0.3) -> int:
        log = self.base / "app-local-data" / "logs" / "finzytrack.jsonl"
        log.unlink(missing_ok=True)
        if text is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(text)
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()" if alive else "pass"],  # noqa: S603
                                stdin=subprocess.PIPE)
        try:
            if not alive:
                proc.wait(timeout=10)
            return harness.wait_for_engine(log, proc, timeout=timeout)
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=10)

    def test_the_engine_verdict_is_read_from_the_sidecars_log_file(self) -> None:
        dirs = harness.Dirs(self.base / "home", self.base / "app-local-data", self.base / "app-cache",
                            self.base / "workspace")
        self.assertEqual(harness.log_file(dirs), self.base / "app-local-data" / "logs" / "finzytrack.jsonl")

    def test_wait_for_engine_returns_the_engine_pid(self) -> None:
        self.assertEqual(self.wait(event_line("engine.ready", pid=4242)), 4242)

    def test_wait_for_engine_refuses_a_failed_engine_no_verdict_and_a_dead_sidecar(self) -> None:
        cases = {
            "failed": (event_line("engine.start_failed", kind="start_failed", detail="x"), True),
            "no verdict in time": (event_line("engine.stderr", line="still starting"), True),
            "no log file yet": (None, True),
        }
        for name, (text, alive) in cases.items():
            with self.subTest(case=name), self.assertRaises(harness.Refused):
                self.wait(text, alive=alive)

    def test_a_sidecar_that_exited_is_refused_at_once_not_after_the_timeout(self) -> None:
        began = time.monotonic()
        with self.assertRaises(harness.Refused) as caught:
            self.wait("", alive=False, timeout=30.0)
        self.assertIn("exited", str(caught.exception))
        self.assertLess(time.monotonic() - began, 10.0)


@unittest.skipUnless(BACKEND_PY.is_file(), "needs the backend venv (cd backend && uv sync --locked)")
class EndToEndTest(EnvMixin):
    def test_a_run_starts_isolated_and_leaves_nothing_behind(self) -> None:
        proc = subprocess.Popen(  # noqa: S603 (fixed argv: the harness under test)
            [sys.executable, str(REPO / "scripts/acceptance/launcher.py"), "--tz", "America/Los_Angeles"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "TMPDIR": str(self.base)},
        )
        try:
            assert proc.stdout is not None and proc.stdin is not None
            line = proc.stdout.readline()
            self.assertTrue(line.startswith("FT_HARNESS_READY "), line + (proc.stderr.read() if proc.poll() else ""))
            run = json.loads(line.removeprefix("FT_HARNESS_READY "))
            root = Path(run["root"])
            self.assertTrue(harness.copies.within(root, self.base))
            self.assertTrue((Path(run["workspace"]) / "main.beancount").is_file())
            self.assertEqual(Path(run["workspace"]).parent, root)
            self.assertEqual(run["tz"], "America/Los_Angeles")
            self.assertEqual(run["today"], "2026-03-31")
            self.assertEqual(datetime.fromisoformat(run["frozenAt"]).astimezone(UTC),
                             datetime(2026, 4, 1, 4, 30, tzinfo=UTC))
            self.assertIn('"anchor": "2026-03-31"', (Path(run["workspace"]) / "generated-for.json").read_text())
            backend = run["backend"]
            m = re.fullmatch(r"http://127\.0\.0\.1:(\d+)", backend)
            assert m is not None, backend
            port = int(m.group(1))
            self.assertNotEqual(port, harness.OWNER_PORT)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/boot")
            self.assertEqual(conn.getresponse().status, 501)  # the sidecar's placeholder: no routes yet
            conn.close()
            llm = re.fullmatch(r"http://127\.0\.0\.1:(\d+)/v1", run["llm"])
            assert llm is not None, run["llm"]
            conn = http.client.HTTPConnection("127.0.0.1", int(llm.group(1)), timeout=10)
            conn.request("GET", "/v1/models")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
            # S0039: the sidecar's events are in its structured log, not on its stderr.
            self.assertNotIn('"engine.ready"', (root / "sidecar.log").read_text())
            engine = harness.engine_state((root / "app-local-data" / harness.LOG_FILE).read_text())
            assert engine is not None and engine[0] == "ready", engine
            engine_pid = engine[1]
            assert isinstance(engine_pid, int)
            self.assertTrue(pid_alive(engine_pid), "the run's engine is not running")
            proc.stdin.close()  # the worker is done
            self.assertEqual(proc.wait(timeout=30), 0, proc.stderr.read() if proc.stderr else "")
            self.assertFalse(root.exists())
            self.assertTrue(gone(engine_pid), "the run's engine outlived the run")
            with self.assertRaises(OSError):
                http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)

    def test_a_run_refuses_a_zone_without_a_profile(self) -> None:
        r = subprocess.run(  # noqa: S603
            [sys.executable, str(REPO / "scripts/acceptance/launcher.py"), "--tz", "UTC"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
            env={**os.environ, "TMPDIR": str(self.base)}, check=False,
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("FT_HARNESS_READY", r.stdout)
        self.assertEqual([p for p in self.base.iterdir() if p.name.startswith("ft-acceptance-")], [])


if __name__ == "__main__":
    unittest.main()
