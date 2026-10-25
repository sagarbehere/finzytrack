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
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "acceptance"))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))

import guards  # noqa: E402
import launcher as harness  # noqa: E402

BACKEND_PY = REPO / "backend" / ".venv" / "bin" / "python"


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
        for parent in (REPO, REPO / "build", self.live, self.legacy, Path(str(self.live).upper()), Path("/")):
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

    def test_a_made_folder_that_was_replaced_is_refused(self) -> None:
        root = harness.TempRoot(parent=self.base)
        try:
            d = root.make_dir("workspace")
            d.rmdir()
            d.mkdir()  # same path, another folder
            with self.assertRaises(harness.Refused):
                root.check_own(d)
            d.rmdir()
            d.symlink_to(self.live)  # same path, now a link to the live data folder
            with self.assertRaises(harness.Refused):
                root.check_own(d)
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
            proc.stdin.close()  # the worker is done
            self.assertEqual(proc.wait(timeout=30), 0, proc.stderr.read() if proc.stderr else "")
            self.assertFalse(root.exists())
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
