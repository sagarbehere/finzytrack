"""The type check per target platform (S0113): `scripts/pyright_platforms.py` runs the package's own pyright once per
platform in PLATFORMS, with `--pythonplatform`, and passes only when every pass ran and passed.

The cases run the real script against a scratch package whose `.venv/bin/pyright` is a stand-in that records its
arguments and fails for the platform named in FAIL_PLATFORM.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "pyright_platforms.py"
FAKE_PYRIGHT = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
platform = sys.argv[sys.argv.index("--pythonplatform") + 1] if "--pythonplatform" in sys.argv else "none"
with open(Path(__file__).with_name(f"pyright-{platform}.log"), "w") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
failing = os.environ.get("FAIL_PLATFORM") == platform
print(f"{'3 errors' if failing else '0 errors'}, 0 warnings, 0 informations ({platform})")
sys.exit(1 if failing else 0)
"""


class PyrightPlatforms(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.package = Path(self.tmp.name) / "pkg"
        self.bin = self.package / ".venv" / "bin"
        self.bin.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake(self) -> None:
        pyright = self.bin / "pyright"
        pyright.write_text(FAKE_PYRIGHT)
        pyright.chmod(pyright.stat().st_mode | stat.S_IXUSR)

    def run_script(self, fail: str = "") -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k != "FAIL_PLATFORM"}
        if fail:
            env["FAIL_PLATFORM"] = fail
        return subprocess.run([sys.executable, str(SCRIPT), str(self.package)], capture_output=True, text=True,
                              env=env, check=False)

    def platforms(self) -> list[str]:
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import pyright_platforms
        finally:
            sys.path.pop(0)
        return list(pyright_platforms.PLATFORMS)

    def test_every_platform_is_checked_with_the_packages_own_config(self) -> None:
        self.fake()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.platforms(), ["Darwin", "Linux"])
        for platform in self.platforms():
            log = (self.bin / f"pyright-{platform}.log").read_text()
            self.assertEqual(log, f"--pythonplatform {platform}\n", "only the platform: the config stays the pinned one")
            self.assertIn(f"({platform})", r.stdout, "each pass's output is shown")

    def test_one_failing_platform_fails_the_check_and_names_it(self) -> None:
        """Must not pass: a pass with errors on any one platform."""
        self.fake()
        for platform in self.platforms():
            with self.subTest(platform):
                r = self.run_script(fail=platform)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(f"pyright --pythonplatform {platform}: FAILED", r.stderr)

    def test_a_missing_pyright_fails_closed(self) -> None:
        """Must not pass: no pyright in the package's environment is a failure, never a skipped check."""
        r = self.run_script()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("missing", r.stderr)


class Wired(unittest.TestCase):
    """The push and the manual CI run check both packages per platform, and no single-platform pyright run is left
    (the hook's bytes are pinned in test_hook_pins)."""

    def test_pre_push_and_ci_run_the_per_platform_check_for_both_packages(self) -> None:
        hook = (REPO / ".githooks/pre-push").read_text()
        for line in ('  run "pyright" python3 scripts/pyright_platforms.py backend\n',
                     '  run "pyright (engine)" python3 scripts/pyright_platforms.py engine\n'):
            self.assertIn(line, hook)
        ci = (REPO / ".github/workflows/ci.yml").read_text()
        self.assertEqual(ci.count("run: python3 ../scripts/pyright_platforms.py .\n"), 2, "the backend and engine jobs")
        for text, where in ((hook, "pre-push"), (ci, "ci.yml")):
            self.assertNotIn(".venv/bin/pyright\n", text, f"{where}: a pyright run for one platform only")
            self.assertNotIn('.venv/bin/pyright"', text, f"{where}: a pyright run for one platform only")


if __name__ == "__main__":
    unittest.main()
