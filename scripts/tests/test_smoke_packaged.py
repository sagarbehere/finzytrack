"""Meta-tests for the packaged-build smoke harness (`shell/smoke_packaged.py`; spec: `shell/src-tauri/src/smoke.rs`).

A normal run passes only on exit 0 with exactly one `FT_SMOKE_OK` line; the must-not-pass run (`--expect-fail`)
passes only on a non-zero exit with exactly one `FT_SMOKE_FAIL` line, so a crash or a hang is never mistaken for
either verdict (EP-29).
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
        self.assertTrue(verdict(0, f"log line\n{OK}\n", expect_fail=False)[0])

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
                self.assertFalse(verdict(code, out, expect_fail=False)[0])

    def test_an_expected_refusal(self) -> None:
        self.assertTrue(verdict(1, FAIL, expect_fail=True)[0])

    def test_expected_refusals_that_must_not_pass(self) -> None:
        cases = {
            "the run passed": (0, OK),
            "crash with no smoke line": (101, "thread 'main' panicked"),
            "timeout": (None, FAIL),
            "FAIL line but exit 0": (0, FAIL),
            "OK line with non-zero exit": (1, OK),
        }
        for name, (code, out) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(verdict(code, out, expect_fail=True)[0])


if __name__ == "__main__":
    unittest.main()
