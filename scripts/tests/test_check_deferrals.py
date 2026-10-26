"""Meta-tests for the deferral check (S0033, seeded-defect run 1 E10): an open backlog item pushed to a later
milestone, parked, deferred, removed or deleted must cite a decision in the same push."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_deferrals as cd  # noqa: E402

HEADER = "| ID | Title | Area | Milestone | Status | Ref |\n|---|---|---|---|---|---|\n"


def backlog(*rows: str) -> str:
    return "# Backlog\n\n" + HEADER + "".join(r + "\n" for r in rows)


def row(bid: str, milestone: str, status: str = "open", title: str = "A thing", ref: str = "`review:1`") -> str:
    return f"| {bid} | {title} | WS-QA | {milestone} | {status} | {ref} |"


ADRS = {"ADR-0058"}


def problems(base_rows: list[str], tip_rows: list[str] | None) -> list[str]:
    tip = backlog(*tip_rows) if tip_rows is not None else None
    return cd.problems(backlog(*base_rows), tip, lambda adr: adr in ADRS)


class Refused(unittest.TestCase):
    def test_a_later_milestone_without_a_citation(self) -> None:  # plant E10: M6 to M12
        self.assertTrue(problems([row("L4-03", "M6")], [row("L4-03", "M12")]))
        self.assertTrue(problems([row("L4-03", "M6", "in-progress")], [row("L4-03", "M7", "in-progress")]))

    def test_a_bare_m1_counts_as_m1a(self) -> None:
        self.assertTrue(problems([row("I2", "M1")], [row("I2", "M1b")]))
        self.assertEqual(problems([row("I2", "M1")], [row("I2", "M1a")]), [])

    def test_parking_deferring_or_removing_without_a_citation(self) -> None:
        for status, cell in (("parked", "—"), ("post-v0.4", "—"), ("removed", "M6"), ("parked", "M6")):
            with self.subTest(status=status):
                self.assertTrue(problems([row("BL-0001", "M6")], [row("BL-0001", cell, status)]))

    def test_an_open_item_losing_its_milestone(self) -> None:
        self.assertTrue(problems([row("BL-0001", "M6")], [row("BL-0001", "—")]))
        self.assertTrue(problems([row("BL-0001", "M6")], [row("BL-0001", "later")]))

    def test_a_deleted_row_even_with_a_citation_elsewhere(self) -> None:
        self.assertTrue(problems([row("BL-0001", "M6"), row("BL-0002", "M6")],
                                 [row("BL-0002", "M6", ref='owner: "drop BL-0001", `ADR-0058`')]))

    def test_a_deleted_backlog_file(self) -> None:
        self.assertTrue(problems([row("BL-0001", "M6")], None))

    def test_a_citation_already_on_the_row_is_not_new(self) -> None:
        self.assertTrue(problems([row("BL-0001", "M6", ref="`ADR-0058`")], [row("BL-0001", "M9", ref="`ADR-0058`")]))
        quote = 'owner: "keep it at M6"'
        self.assertTrue(problems([row("BL-0001", "M6", ref=quote)], [row("BL-0001", "M9", ref=quote)]))

    def test_an_adr_that_does_not_exist_or_a_quote_that_is_not_one(self) -> None:
        for ref in ("`ADR-0099`", "ADR-58", "owner", 'owner: ""', "owner: later", "(owner, 2026-10-26)", 'Owner says "ok"'):
            with self.subTest(ref=ref):
                self.assertTrue(problems([row("BL-0001", "M6")], [row("BL-0001", "M9", ref=ref)]))

    def test_a_citation_on_another_row_does_not_count(self) -> None:
        self.assertTrue(problems([row("BL-0001", "M6"), row("BL-0002", "M6")],
                                 [row("BL-0001", "M9"), row("BL-0002", "M6", ref="`ADR-0058`")]))

    def test_an_id_on_two_rows_at_the_tip(self) -> None:  # S0033 verifier F2
        # a done row first and an open duplicate at a later milestone: whichever row a reader keeps, refused
        self.assertTrue(problems([row("L4-03", "M6")], [row("L4-03", "M6", "done"), row("L4-03", "M12")]))
        self.assertTrue(problems([row("L4-03", "M6")], [row("L4-03", "M12"), row("L4-03", "M6", "done")]))
        self.assertTrue(any("more than one row" in p for p in problems(
            [row("L4-03", "M6")], [row("L4-03", "M6"), row("L4-03", "M6")])))
        self.assertTrue(problems([row("L4-03", "M6")], [row("L4-03", "M6"), row("BL-0002", "M6", "done"),
                                                       row("BL-0002", "M6", "done")]))

    def test_every_deferred_item_is_named(self) -> None:
        found = problems([row("BL-0001", "M6"), row("BL-0002", "M6")], [row("BL-0001", "M9"), row("BL-0002", "M9")])
        self.assertEqual(len(found), 2)
        self.assertIn("BL-0001", found[0])
        self.assertIn("BL-0002", found[1])


class Allowed(unittest.TestCase):
    def test_a_later_move_citing_a_new_adr_or_owner_quote(self) -> None:
        self.assertEqual(problems([row("BL-0001", "M6")], [row("BL-0001", "M9", ref="`review:1`, `ADR-0058`")]), [])
        self.assertEqual(problems([row("BL-0001", "M6")],
                                  [row("BL-0001", "M9", title='A thing; owner: "after the import work"')]), [])
        self.assertEqual(problems([row("BL-0001", "M0")], [row("BL-0001", "—", "parked", ref="`ADR-0058`")]), [])

    def test_earlier_closed_new_and_untouched_rows(self) -> None:
        self.assertEqual(problems([row("BL-0001", "M6")], [row("BL-0001", "M2")]), [])
        self.assertEqual(problems([row("BL-0001", "M6")], [row("BL-0001", "M6", "done")]), [])
        self.assertEqual(problems([row("BL-0001", "M6")], [row("BL-0001", "M6"), row("BL-0002", "M12")]), [])
        self.assertEqual(problems([row("BL-0001", "—", "parked")], [row("BL-0001", "M12", "parked")]), [])
        self.assertEqual(problems([row("BL-0001", "M6", "done")], [row("BL-0001", "M12", "done")]), [])
        self.assertEqual(problems([row("BL-0001", "M6", "in-progress")], [row("BL-0001", "M6", "open")]), [])

    def test_no_backlog_at_the_base(self) -> None:
        self.assertEqual(cd.problems(None, backlog(row("BL-0001", "M6")), lambda adr: False), [])


class OnARange(unittest.TestCase):
    """The script reads the backlog at the range's base and tip, as the pre-push hook runs it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "gitconfig").write_text("")
        self.repo = base / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(base / "gitconfig"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS"):
            self.env.pop(k, None)
        self.git("init", "-q", "-b", "next")
        self.base = self.commit(backlog(row("L4-03", "M6")), "first", adr=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def commit(self, text: str, msg: str, adr: bool = False) -> str:
        (self.repo / "design").mkdir(exist_ok=True)
        (self.repo / "design/backlog.md").write_text(text)
        if adr:
            (self.repo / "design/decisions").mkdir(exist_ok=True)
            (self.repo / "design/decisions/ADR-0058-process-overhead-trim.md").write_text("# ADR-0058\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def check(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPTS / "check_deferrals.py"), *args], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)

    def test_a_move_in_the_range_is_refused_and_a_cited_one_passes(self) -> None:
        moved = self.commit(backlog(row("L4-03", "M12")), "move")
        r = self.check("--range", f"{self.base}..{moved}")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("L4-03", r.stderr)
        cited = self.commit(backlog(row("L4-03", "M12", ref="`ADR-0058`")), "cite")
        self.assertEqual(self.check("--range", f"{self.base}..{cited}").returncode, 0)  # cited later in the same push

    def test_the_adr_must_exist_at_the_tip(self) -> None:
        cited = self.commit(backlog(row("L4-03", "M12", ref="`ADR-0059`")), "cite a missing ADR")
        self.assertEqual(self.check("--range", f"{self.base}..{cited}").returncode, 1)

    def test_a_move_undone_inside_the_range_passes(self) -> None:
        self.commit(backlog(row("L4-03", "M12")), "move")
        back = self.commit(backlog(row("L4-03", "M6")), "back")
        self.assertEqual(self.check("--range", f"{self.base}..{back}").returncode, 0)

    def test_a_whole_history_spec_has_no_base_and_a_bad_spec_is_an_error(self) -> None:
        moved = self.commit(backlog(row("L4-03", "M12")), "move")
        self.assertEqual(self.check("--range", moved).returncode, 0)
        self.assertEqual(self.check("--range", "next").returncode, 2)


if __name__ == "__main__":
    unittest.main()
