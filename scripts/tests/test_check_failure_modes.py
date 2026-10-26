"""The failure-mode spec check (S0041; scripts/check_failure_modes.py, design/specs/failure-modes.md)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_failure_modes as cfm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

BACKLOG = """\
| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0901 | open row | WS-QA | M2 | open | `x` |
| BL-0902 | closed row | WS-QA | M2 | done | `x` |
| BL-0903 | engine row | WS-QA | M1b (agent) | in-progress | `x` |
| L9-99 | a finding | Low | WS-UI | M5 | open | — | `review:L9` |
"""

STATECHART = """\
## States

| State | Meaning |
|---|---|
| `Ready` | ready |
| `Crashed` | crashed |

## Events

| Event | Meaning |
|---|---|
| `Ended` | ended |
"""

TEST_PY = """\
def helper():
    pass


def test_one():
    pass


class Suite:
    def test_two(self):
        pass


def test_outside():
    pass
"""

TEST_RS = """\
fn helper() {}

#[cfg(test)]
mod tests {
    #[test]
    fn rust_case() {}
}
"""


def spec(overrides: dict[str, str] | None = None, *, drop: str | None = None, extra: str = "") -> str:
    """A complete, valid spec; `overrides` replaces whole rows by ID, `drop` leaves one out."""
    lines: list[str] = []
    for letter in cfm.PROGRAMS:
        lines += ["", "| ID | Failure | Notes | Test |" if letter != "E" else "| ID | Failure | Notes | States | Test |"]
        lines.append("|---|---|---|---|" if letter != "E" else "|---|---|---|---|---|")
        for n, failure in enumerate(cfm.FAILURES, 1):
            rid = f"{letter}{n}"
            if rid == drop:
                continue
            if overrides and rid in overrides:
                lines.append(overrides[rid])
            elif letter == "E":
                lines.append(f"| {rid} | {failure} | x | `Busy` → `Crashed` on `Ended` | `t/test_x.py::test_one` |")
            else:
                lines.append(f"| {rid} | {failure} | x | `t/test_x.py::test_one`; BL-0901 (M2) |")
    return "\n".join(lines) + "\n" + extra


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "t").mkdir()
        (self.repo / "t" / "test_x.py").write_text(TEST_PY)
        (self.repo / "t" / "smoke.rs").write_text(TEST_RS)
        self.chart = STATECHART.replace("| `Crashed` |", "| `Busy` | busy |\n| `Crashed` |")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def problems(self, text: str) -> list[str]:
        return cfm.check(text, BACKLOG, self.chart, self.repo)[0]

    def refused(self, text: str, fragment: str) -> None:
        problems = self.problems(text)
        self.assertTrue(any(fragment in p for p in problems), (fragment, problems))


class Passes(Fixture):
    def test_a_complete_spec_passes(self) -> None:
        problems, counts = cfm.check(spec(), BACKLOG, self.chart, self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(sum(counts.values()), 28)

    def test_every_reference_shape_passes(self) -> None:
        text = spec(
            {
                "M1": "| M1 | crash | x | `t/test_x.py::Suite::test_two`; `t/smoke.rs::rust_case`; L9-99 (M5) |",
                "M2": "| M2 | hang | x | BL-0903 (M1b) |",
                "M3": "| M3 | slow | x | same as M1 |",
                "M5": "| M5 | parent gone | x | n/a: the parent is the launcher here |",
                "E5": "| E5 | parent gone | x | n/a: the client is gone with it | BL-0901 (M2) |",
            }
        )
        problems, counts = cfm.check(text, BACKLOG, self.chart, self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(counts["same as"], 1)
        self.assertEqual(counts["n/a"], 1)
        self.assertEqual(counts["planned"], 2)

    def test_the_real_spec_passes(self) -> None:
        r = subprocess.run(  # noqa: S603 (this interpreter running the check under test)
            [sys.executable, str(ROOT / "scripts" / "check_failure_modes.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("28 rows", r.stdout)


class MustNotPass(Fixture):
    def test_a_missing_pair_is_refused(self) -> None:
        self.refused(spec(drop="W4"), "no row for the frontend webview: killed for memory")

    def test_a_duplicated_row_is_refused(self) -> None:
        self.refused(spec(extra="| S1 | crash | x | `t/test_x.py::test_one` |\n"), "appears twice")

    def test_a_second_row_for_a_pair_is_refused(self) -> None:
        self.refused(spec(extra="| S8 | crash | x | `t/test_x.py::test_one` |\n"), "2 rows for the Tauri shell: crash")

    def test_an_id_whose_number_is_another_failures_is_refused(self) -> None:
        text = spec({"S2": "| S2 | slow | x | `t/test_x.py::test_one` |", "S3": "| S3 | hang | x | same as S2 |"})
        self.refused(text, "S2 is 'slow', which is number 3")

    def test_an_unknown_failure_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | exploded | x | `t/test_x.py::test_one` |"}), "unknown failure 'exploded'")

    def test_an_empty_test_cell_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x |  |"}), "the Test cell is empty")

    def test_prose_in_the_test_cell_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | covered by the smoke run |"}), "not a test or backlog reference")
        self.refused(
            spec({"S1": "| S1 | crash | x | `t/test_x.py::test_one` (mostly) |"}), "not a test or backlog reference"
        )

    def test_a_backlog_id_without_its_milestone_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | BL-0901 |"}), "not a test or backlog reference")

    def test_a_missing_test_file_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_gone.py::test_one` |"}), "no file t/test_gone.py")

    def test_a_missing_test_name_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_x.py::test_gone` |"}), "no test test_gone")

    def test_a_helper_that_is_not_a_test_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_x.py::helper` |"}), "helper is not a test name")
        self.refused(spec({"S1": "| S1 | crash | x | `t/smoke.rs::helper` |"}), "no #[test] fn helper")

    def test_a_test_named_in_the_wrong_class_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_x.py::Suite::test_outside` |"}), "no test test_outside")
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_x.py::Other::test_two` |"}), "no class Other")

    def test_a_name_in_a_comment_or_string_is_not_a_test(self) -> None:
        (self.repo / "t" / "test_y.py").write_text('# def test_ghost(\nX = "def test_ghost("\n')
        self.refused(spec({"S1": "| S1 | crash | x | `t/test_y.py::test_ghost` |"}), "no test test_ghost")

    def test_an_unknown_kind_of_test_file_is_refused(self) -> None:
        (self.repo / "t" / "a.spec.ts").write_text("it('test_one')\n")
        self.refused(spec({"S1": "| S1 | crash | x | `t/a.spec.ts::test_one` |"}), "only Python and Rust tests")

    def test_an_unknown_backlog_row_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | BL-0999 (M2) |"}), "no backlog row BL-0999")

    def test_a_closed_backlog_row_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | BL-0902 (M2) |"}), "BL-0902 is done: cite the test")

    def test_a_backlog_row_with_another_milestone_is_refused(self) -> None:
        self.refused(spec({"S1": "| S1 | crash | x | BL-0901 (M1b) |"}), "BL-0901 is due in M2, not M1b")

    def test_same_as_a_missing_row_or_a_chain_is_refused(self) -> None:
        self.refused(spec({"S3": "| S3 | slow | x | same as S9 |"}), "same as S9: no such row")
        text = spec({"S3": "| S3 | slow | x | same as S2 |", "S2": "| S2 | hang | x | same as S1 |"})
        self.refused(text, "same as S2, which is itself 'same as S1'")
        text = spec({"S3": "| S3 | slow | x | same as S5 |", "S5": "| S5 | parent gone | x | n/a: the launcher is its parent |"})
        self.refused(text, "same as S5, which is itself 'n/a'")

    def test_same_as_or_n_a_beside_other_references_is_refused(self) -> None:
        self.refused(spec({"S3": "| S3 | slow | x | same as S2; BL-0901 (M2) |"}), "not a test or backlog reference")

    def test_n_a_without_a_reason_is_refused(self) -> None:
        self.refused(spec({"S5": "| S5 | parent gone | x | n/a: none |"}), "n/a needs a reason")

    def test_an_engine_row_naming_no_state_is_refused(self) -> None:
        self.refused(
            spec({"E1": "| E1 | crash | x | on `Ended` | `t/test_x.py::test_one` |"}), "names no state of the statechart"
        )
        self.refused(spec({"E1": "| E1 | crash | x | crashes | `t/test_x.py::test_one` |"}), "names no state")

    def test_an_engine_row_naming_an_unknown_state_is_refused(self) -> None:
        self.refused(
            spec({"E1": "| E1 | crash | x | `Ready` → `Dead` | `t/test_x.py::test_one` |"}),
            "'Dead' is not a state or event",
        )

    def test_an_engine_states_n_a_without_a_reason_is_refused(self) -> None:
        self.refused(spec({"E5": "| E5 | parent gone | x | n/a: gone | `t/test_x.py::test_one` |"}), "n/a needs a reason")

    def test_the_cli_fails_on_a_refused_spec(self) -> None:
        bad = self.repo / "bad.md"
        bad.write_text(spec(drop="S1"))
        r = subprocess.run(  # noqa: S603 (this interpreter running the check under test)
            [sys.executable, str(ROOT / "scripts" / "check_failure_modes.py"), str(bad)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("no row for the Tauri shell: crash", r.stderr)


class Backlog(unittest.TestCase):
    def test_status_and_milestone_are_read_from_either_table_shape(self) -> None:
        rows = cfm.backlog_rows(BACKLOG)
        self.assertEqual(rows["BL-0901"], ("M2", "open"))
        self.assertEqual(rows["BL-0903"], ("M1b", "in-progress"))
        self.assertEqual(rows["L9-99"], ("M5", "open"))

    def test_the_statechart_reference_gives_states_and_events_apart(self) -> None:
        states, events = cfm.statechart_names(STATECHART)
        self.assertEqual(states, {"Ready", "Crashed"})
        self.assertEqual(events, {"Ended"})


if __name__ == "__main__":
    unittest.main()
