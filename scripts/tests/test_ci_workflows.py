"""The manual CI workflows (ADR-0060; S0036): job time limits are hang backstops, not budgets, and the frontend job
does not download the Playwright browsers again when the Playwright version has not changed.

The workflows are read as text: the meta-tests run on the system Python, which has no YAML parser. The job blocks
are the lines between two job keys (two-space indent under `jobs:`), which is how every workflow here is written.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = [REPO / ".github" / "workflows" / n for n in ("ci.yml", "nightly.yml", "packaged.yml")]
# A backstop far above any normal run (owner rule, S0034: no fixed budget that growth outruns). The frontend job was
# cancelled at its old 15-minute limit in the manual CI run of 2026-10-26.
BACKSTOP_MIN = 60


def jobs(text: str) -> dict[str, str]:
    """Job name -> the text of its block."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line == "jobs:\n")
    out: dict[str, str] = {}
    name = ""
    for line in lines[start + 1:]:
        m = re.fullmatch(r"  ([\w-]+):\n", line)
        if m:
            name = m.group(1)
            out[name] = ""
        elif name:
            out[name] += line
    return out


def job_timeout(block: str) -> int | None:
    found = re.findall(r"^    timeout-minutes: (\d+)$", block, re.MULTILINE)
    return int(found[0]) if len(found) == 1 else None


class TimeoutTest(unittest.TestCase):
    def test_every_job_has_one_hang_backstop_far_above_a_normal_run(self) -> None:
        seen = 0
        for wf in WORKFLOWS:
            text = wf.read_text()
            self.assertRegex(text, r"(?m)^#.*timeout-minutes.*hang backstop", f"{wf.name}: say what the limit is")
            for name, block in jobs(text).items():
                with self.subTest(workflow=wf.name, job=name):
                    if re.search(r"^    uses: \./", block, re.MULTILINE):
                        continue  # a called workflow: its own jobs carry the limit (GitHub refuses one here)
                    seen += 1
                    minutes = job_timeout(block)
                    self.assertIsNotNone(minutes, "exactly one job-level timeout-minutes")
                    assert minutes is not None
                    self.assertGreaterEqual(minutes, BACKSTOP_MIN)
        self.assertGreaterEqual(seen, 8)  # ci.yml 6, nightly.yml 1, packaged.yml 1

    def test_the_parser_sees_a_short_or_missing_limit(self) -> None:  # @regression-guard
        # Must not pass: a job with a budget-like limit, or none, or two.
        self.assertEqual(job_timeout("    runs-on: x\n    timeout-minutes: 15\n"), 15)
        self.assertIsNone(job_timeout("    runs-on: x\n"))
        self.assertIsNone(job_timeout("    timeout-minutes: 60\n    timeout-minutes: 60\n"))
        # a step-level limit is not the job's
        self.assertIsNone(job_timeout("    steps:\n      - run: x\n        timeout-minutes: 90\n"))
        self.assertEqual(sorted(jobs("on: x\njobs:\n  a:\n    x: 1\n  b-c:\n    y: 2\n")), ["a", "b-c"])


class PlaywrightCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.block = jobs((REPO / ".github/workflows/ci.yml").read_text())["frontend"]

    def step(self, needle: str) -> str:
        """The step (from its `- ` line to the next) that contains *needle*; exactly one."""
        steps = re.split(r"(?m)^(?=      - )", self.block)
        hits = [s for s in steps if needle in s]
        self.assertEqual(len(hits), 1, needle)
        return hits[0]

    def test_the_browsers_are_cached_by_the_exact_playwright_version(self) -> None:
        version = self.step("require('@playwright/test/package.json').version")
        self.assertIn("    id: pw\n", version)
        self.assertIn('>> "$GITHUB_OUTPUT"', version)
        cache = self.step("uses: actions/cache@")
        self.assertIn("    id: pw-cache\n", cache)
        self.assertIn("path: ~/.cache/ms-playwright\n", cache)
        self.assertRegex(cache, r"key: playwright-\$\{\{ runner\.os \}\}-\$\{\{ steps\.pw\.outputs\.version \}\}-")
        self.assertNotIn("restore-keys", cache)  # another version's browsers are never restored
        order = [self.block.index(s) for s in ("pnpm install --frozen-lockfile", "id: pw\n", "id: pw-cache\n",
                                                "playwright install --with-deps", "pnpm -s e2e")]
        self.assertEqual(order, sorted(order))

    def test_a_hit_installs_only_the_system_packages_and_a_miss_everything(self) -> None:
        miss = self.step("playwright install --with-deps chromium webkit")
        self.assertIn("if: steps.pw-cache.outputs.cache-hit != 'true'", miss)
        hit = self.step("playwright install-deps chromium webkit")
        self.assertIn("if: steps.pw-cache.outputs.cache-hit == 'true'", hit)


class ActionPinTest(unittest.TestCase):
    def test_every_remote_action_is_pinned_to_a_major_version_tag(self) -> None:  # @regression-guard
        # The repo's convention (owner/name@vN); a branch name or no ref would follow whatever is pushed there.
        for wf in WORKFLOWS:
            for ref in re.findall(r"^\s*(?:- )?uses: (\S+)$", wf.read_text(), re.MULTILINE):
                with self.subTest(workflow=wf.name, uses=ref):
                    if ref.startswith("./"):
                        continue
                    self.assertRegex(ref, r"^[\w-]+/[\w-]+@v\d+$")


if __name__ == "__main__":
    unittest.main()
