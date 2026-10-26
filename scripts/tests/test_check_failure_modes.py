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
| `Busy` | busy |
| `Crashed` | crashed |
| `Unavailable` | unavailable |
| `Closed` | closed |

## Transitions

| From | Event | To | Effects | Note |
|---|---|---|---|---|
| `Busy` | `Ended` | `Crashed`, `Unavailable` | `Kill`, `Log`, `Fail` | a stop |
| `Busy` | `TimedOut` | `Crashed`, `Unavailable` | `Kill`, `Log`, `Fail` | a stop |
| `Ready` | `Exited` | `Crashed`, `Unavailable` | `Kill`, `Log` | a stop |
| `Ready` | `Close` | `Closed` | `Shutdown`, `Log` |  |
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

GOOD_STATES = "`Busy` → `Crashed` on `Ended`"
GOOD_TEST = "`t/test_x.py::test_one`; BL-0901 (M2)"


def row(rid: str, failure: str, test: str = GOOD_TEST, states: str = GOOD_STATES, body: str | None = None) -> str:
    """One row in the real spec's shape: who, data and user cells, the engine's States cell, the Test cell."""
    cells = [body or "a", body or "b", body or "c"]
    if rid.upper().startswith("E"):
        cells.append(states if body is None else body)
    return "| " + " | ".join([rid, failure, *cells, test]) + " |"


def same_as(rid: str, failure: str, target: str) -> str:
    text = f"same as {target}"
    return row(rid, failure, text, body=text)


def spec(overrides: dict[str, str] | None = None, *, drop: str | None = None, extra: str = "") -> str:
    """A complete, valid spec; `overrides` replaces whole rows by ID, `drop` leaves one out."""
    lines: list[str] = []
    for letter in cfm.PROGRAMS:
        lines += ["", "| ID | Failure | Who | Data | User |" + (" States |" if letter == "E" else "") + " Test |"]
        lines.append("|---|---|---|---|---|---|" + ("---|" if letter == "E" else ""))
        for n, failure in enumerate(cfm.FAILURES, 1):
            rid = f"{letter}{n}"
            if rid == drop:
                continue
            lines.append(overrides[rid] if overrides and rid in overrides else row(rid, failure))
    return "\n".join(lines) + "\n" + extra


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


class Passes(Fixture):
    def test_a_complete_spec_passes(self) -> None:
        problems, counts = cfm.check(spec(), BACKLOG, STATECHART, self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(sum(counts.values()), 28)

    def test_every_reference_shape_passes(self) -> None:
        text = spec(
            {
                "M1": row("M1", "crash", "`t/test_x.py::Suite::test_two`; `t/smoke.rs::rust_case`; L9-99 (M5)"),
                "M2": row("M2", "hang", "BL-0903 (M1b)"),
                "M3": same_as("M3", "slow", "M1"),
                "M5": row("M5", "parent gone", "n/a: the parent is the launcher here"),
                "E2": row("E2", "hang", states="`Busy` → `Crashed` or `Unavailable` on `Ended` or `TimedOut`"),
                "E5": row("E5", "parent gone", "BL-0901 (M2)", states="n/a: the client is gone with it"),
                "E7": row("E7", "told to stop", states="`Ready` → `Closed` on `Close`; `Ready` → `Unavailable` on `Exited`"),
            }
        )
        problems, counts = cfm.check(text, BACKLOG, STATECHART, self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(counts["same as"], 1)
        self.assertEqual(counts["n/a"], 1)
        self.assertEqual(counts["planned"], 2)

    def test_tables_and_text_outside_the_rows_are_ignored(self) -> None:
        text = spec(extra="\n| Class | HTTP |\n|---|---|\n| Invalid | 422 |\n\nSee row E1 and `E2`.\n")
        self.assertEqual(self.problems(text), [])

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
        self.refused(spec(extra=row("S1", "crash") + "\n"), "appears twice")

    def test_a_second_row_for_a_pair_is_refused(self) -> None:
        self.refused(spec(extra=row("S8", "crash") + "\n"), "2 rows for the Tauri shell: crash")

    def test_a_row_of_an_unknown_program_is_refused(self) -> None:  # S0041 verifier F1
        self.refused(spec(extra=row("X1", "crash") + "\n"), "X1: not a row of a known program")
        self.refused(spec(extra="| Q8 | burns | | | | |\n"), "Q8: not a row of a known program")
        self.refused(spec(extra=row("EE1", "crash") + "\n"), "EE1: not a row of a known program")

    def test_a_row_id_in_another_case_is_refused(self) -> None:  # S0041 verifier F1
        self.refused(spec(extra=row("e1", "crash", "prose only") + "\n"), "e1: not a row of a known program")
        self.refused(spec(drop="M3", extra=row("m3", "slow") + "\n"), "m3: not a row of a known program")

    def test_a_row_in_another_programs_shape_is_refused(self) -> None:
        self.refused(spec({"E1": "| E1 | crash | a | b | c | `t/test_x.py::test_one` |"}), "E1: 5 cells after the ID")
        self.refused(spec({"M1": row("E1", "crash").replace("| E1 |", "| M1 |")}), "M1: 6 cells after the ID")

    def test_a_row_hidden_in_a_code_fence_or_a_comment_does_not_count(self) -> None:  # S0041 verifier F5
        e1 = row("E1", "crash")
        for hidden in (f"```\n{e1}\n```", f"~~~\n{e1}\n~~~", f"<!--\n{e1}\n-->", f"<!--\nnote\n{e1}\n-->", f"<!-- {e1} -->"):
            with self.subTest(hidden=hidden):
                self.refused(spec(drop="E1", extra=hidden + "\n"), "no row for the ledger engine: crash")

    def test_an_id_whose_number_is_another_failures_is_refused(self) -> None:
        text = spec({"S2": row("S2", "slow"), "S3": same_as("S3", "hang", "S2")})
        self.refused(text, "S2 is 'slow', which is number 3")

    def test_an_unknown_failure_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "exploded")}), "unknown failure 'exploded'")

    def test_an_empty_test_cell_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "")}), "the Test cell is empty")

    def test_prose_in_the_test_cell_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "covered by the smoke run")}), "not a test or backlog reference")
        self.refused(spec({"S1": row("S1", "crash", "`t/test_x.py::test_one` (mostly)")}), "not a test or backlog")

    def test_a_backlog_id_without_its_milestone_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "BL-0901")}), "not a test or backlog reference")

    def test_a_missing_test_file_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "`t/test_gone.py::test_one`")}), "no file t/test_gone.py")

    def test_a_test_outside_the_repo_is_refused(self) -> None:  # S0041 verifier attack 9
        self.refused(spec({"S1": row("S1", "crash", "`../test_outside_repo.py::test_one`")}), "outside the repo")
        self.refused(spec({"S1": row("S1", "crash", "`t/../../test_outside_repo.py::test_one`")}), "outside the repo")
        outside = Path(self._tmp.name) / "test_outside_repo.py"
        self.refused(spec({"S1": row("S1", "crash", f"`{outside}::test_one`")}), "outside the repo")

    def test_a_missing_test_name_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "`t/test_x.py::test_gone`")}), "no test test_gone")

    def test_a_helper_that_is_not_a_test_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "`t/test_x.py::helper`")}), "helper is not a test name")
        self.refused(spec({"S1": row("S1", "crash", "`t/smoke.rs::helper`")}), "no #[test] fn helper")

    def test_a_test_named_in_the_wrong_class_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "`t/test_x.py::Suite::test_outside`")}), "no test test_outside")
        self.refused(spec({"S1": row("S1", "crash", "`t/test_x.py::Other::test_two`")}), "no class Other")

    def test_a_name_in_a_comment_or_string_is_not_a_test(self) -> None:
        (self.repo / "t" / "test_y.py").write_text('# def test_ghost(\nX = "def test_ghost("\n')
        self.refused(spec({"S1": row("S1", "crash", "`t/test_y.py::test_ghost`")}), "no test test_ghost")

    def test_an_unknown_kind_of_test_file_is_refused(self) -> None:
        (self.repo / "t" / "a.spec.ts").write_text("it('test_one')\n")
        self.refused(spec({"S1": row("S1", "crash", "`t/a.spec.ts::test_one`")}), "only Python and Rust tests")

    def test_an_unknown_backlog_row_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "BL-0999 (M2)")}), "no backlog row BL-0999")

    def test_a_closed_backlog_row_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "BL-0902 (M2)")}), "BL-0902 is done: cite the test")

    def test_a_backlog_row_with_another_milestone_is_refused(self) -> None:
        self.refused(spec({"S1": row("S1", "crash", "BL-0901 (M1b)")}), "BL-0901 is due in M2, not M1b")

    def test_same_as_a_missing_row_or_a_chain_is_refused(self) -> None:
        self.refused(spec({"S3": same_as("S3", "slow", "S9")}), "same as S9: no such row")
        text = spec({"S3": same_as("S3", "slow", "S2"), "S2": same_as("S2", "hang", "S1")})
        self.refused(text, "same as S2, which is itself 'same as S1'")
        text = spec({"S3": same_as("S3", "slow", "S5"), "S5": row("S5", "parent gone", "n/a: the launcher is its parent")})
        self.refused(text, "same as S5, which is itself 'n/a'")

    def test_same_as_a_row_of_another_program_is_refused(self) -> None:  # S0041 verifier F4
        self.refused(spec({"W3": same_as("W3", "slow", "E2")}), "same as E2: a row of another program")

    def test_a_same_as_row_with_other_content_is_refused(self) -> None:  # S0041 verifier F4
        text = spec({"W3": row("W3", "slow", "same as W2")})
        self.refused(text, "every cell of a same-as row says 'same as W2'")
        text = spec({"E3": row("E3", "slow", "same as E2", body="same as E2").replace("same as E2 | same as E2 |", "x | same as E2 |", 1)})
        self.refused(text, "every cell of a same-as row says 'same as E2'")

    def test_same_as_or_n_a_beside_other_references_is_refused(self) -> None:
        self.refused(spec({"S3": row("S3", "slow", "same as S2; BL-0901 (M2)")}), "not a test or backlog reference")

    def test_n_a_without_a_reason_is_refused(self) -> None:
        self.refused(spec({"S5": row("S5", "parent gone", "n/a: none")}), "n/a needs a reason")

    def test_a_states_cell_that_is_not_a_list_of_transitions_is_refused(self) -> None:  # S0041 verifier F3
        for states in ("on `Ended`", "crashes", "`Busy`", "`Busy` → `Crashed`", "`Busy` → `Crashed` on `Ended` (mostly)",
                       "`Busy` -> `Crashed` on `Ended`", "`Busy` → `Crashed` or on `Ended`", ""):
            with self.subTest(states=states):
                self.refused(spec({"E1": row("E1", "crash", states=states)}), "not a transition")

    def test_a_transition_the_statechart_lacks_is_refused(self) -> None:  # S0041 verifier F3
        cases = {
            "`Ready` → `Closed` on `TimedOut`": "`Ready` on `TimedOut` is not in the statechart",
            "`Busy` → `Closed` on `Ended`": "`Busy` on `Ended` does not lead to `Closed`",
            "`Busy` → `Crashed` or `Dead` on `Ended`": "`Busy` on `Ended` does not lead to `Dead`",
            "`Busy` → `Crashed` on `Ended` or `Exited`": "`Busy` on `Exited` is not in the statechart",
            "`Busy` → `Crashed` on `Ended`; `Ghost` → `Busy` on `Ended`": "`Ghost` on `Ended` is not in the statechart",
        }
        for states, fragment in cases.items():
            with self.subTest(states=states):
                self.refused(spec({"E1": row("E1", "crash", states=states)}), fragment)

    def test_an_engine_states_n_a_without_a_reason_is_refused(self) -> None:
        self.refused(spec({"E5": row("E5", "parent gone", states="n/a: gone")}), "n/a needs a reason")

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


class Sources(unittest.TestCase):
    def test_status_and_milestone_are_read_from_either_table_shape(self) -> None:
        rows = cfm.backlog_rows(BACKLOG)
        self.assertEqual(rows["BL-0901"], ("M2", "open"))
        self.assertEqual(rows["BL-0903"], ("M1b", "in-progress"))
        self.assertEqual(rows["L9-99"], ("M5", "open"))

    def test_the_statechart_transitions_are_read_from_its_transitions_table(self) -> None:
        self.assertEqual(
            cfm.statechart_transitions(STATECHART),
            {
                ("Busy", "Ended"): frozenset({"Crashed", "Unavailable"}),
                ("Busy", "TimedOut"): frozenset({"Crashed", "Unavailable"}),
                ("Ready", "Exited"): frozenset({"Crashed", "Unavailable"}),
                ("Ready", "Close"): frozenset({"Closed"}),
            },
        )

    def test_only_the_transitions_table_gives_transitions(self) -> None:
        other = "## Notes\n\n| From | Event | To |\n|---|---|---|\n| `Ready` | `TimedOut` | `Closed` |\n\n"
        self.assertNotIn(("Ready", "TimedOut"), cfm.statechart_transitions(other + STATECHART))

    def test_the_real_statechart_has_its_transitions(self) -> None:
        real = cfm.statechart_transitions(cfm.STATECHART.read_text(encoding="utf-8"))
        self.assertEqual(len(real), 34)  # S0040: 34 rows in ROWS
        self.assertEqual(real[("Busy", "TimedOut")], frozenset({"Crashed", "Unavailable"}))

    def test_fenced_and_commented_lines_are_hidden(self) -> None:
        text = "a\n```\nb\n```\nc\n<!-- d\ne -->\nf\n<!-- g --> h\n"
        self.assertEqual([line for _, line in cfm.visible_lines(text)], ["a", "c", "f"])


if __name__ == "__main__":
    unittest.main()
