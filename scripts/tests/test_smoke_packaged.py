"""Meta-tests for the packaged-build smoke harness (`shell/smoke_packaged.py`; spec: `shell/src-tauri/src/smoke.rs`).

A normal run passes only on exit 0 with exactly one `FT_SMOKE_OK` line; the must-not-pass run (`--expect-fail`)
passes only on a non-zero exit with exactly one `FT_SMOKE_FAIL` line giving the expected reason, so a crash, a
hang or a refusal for another cause is never mistaken for either verdict (EP-29).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shell"))

from smoke_packaged import verdict  # noqa: E402

OK = 'FT_SMOKE_OK {"version":"0.4.0-dev.0"}'
FAIL = "FT_SMOKE_FAIL cannot start the bundled interpreter"


class Verdict(unittest.TestCase):
    def test_a_passing_run(self) -> None:
        self.assertTrue(verdict(0, f"log line\n{OK}\n", expect_fail=None)[0])

    def test_runs_that_must_not_pass(self) -> None:
        cases = {
            "exit 0 without a smoke line (a plain launch that quit)": (0, "started\n"),
            "OK line but non-zero exit": (1, OK),
            "FAIL line with exit 0": (0, FAIL),
            "two OK lines": (0, f"{OK}\n{OK}"),
            "OK and FAIL lines": (0, f"{OK}\n{FAIL}"),
            "timeout": (None, OK),
            "OK text not at line start": (0, f"echo {OK}"),
        }
        for name, (code, out) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(verdict(code, out, expect_fail=None)[0])

    def test_an_expected_refusal(self) -> None:
        self.assertTrue(verdict(1, FAIL, expect_fail="cannot start the bundled interpreter")[0])

    def test_expected_refusals_that_must_not_pass(self) -> None:
        cases = {
            "the run passed": (0, OK),
            "crash with no smoke line": (101, "thread 'main' panicked"),
            "timeout": (None, FAIL),
            "FAIL line but exit 0": (0, FAIL),
            "OK line with non-zero exit": (1, OK),
            "a refusal for another reason (the shell's own timeout)": (1, "FT_SMOKE_FAIL no verdict within 120s"),
            "a refusal for another reason (no frontend)": (1, "FT_SMOKE_FAIL the frontend bundle is missing"),
        }
        for name, (code, out) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(verdict(code, out, expect_fail="cannot start the bundled interpreter")[0])

    # S0153 (BL-0263): the self-check follows a failed view probe's reason with its detail, on the same line.
    # `--expect-fail` matches by containment, so a known reason followed by its detail still counts and nothing else
    # does. Phase 2 fixed the AppImage's probe, so no job expects this reason any more; the mechanism stays for a
    # future fixme OS (and the must-not-pass runs use it).
    KNOWN = "the bundled engine failed: engine view: CalledProcessError"
    VIEW_FAILED = "FT_SMOKE_FAIL the bundled engine failed: engine view: "
    DETAIL = '{"exit": 1, "stderr": "Fatal Python error: ...", "stdout": "", "argv": ["~/x"], "cwd": "/", "env": {}}'

    def test_no_job_expects_the_appimages_old_failure(self) -> None:
        """Must not pass on 3179ff4b: the AppImage's smoke run is strict since its view probe got the engine's
        environment, so its old reason is no OS's known failure."""
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/packaged.yml").read_text()
        self.assertNotIn(self.KNOWN, workflow)

    def test_the_known_reason_followed_by_its_detail_is_expected(self) -> None:  # @regression-guard (S0153)
        out = f"FT_SMOKE_FAIL {self.KNOWN} {self.DETAIL}"
        self.assertTrue(verdict(1, out, expect_fail=self.KNOWN)[0])

    def test_other_failures_with_a_detail_are_not_the_known_reason(self) -> None:  # @regression-guard (S0153)
        cases = {
            "the view probe hung": f"{self.VIEW_FAILED}TimeoutExpired {self.DETAIL}",
            "the view output unreadable": f"{self.VIEW_FAILED}JSONDecodeError {self.DETAIL}",
            "the engine did not start, stderr naming the error class": (
                "FT_SMOKE_FAIL the bundled engine failed: start_failed: CalledProcessError in a child"
            ),
            "another cause": "FT_SMOKE_FAIL the bundled engine did not answer a ping",
        }
        for name, out in cases.items():
            with self.subTest(case=name):
                self.assertFalse(verdict(1, out, expect_fail=self.KNOWN)[0])
        with self.subTest(case="the known reason, but the run exited 0"):
            self.assertFalse(verdict(0, f"FT_SMOKE_FAIL {self.KNOWN} {self.DETAIL}", expect_fail=self.KNOWN)[0])


if __name__ == "__main__":
    unittest.main()
