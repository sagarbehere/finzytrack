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


RULE = "IMP-0070"  # PROCESS.md §5: a row moves with its first consumer by rule (P6, owner 2026-11-12)


def moves_with(consumer: str, imp: str = RULE) -> str:
    return f"`review:1`; moves with {consumer} ({imp})"


class MovesWithItsConsumer(unittest.TestCase):
    """P6 (IMP-0070): a row may move to the milestone of its first consumer, a named open backlog row."""

    def test_a_row_follows_its_consumer_to_the_consumers_milestone(self) -> None:
        consumer = row("BL-0126", "M2")
        self.assertEqual(problems([row("BL-0114", "M1a"), consumer],
                                  [row("BL-0114", "M2", ref=moves_with("BL-0126")), consumer]), [])
        self.assertEqual(problems([row("BL-0114", "M1a", "in-progress"), consumer],
                                  [row("BL-0114", "M2", "in-progress", ref=moves_with("BL-0126")), consumer]), [])

    def test_a_review_id_consumer_and_a_bare_m1(self) -> None:
        consumer = row("L4-03", "M7")
        self.assertEqual(problems([row("BL-0082", "M1"), consumer],
                                  [row("BL-0082", "M7", ref=moves_with("L4-03")), consumer]), [])

    def test_a_consumer_that_moved_later_on_a_decision_of_its_own(self) -> None:
        self.assertEqual(problems([row("BL-0114", "M1a"), row("BL-0126", "M2")],
                                  [row("BL-0114", "M3", ref=moves_with("BL-0126")),
                                   row("BL-0126", "M3", ref='owner: "M3 with the shell"')]), [])

    def test_the_citation_already_on_the_row_still_follows_the_consumer(self) -> None:
        # it can only ever follow its consumer, whose own move is checked
        self.assertEqual(problems([row("BL-0114", "M2", ref=moves_with("BL-0126")), row("BL-0126", "M2")],
                                  [row("BL-0114", "M3", ref=moves_with("BL-0126")),
                                   row("BL-0126", "M3", ref="`ADR-0058`")]), [])


class MovesWithRefused(unittest.TestCase):
    """Must not pass: every shape of the rule's citation that does not tie the row to an anchored consumer."""

    def refused(self, base_rows: list[str], tip_rows: list[str]) -> None:
        self.assertTrue(problems(base_rows, tip_rows), (base_rows, tip_rows))

    def test_a_consumer_on_another_milestone(self) -> None:
        self.refused([row("BL-0114", "M1a"), row("BL-0126", "M2")],
                     [row("BL-0114", "M3", ref=moves_with("BL-0126")), row("BL-0126", "M2")])

    def test_a_consumer_that_is_missing(self) -> None:
        self.refused([row("BL-0114", "M1a")], [row("BL-0114", "M2", ref=moves_with("BL-0126"))])

    def test_a_consumer_that_is_new_in_the_range(self) -> None:
        self.refused([row("BL-0114", "M1a")],
                     [row("BL-0114", "M12", ref=moves_with("BL-0999")), row("BL-0999", "M12")])

    def test_a_consumer_that_is_not_open(self) -> None:
        for base_status, tip_status, cell in (("open", "done", "M2"), ("open", "parked", "M2"),
                                              ("open", "removed", "M2"), ("parked", "open", "M2"),
                                              ("done", "open", "M2"), ("open", "post-v0.4", "M2")):
            with self.subTest(base=base_status, tip=tip_status):
                self.refused([row("BL-0114", "M1a"), row("BL-0126", "M2", base_status)],
                             [row("BL-0114", "M2", ref=moves_with("BL-0126")),
                              row("BL-0126", cell, tip_status, ref='owner: "closed"')])

    def test_a_consumer_that_itself_moved_by_the_rule(self) -> None:
        # two rows naming each other could otherwise move anywhere
        self.refused([row("BL-0001", "M2"), row("BL-0002", "M2")],
                     [row("BL-0001", "M12", ref=moves_with("BL-0002")), row("BL-0002", "M12", ref=moves_with("BL-0001"))])
        self.refused([row("BL-0001", "M2"), row("BL-0002", "M2"), row("BL-0003", "M2")],
                     [row("BL-0001", "M9", ref=moves_with("BL-0002")), row("BL-0002", "M9", ref=moves_with("BL-0003")),
                      row("BL-0003", "M2")])

    def test_a_consumer_that_moved_later_without_any_citation(self) -> None:
        found = problems([row("BL-0114", "M1a"), row("BL-0126", "M2")],
                         [row("BL-0114", "M3", ref=moves_with("BL-0126")), row("BL-0126", "M3")])
        self.assertTrue(any("BL-0126" in p for p in found))
        self.assertTrue(any("BL-0114" in p for p in found))

    def test_the_row_naming_itself(self) -> None:
        self.refused([row("BL-0114", "M1a")], [row("BL-0114", "M2", ref=moves_with("BL-0114"))])

    def test_a_wrong_or_missing_rule_id_or_a_loose_wording(self) -> None:
        consumer = row("BL-0126", "M2")
        for ref in (moves_with("BL-0126", "IMP-0069"), "moves with BL-0126", "moves with BL-0126 IMP-0070",
                    "moves with `BL-0126` (IMP-0070)", "Moves With BL-0126 (IMP-0070)", "moves along BL-0126 (IMP-0070)",
                    "moves with (IMP-0070)"):
            with self.subTest(ref=ref):
                self.refused([row("BL-0114", "M1a"), consumer], [row("BL-0114", "M2", ref=ref), consumer])

    def test_a_status_change_is_not_a_move(self) -> None:
        consumer = row("BL-0126", "M2")
        for status, cell in (("parked", "M2"), ("post-v0.4", "M2"), ("removed", "M2"), ("parked", "—")):
            with self.subTest(status=status):
                self.refused([row("BL-0114", "M1a"), consumer],
                             [row("BL-0114", cell, status, ref=moves_with("BL-0126")), consumer])

    def test_a_row_losing_its_milestone(self) -> None:
        consumer = row("BL-0126", "—")
        self.refused([row("BL-0114", "M1a"), consumer], [row("BL-0114", "—", ref=moves_with("BL-0126")), consumer])

    def test_to_no_milestone_behind_a_consumer_that_went_there_on_a_decision(self) -> None:  # S0107 verifier F4
        # the consumer may lose its milestone on the owner's words; a row may not follow it there by the rule
        self.refused([row("BL-0114", "M1a"), row("BL-0126", "M2")],
                     [row("BL-0114", "—", ref=moves_with("BL-0126")), row("BL-0126", "—", ref='owner: "later"')])

    def test_a_look_alike_word_before_with(self) -> None:  # S0107 verifier F4
        consumer = row("BL-0126", "M2")
        for ref in ("removes with BL-0126 (IMP-0070)", "re-moves with BL-0126 (IMP-0070)"):
            with self.subTest(ref=ref):
                self.refused([row("BL-0114", "M1a"), consumer], [row("BL-0114", "M2", ref=ref), consumer])

    def test_a_deleted_row(self) -> None:
        self.refused([row("BL-0114", "M1a"), row("BL-0126", "M2")], [row("BL-0126", "M2", ref=moves_with("BL-0114"))])

    def test_the_citation_on_the_consumer_not_the_moved_row(self) -> None:
        self.refused([row("BL-0114", "M1a"), row("BL-0126", "M2")],
                     [row("BL-0114", "M2"), row("BL-0126", "M2", ref=moves_with("BL-0126") + " BL-0114")])


class TheRuleIsWritten(unittest.TestCase):
    """The citation's IMP id is the entry that records the owner's approval, and PROCESS.md states the form."""

    def test_the_rule_id_names_the_improvements_entry_and_the_process_states_the_form(self) -> None:
        root = SCRIPTS.parent
        self.assertEqual(cd.MOVES_WITH_RULE, RULE)
        self.assertIn(f"## {RULE}:", (root / "design/process/IMPROVEMENTS.md").read_text())
        self.assertIn(f"moves with <ID> ({RULE})", (root / "design/process/PROCESS.md").read_text())


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
