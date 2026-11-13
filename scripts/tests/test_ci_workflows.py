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


PIN_LINE = r"^\s*(?:- )?uses: (\S+)(?:\s+#.*)?$"


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
        # S0036 verifier F1: an empty version would key a cache no later version replaces; the step must fail
        self.assertIn('test -n "$version"', version)
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


class PackagedSmokeTest(unittest.TestCase):
    """Each OS of the packaged run proves the smoke test refuses a package without its engine (S0038; EP-29), as it
    already does for one without its interpreter: the engine runs from `sidecar/engine`, beside `sidecar/python`."""

    def setUp(self) -> None:
        block = jobs((REPO / ".github/workflows/packaged.yml").read_text())["package"]
        self.steps = {m.group(1): s for s in re.split(r"(?m)^(?=      - )", block)
                      if (m := re.search(r"name: (macOS|Windows|Linux) ", s))}

    def test_every_os_runs_the_smoke_test_once_more_without_the_engine(self) -> None:
        self.assertEqual(sorted(self.steps), ["Linux", "Windows", "macOS"])
        for os_name, step in self.steps.items():
            with self.subTest(os=os_name):
                self.assertRegex(step, r'mv "(?:[^"]*/sidecar/engine|\$engine_dir)" ')
                self.assertEqual(step.count('--expect-fail "the bundled engine'), 1)
                self.assertEqual(step.count('--expect-fail "cannot start the bundled interpreter"'), 1)
                # the engine-less run comes after the full run and before the interpreter is moved away
                full = re.search(r"smoke_packaged\.py \"\$\w+\"(?: \"\$\{known\[@\]\}\")?\n", step)
                assert full is not None
                engine_away = step.index('--expect-fail "the bundled engine')
                python_away = step.index("sidecar/python")
                self.assertLess(full.start(), engine_away)
                self.assertLess(engine_away, python_away)


def matrix_entries(block: str) -> list[tuple[str, str | None, str]]:
    """(os, known_failure or None when the key is absent, the comment lines above the entry) per `- os:` entry of a
    job's `matrix.include`."""
    lines = block.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "include:")
    out: list[tuple[str, str | None, str]] = []
    comment: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            comment.append(stripped)
        elif m := re.fullmatch(r"- os: ([\w.-]+)(?:\s+#.*)?", stripped):
            out.append((m.group(1), None, "\n".join(comment)))
            comment = []
        elif out and (m := re.fullmatch(r'known_failure: "([^"]*)"', stripped)):
            out[-1] = (out[-1][0], m.group(1), out[-1][2])
        elif stripped and not re.fullmatch(r"[\w-]+: .*", stripped):
            break
        elif stripped and not line.startswith("          "):
            break
    return out


def backlog_row(row_id: str) -> str | None:
    found = re.search(rf"(?m)^\| {row_id} \|.*$", (REPO / "design/backlog.md").read_text())
    return found.group(0) if found else None


class PackagedFixmeTest(unittest.TestCase):
    """S0114 (owner, 2026-11-13: "Narrow it (Recommended)"; S0112 verifier F2): a fixme OS of the packaged run is
    green only when its first smoke run fails with exactly its known failure; any other failure, in that run or in a
    later step, and an unexpected pass, fail the run. No job hides its result (`continue-on-error`), macOS is strict,
    and each known failure is the reason its open M11 row names (ADR-0061 decision 4b)."""

    def setUp(self) -> None:
        self.block = jobs((REPO / ".github/workflows/packaged.yml").read_text())["package"]
        self.steps = {m.group(1): s for s in re.split(r"(?m)^(?=      - )", self.block)
                      if (m := re.search(r"name: (macOS|Windows|Linux) ", s))}

    def test_no_job_hides_its_result(self) -> None:
        self.assertNotIn("continue-on-error", (REPO / ".github/workflows/packaged.yml").read_text())

    def test_macos_is_strict_and_every_known_failure_is_its_open_m11_rows_reason(self) -> None:
        entries = matrix_entries(self.block)
        self.assertEqual(sorted(e[0].split("-")[0] for e in entries), ["macos", "ubuntu", "windows"])
        for os_name, known, comment in entries:
            with self.subTest(os=os_name):
                self.assertIsNotNone(known, "every OS states its known failure (empty: strict)")
                if os_name.startswith("macos"):
                    self.assertEqual(known, "")
                if not known:
                    continue
                rows = re.findall(r"BL-\d{4}", comment)
                self.assertEqual(len(rows), 1, "a known failure cites exactly one backlog row")
                row = backlog_row(rows[0])
                assert row is not None, rows[0]
                cells = [c.strip() for c in row.split("|")]
                self.assertEqual(cells[4:6], ["M11", "open"], rows[0])
                # quoted whole in the row, so a shorter reason (one that also accepts other failures) is refused
                self.assertIn(f'"{known}"', row, "the known failure is the whole reason its row quotes")

    def test_each_oses_first_smoke_run_takes_the_known_failure_and_no_other_run_does(self) -> None:
        self.assertIn("      KNOWN_FAILURE: ${{ matrix.known_failure }}\n", self.block)
        self.assertNotIn("${{ matrix.known_failure }}", "".join(self.steps.values()))  # through env only
        self.assertEqual(sorted(self.steps), ["Linux", "Windows", "macOS"])
        for os_name, step in self.steps.items():
            with self.subTest(os=os_name):
                self.assertIn('known=()\n', step)
                self.assertIn('if [ -n "$KNOWN_FAILURE" ]; then known=(--expect-fail "$KNOWN_FAILURE"); fi\n', step)
                runs = re.findall(r"(?m)^.*smoke_packaged\.py .*$", step)
                self.assertGreaterEqual(len(runs), 3)
                self.assertRegex(runs[0], r'smoke_packaged\.py "\$\w+" "\$\{known\[@\]\}"$')
                for later in runs[1:]:
                    self.assertNotIn("known", later)
                self.assertLess(step.index("known=()"), step.index(runs[0]))

    def test_the_matrix_parser_sees_a_missing_or_strict_entry(self) -> None:  # @regression-guard
        block = ("    strategy:\n      matrix:\n        include:\n          - os: macos-15  # x\n"
                 '            known_failure: ""\n          # fixme (BL-0001)\n          - os: ubuntu-22.04\n'
                 '            known_failure: "boom"\n          - os: windows-2022\n    runs-on: x\n')
        self.assertEqual(matrix_entries(block), [("macos-15", "", ""), ("ubuntu-22.04", "boom", "# fixme (BL-0001)"),
                                                 ("windows-2022", None, "")])


class ActionPinTest(unittest.TestCase):
    def test_a_trailing_comment_does_not_hide_a_uses_line(self) -> None:  # S0036 verifier F2
        text = "      - uses: actions/checkout@main  # x\n"
        self.assertEqual(re.findall(PIN_LINE, text, re.MULTILINE), ["actions/checkout@main"])

    def test_every_remote_action_is_pinned_to_a_major_version_tag(self) -> None:  # @regression-guard
        # The repo's convention (owner/name@vN); a branch name or no ref would follow whatever is pushed there.
        for wf in WORKFLOWS:
            for ref in re.findall(PIN_LINE, wf.read_text(), re.MULTILINE):
                with self.subTest(workflow=wf.name, uses=ref):
                    if ref.startswith("./"):
                        continue
                    self.assertRegex(ref, r"^[\w-]+/[\w-]+@v\d+$")


if __name__ == "__main__":
    unittest.main()
