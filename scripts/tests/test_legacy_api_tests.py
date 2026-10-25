"""Meta-tests for the S12 runner (legacy API tests out of process): outcome comparison, the backend copy, offline env."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "oracle"))

import launch_legacy  # noqa: E402
import legacy_api_tests as lat  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.api.test_a" name="test_ok" />
<testcase classname="tests.api.test_a" name="test_bad"><failure message="assert 1 == 2">x</failure></testcase>
<testcase classname="tests.api.test_a" name="test_err"><error message="fixture boom">x</error></testcase>
<testcase classname="tests.test_b.TestK" name="test_skip"><skipped message="no" /></testcase>
<testcase classname="tests.test_c" name="test_param[a-1]" />
</testsuite></testsuites>
"""


class OutcomeTest(unittest.TestCase):
    def test_parse_junit_maps_node_ids_to_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.xml"
            p.write_text(JUNIT, encoding="utf-8")
            got = lat.parse_junit(p)
        self.assertEqual(got, {
            "tests/api/test_a.py::test_ok": ("passed", ""), "tests/api/test_a.py::test_bad": ("failed", "assert 1 == 2"),
            "tests/api/test_a.py::test_err": ("error", "fixture boom"), "tests/test_b.py::TestK::test_skip": ("skipped", "no"),
            "tests/test_c.py::test_param[a-1]": ("passed", "")})

    def test_only_shimmed_passing_tests_count(self) -> None:
        inproc = {"t1": ("passed", ""), "t2": ("passed", ""), "t3": ("passed", ""), "t4": ("failed", "m"), "t5": ("passed", "")}
        outproc = {"t1": ("passed", ""), "t2": ("passed", ""), "t3": ("failed", "assert"), "t4": ("passed", "")}
        s = lat.compare_outcomes(inproc, outproc, api={"t1", "t2", "t3", "t4"}, shimmed={"t1", "t3", "t4"})
        self.assertEqual(s["denominator"], 3)  # t4 does not pass in process; t5 is not API-level
        self.assertEqual(s["passing_out_of_process"], 1)  # t2 passed but the shim never served it
        self.assertEqual(s["not_shimmed"], ["t2"])
        self.assertEqual(s["failing_out_of_process"], {"t3": "failed: assert"})
        self.assertAlmostEqual(s["ratio"], 1 / 3)


class CopyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.legacy = base / "legacy"
        backend = self.legacy / "backend"
        for rel, text in {"app/__init__.py": "", "app/__pycache__/x.pyc": "b", "tests/test_x.py": "def test(): pass\n",
                          "pyproject.toml": "", "requirements.txt": "", "resources/seed.txt": "s", "scripts/g.py": ""}.items():
            (backend / rel).parent.mkdir(parents=True, exist_ok=True)
            (backend / rel).write_text(text, encoding="utf-8")
        (self.legacy / "VERSION").write_text("0.3.0\n", encoding="utf-8")
        self.old = os.environ.get("FT_LEGACY_ROOT")
        os.environ["FT_LEGACY_ROOT"] = str(self.legacy)
        self.base = base

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("FT_LEGACY_ROOT", None)
        else:
            os.environ["FT_LEGACY_ROOT"] = self.old
        self.tmp.cleanup()

    def test_copy_skips_bytecode_and_leaves_the_source_alone(self) -> None:
        before = sorted(p.read_bytes() for p in self.legacy.rglob("*") if p.is_file())
        dst = lat.copy_backend(self.base / "work")
        self.assertTrue((dst / "tests/test_x.py").is_file())
        self.assertFalse((dst / "app/__pycache__").exists())
        self.assertEqual(sorted(p.read_bytes() for p in self.legacy.rglob("*") if p.is_file()), before)

    def test_copy_refuses_a_destination_inside_the_legacy_worktree(self) -> None:
        with self.assertRaises(ValueError):
            lat.copy_backend(self.legacy / "scratch")


class OfflineTest(unittest.TestCase):
    def test_outbound_http_goes_to_an_unreachable_proxy_and_loopback_is_exempt(self) -> None:
        env = launch_legacy.offline_env({"HTTPS_PROXY": "http://real-proxy:8080", "PATH": "/bin"})
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            self.assertEqual(env[k], launch_legacy.DEAD_PROXY)
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertIn("localhost", env["no_proxy"])
        self.assertEqual(env["PATH"], "/bin")


if __name__ == "__main__":
    unittest.main()
