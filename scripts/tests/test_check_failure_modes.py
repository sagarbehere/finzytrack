"""The failure-mode spec check (S0041, narrowed in S0043; scripts/check_failure_modes.py, design/specs/failure-modes.md).

One must-not-pass per rule the check keeps; the rules S0041 had and S0043 dropped (owner-approved, IMP-0032) are
pinned as passing. The kept rules' tests are @regression-guard: S0043 changed how they are written, not what they
refuse."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_failure_modes as cfm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_failure_modes.py"

BACKLOG = """\
| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0901 | open row | WS-QA | M2 | open | `x` |
| BL-0902 | closed row | WS-QA | M2 | done | `x` |
| BL-0903 | engine row | WS-QA | M1b (agent) | in-progress | `x` |
| L9-99 | a finding | Low | WS-UI | M5 | open | — | `review:L9` |
"""

STATECHART = """\
## Notes

| From | Event | To |
|---|---|---|
| `Ready` | `TimedOut` | `Closed` |

## Transitions

| From | Event | To | Effects | Note |
|---|---|---|---|---|
| `Busy` | `Ended` | `Crashed`, `Unavailable` | `Kill`, `Log`, `Fail` | a stop |
| `Busy` | `TimedOut` | `Crashed`, `Unavailable` | `Kill`, `Log`, `Fail` | a stop |
| `Ready` | `Exited` | `Crashed`, `Unavailable` | `Kill`, `Log` | a stop |
| `Ready` | `Close` | `Closed` | `Shutdown`, `Log` |  |
"""

TEST_PY = "def helper():\n    pass\n\n\ndef test_one():\n    pass\n\n\nclass Suite:\n    def test_two(self):\n        pass\n"
TEST_RS = "fn helper() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn rust_case() {}\n}\n"


def row(rid: str, failure: str, test: str = "`t/test_x.py::test_one`; BL-0901 (M2)",
        states: str = "`Busy` → `Crashed` on `Ended`") -> str:
    """One row in the real spec's shape: who, data and user cells, the engine's States cell, the Test cell."""
    return "| " + " | ".join([rid, failure, "a", "b", "c", *([states] if rid[0] == "E" else []), test]) + " |"


def spec(overrides: dict[str, str] | None = None, *, drop: str | None = None, extra: str = "") -> str:
    """A complete, valid spec; `overrides` replaces whole rows by ID, `drop` leaves one out."""
    lines = []
    for letter in cfm.PROGRAMS:
        for n, failure in enumerate(cfm.FAILURES, 1):
            rid = f"{letter}{n}"
            if rid != drop:
                lines.append((overrides or {}).get(rid) or row(rid, failure))
    return "| ID | Failure |\n|---|---|\n" + "\n".join(lines) + "\n" + extra


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / "t").mkdir(parents=True)
        (self.repo / "t" / "test_x.py").write_text(TEST_PY)
        (self.repo / "t" / "smoke.rs").write_text(TEST_RS)
        (Path(self._tmp.name) / "test_outside_repo.py").write_text(TEST_PY)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def problems(self, text: str) -> list[str]:
        return cfm.check(text, BACKLOG, STATECHART, self.repo)[0]

    def refused(self, text: str, fragment: str) -> None:
        problems = self.problems(text)
        self.assertTrue(any(fragment in p for p in problems), (fragment, problems))

    def refused_test(self, test: str, fragment: str) -> None:
        self.refused(spec({"S1": row("S1", "crash", test)}), fragment)


class Passes(Fixture):
    def test_every_reference_shape_passes(self) -> None:  # @regression-guard
        text = spec({
            "M1": row("M1", "crash", "`t/test_x.py::Suite::test_two`; `t/smoke.rs::rust_case`; L9-99 (M5)"),
            "M2": row("M2", "hang", "BL-0903 (M1b)"),
            "M3": row("M3", "slow", "same as M1"),
            "M5": row("M5", "parent gone", "n/a: the launcher is its parent"),
            "E2": row("E2", "hang", states="`Busy` → `Crashed` or `Unavailable` on `Ended` or `TimedOut`"),
            "E5": row("E5", "parent gone", states="n/a: the client is gone with it"),
            "E7": row("E7", "told to stop", states="`Ready` → `Closed` on `Close`; `Ready` → `Unavailable` on `Exited`"),
        }, extra="\n| Class | HTTP |\n|---|---|\n| Invalid | 422 |\n\nSee row E1 and `E2`.\n")
        problems, counts = cfm.check(text, BACKLOG, STATECHART, self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(sum(counts.values()), 28)
        self.assertEqual((counts["same as"], counts["n/a"], counts["planned"]), (1, 1, 1))

    def test_formatting_is_not_policed(self) -> None:  # S0043, the loosening (IMP-0032)
        text = spec({
            "S2": row("S2", "slow"),  # an ID whose number is another failure's
            "S3": row("S3", "hang", "same as S2"),  # a same-as row whose other cells say something else
            "S5": row("S5", "parent gone", "n/a: none"),  # a short n/a reason
            "E5": row("E5", "parent gone", states="n/a: gone"),
            "W1": "| W1 | crash | a | b | c | d | `t/test_x.py::test_one` |",  # another cell count
        }, extra=row("W9", "exploded") + "\n")  # a failure the spec does not list
        self.assertEqual(self.problems(text), [])

    def test_the_real_spec_passes(self) -> None:  # @regression-guard
        r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)  # noqa: S603
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("28 rows", r.stdout)


class MustNotPass(Fixture):
    """@regression-guard: each rule S0043 kept, with what it refuses."""

    def test_a_missing_pair_is_refused(self) -> None:
        self.refused(spec(drop="W4"), "no row for the frontend webview: killed for memory")

    def test_a_second_row_for_a_pair_is_refused(self) -> None:
        self.refused(spec(extra=row("S8", "crash") + "\n"), "2 rows for the Tauri shell: crash")

    def test_a_row_of_an_unknown_program_is_refused(self) -> None:  # S0041 verifier F1
        for rid in ("X1", "EE1", "e1"):
            with self.subTest(rid=rid):
                self.refused(spec(extra=row(rid, "crash") + "\n"), f"{rid}: not a row of a known program")

    def test_a_row_hidden_in_a_code_fence_or_a_comment_does_not_count(self) -> None:  # S0041 verifier F5
        e1 = row("E1", "crash")
        for hidden in (f"```\n{e1}\n```", f"~~~\n{e1}\n~~~", f"<!--\nnote\n{e1}\n-->", f"<!-- {e1} -->"):
            with self.subTest(hidden=hidden):
                self.refused(spec(drop="E1", extra=hidden + "\n"), "no row for the ledger engine: crash")

    def test_a_fence_and_a_comment_never_mask_each_other(self) -> None:  # S0043 verifier F1
        e7 = row("E7", "told to stop")
        extra = f"\n```\n<!-- a note\n```\n\nThen it closes with -->.\n\n```\n{e7}\n```\n"
        self.refused(spec(drop="E7", extra=extra), "no row for the ledger engine: told to stop")
        extra = f"\n<!-- a comment that shows\n```\n-->\n\n```\n{e7}\n```\n"
        self.refused(spec(drop="E7", extra=extra), "no row for the ledger engine: told to stop")
        self.assertEqual([line for _, line in cfm.visible_lines("a\n```\n<!-- b\n```\nc\n<!--\n```\n-->\nd\n")],
                         ["a", "c", "d"])

    def test_a_test_cell_that_is_not_references_is_refused(self) -> None:
        for test in ("", "n/a:", "covered by the smoke run", "`t/test_x.py::test_one` (mostly)", "BL-0901",
                     "same as S2; BL-0901 (M2)"):
            with self.subTest(test=test):
                self.refused_test(test, "not a test or backlog reference")

    def test_a_missing_test_file_is_refused(self) -> None:
        self.refused_test("`t/test_gone.py::test_one`", "no file t/test_gone.py")

    def test_a_test_outside_the_repo_is_refused(self) -> None:  # S0041 verifier attack 9
        outside = Path(self._tmp.name) / "test_outside_repo.py"
        for path in ("../test_outside_repo.py", "t/../../test_outside_repo.py", str(outside)):
            with self.subTest(path=path):
                self.refused_test(f"`{path}::test_one`", "outside the repo")

    def test_a_missing_test_or_class_is_refused(self) -> None:
        self.refused_test("`t/test_x.py::test_gone`", "no test test_gone")
        self.refused_test("`t/test_x.py::Suite::test_one`", "no test test_one")
        self.refused_test("`t/test_x.py::Other::test_two`", "no class Other")

    def test_a_name_in_a_comment_or_string_is_not_a_test(self) -> None:  # S0043 verifier F3 (restored from S0041)
        (self.repo / "t" / "test_y.py").write_text('# def test_ghost(\nX = "def test_ghost("\n')
        self.refused_test("`t/test_y.py::test_ghost`", "no test test_ghost")

    def test_a_function_that_is_not_a_test_is_refused(self) -> None:
        self.refused_test("`t/test_x.py::helper`", "helper is not a test name")
        self.refused_test("`t/smoke.rs::helper`", "no #[test] fn helper")
        (self.repo / "t" / "a.spec.ts").write_text("it('test_one')\n")
        self.refused_test("`t/a.spec.ts::test_one`", "only Python tests and Rust #[test] fns")

    def test_an_unknown_backlog_row_is_refused(self) -> None:
        self.refused_test("BL-0999 (M2)", "no backlog row BL-0999")

    def test_a_closed_backlog_row_is_refused(self) -> None:
        self.refused_test("BL-0902 (M2)", "BL-0902 is done: cite the test")

    def test_a_backlog_row_with_another_milestone_is_refused(self) -> None:
        self.refused_test("BL-0901 (M1b)", "BL-0901 is due in M2, not M1b")

    def test_same_as_a_row_that_cites_no_test_is_refused(self) -> None:  # S0041 verifier F4
        self.refused(spec({"S3": row("S3", "slow", "same as S9")}), "same as S9: no such row")
        self.refused(spec({"W3": row("W3", "slow", "same as E2")}), "same as E2: a row of another program")
        text = spec({"S3": row("S3", "slow", "same as S2"), "S2": row("S2", "hang", "same as S1")})
        self.refused(text, "same as S2, which cites no test itself")
        text = spec({"S3": row("S3", "slow", "same as S5"), "S5": row("S5", "parent gone", "n/a: no parent")})
        self.refused(text, "same as S5, which cites no test itself")

    def test_a_states_cell_that_is_not_transitions_is_refused(self) -> None:  # S0041 verifier F3
        for states in ("", "crashes", "`Busy` → `Crashed`", "`Busy` → `Crashed` on `Ended` (mostly)",
                       "`Busy` -> `Crashed` on `Ended`", "`Busy` → `Crashed` or on `Ended`"):
            with self.subTest(states=states):
                self.refused(spec({"E1": row("E1", "crash", states=states)}), "not a transition")
        self.refused(spec({"E1": "| E1 | crash | `t/test_x.py::test_one` |"}), "not a transition")

    def test_a_transition_the_statechart_lacks_is_refused(self) -> None:  # S0041 verifier F3
        cases = {
            "`Ready` → `Closed` on `TimedOut`": "`Ready` on `TimedOut` is not in the statechart",  # only in Notes
            "`Busy` → `Closed` on `Ended`": "`Busy` on `Ended` does not lead to `Closed`",
            "`Busy` → `Crashed` or `Dead` on `Ended`": "`Busy` on `Ended` does not lead to `Dead`",
            "`Busy` → `Crashed` on `Ended` or `Exited`": "`Busy` on `Exited` is not in the statechart",
            "`Busy` → `Crashed` on `Ended`; `Ghost` → `Busy` on `Ended`": "`Ghost` on `Ended` is not in the statechart",
        }
        for states, fragment in cases.items():
            with self.subTest(states=states):
                self.refused(spec({"E1": row("E1", "crash", states=states)}), fragment)

    def test_the_cli_fails_on_a_refused_spec(self) -> None:
        bad = self.repo / "bad.md"
        bad.write_text(spec(drop="S1"))
        r = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True, check=False)  # noqa: S603
        self.assertEqual(r.returncode, 1)
        self.assertIn("no row for the Tauri shell: crash", r.stderr)


class Sources(unittest.TestCase):
    """@regression-guard: the backlog and statechart readers the rules depend on."""

    def test_status_and_milestone_are_read_from_either_table_shape(self) -> None:
        rows = cfm.backlog_rows(BACKLOG)
        self.assertEqual((rows["BL-0901"], rows["BL-0903"], rows["L9-99"]), (("M2", "open"), ("M1b", "in-progress"),
                                                                           ("M5", "open")))

    def test_the_real_statechart_has_its_transitions(self) -> None:
        real = cfm.statechart_transitions(cfm.STATECHART.read_text(encoding="utf-8"))
        self.assertEqual(len(real), 48)  # S0040: 34 rows in ROWS; S0057: 48 (no TimedOut; our ends, Finishing)
        self.assertNotIn(("Busy", "TimedOut"), real)
        self.assertEqual(real[("Busy", "Cancelled")], frozenset({"Idle", "Busy"}))  # Busy: past the commit point
        self.assertEqual(real[("Busy", "Ended")], frozenset({"Crashed", "Unavailable"}))


if __name__ == "__main__":
    unittest.main()
