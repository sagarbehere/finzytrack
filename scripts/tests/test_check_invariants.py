"""Meta-tests for scripts/check_invariants.py: fixtures it must flag and one it must pass."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_invariants as ci  # noqa: E402

GOOD = """
### INV-01: First
- **Rule:** r
- **Why:** w
- **Enforced by:** `scripts/tests/test_one.py`
- **Status:** enforced

### INV-02: Second
- **Rule:** r
- **Why:** w
- **Enforced by:** planned: something.
- **Status:** pending (BL-0002, by M3)
"""
BACKLOG = "| BL-0002 | Build enforcement for INV-02 | open | M3 |\n"


class InvariantCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts/tests").mkdir(parents=True)
        (self.root / "scripts/tests/test_one.py").write_text("# covers INV-01\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_check(self, text: str, backlog: str | None = BACKLOG, exit_ms: str | None = None) -> list[str]:
        return ci.check(text, self.root, backlog, exit_ms)

    def test_good_file_passes(self) -> None:
        self.assertEqual(self.run_check(GOOD), [])

    def test_gap_in_numbering(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("INV-02", "INV-03")))

    def test_missing_why(self) -> None:
        self.assertTrue(any("Why" in e for e in self.run_check(GOOD.replace("- **Why:** w\n", "", 1))))

    def test_enforcement_file_missing(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("test_one.py", "test_nope.py")))

    def test_enforcement_file_not_tagged(self) -> None:
        (self.root / "scripts/tests/test_one.py").write_text("# no tag\n")
        self.assertTrue(any("does not mention" in e for e in self.run_check(GOOD)))

    def test_enforcement_file_that_never_runs(self) -> None:
        (self.root / "notes").mkdir()
        (self.root / "notes/docs.md").write_text("INV-01\n")
        text = GOOD.replace("`scripts/tests/test_one.py`", "`notes/docs.md`")
        self.assertTrue(any("no enforcement file is a test" in e for e in self.run_check(text)))

    def test_pending_without_backlog_item(self) -> None:
        self.assertTrue(self.run_check(GOOD, backlog="| BL-0009 | other | open |\n"))

    def test_pending_on_closed_item(self) -> None:
        self.assertTrue(self.run_check(GOOD, backlog="| BL-0002 | x | done | M3 |\n"))

    def test_a_mention_elsewhere_is_not_the_item(self) -> None:
        backlog = "| Invariant items (`BL-0002`) | 31 |\n| BL-0002 | x | done | M3 |\n"
        self.assertTrue(any("closed" in e for e in self.run_check(GOOD, backlog=backlog)))
        self.assertTrue(self.run_check(GOOD, backlog="- see BL-0002 for details\n"))

    def test_pending_past_milestone_exit(self) -> None:
        self.assertEqual(self.run_check(GOOD, exit_ms="M2"), [])
        self.assertTrue(self.run_check(GOOD, exit_ms="M3"))

    def test_bad_status(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("pending (BL-0002, by M3)", "pending (BL-TBD, by M3)")))


FINDINGS = """
| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L1-06 | Layering | Medium | WS-X | M0 | {s0} | — | `review:L1` |
| L3-07 | dsum | Medium | WS-X | M7 | open | — | `review:L3` |
"""
ITEMS = """
| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0001 | Corpus | WS-WRITE | {m1} | open | x |
"""


class BacklogAtMilestoneExit(unittest.TestCase):
    """Open review findings and items fail the exit of their milestone (S0023); closed ones never do."""

    def errors(self, backlog: str, exit_ms: str | None) -> list[str]:
        return ci.check_backlog(backlog, exit_ms)

    def test_an_open_finding_fails_its_milestone_exit(self) -> None:
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="open"), "M0")))
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="in-progress"), "M0")))

    def test_it_also_fails_any_later_exit(self) -> None:
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="open"), "M2")))

    def test_closed_findings_pass(self) -> None:
        for status in ("done", "removed", "post-v0.4", "parked"):
            with self.subTest(status=status):
                self.assertEqual(self.errors(FINDINGS.format(s0=status), "M0"), [])

    def test_a_later_milestone_does_not_fail_an_earlier_exit(self) -> None:
        self.assertEqual(self.errors(FINDINGS.format(s0="done"), "M6"), [])
        self.assertTrue(any("L3-07" in e for e in self.errors(FINDINGS.format(s0="done"), "M7")))

    def test_without_an_exit_only_the_shape_is_checked(self) -> None:
        self.assertEqual(self.errors(FINDINGS.format(s0="open"), None), [])

    def test_bare_m1_is_due_by_m1a(self) -> None:
        self.assertTrue(self.errors(ITEMS.format(m1="M1"), "M1a"))
        self.assertTrue(self.errors(ITEMS.format(m1="M1a (agent)"), "M1a"))
        self.assertEqual(self.errors(ITEMS.format(m1="M1b (agent)"), "M1a"), [])
        self.assertTrue(self.errors(ITEMS.format(m1="M1b"), "M1b"))
        self.assertEqual(self.errors(ITEMS.format(m1="M1"), "M0"), [])

    def test_unknown_status_is_refused(self) -> None:
        self.assertTrue(any("status" in e for e in self.errors(FINDINGS.format(s0="fixed"), None)))

    def test_open_item_without_a_milestone_is_refused(self) -> None:
        for cell in ("—", "later", "M99", ""):
            with self.subTest(cell=cell):
                self.assertTrue(any("needs a milestone" in e for e in self.errors(ITEMS.format(m1=cell), None)))

    def test_tables_without_an_id_header_are_not_items(self) -> None:
        other = "| Double | Canonical | Milestone |\n|---|---|---|\n| L1-06 | L1-07 | M0 |\n"
        status = "| Status | Meaning |\n|---|---|\n| open | Not started. |\n"
        self.assertEqual(self.errors(other + "\n" + status, "M12"), [])

    def test_a_table_ends_at_a_non_table_line(self) -> None:
        backlog = "| ID | Milestone | Status |\n|---|---|---|\n\ntext\n| L1-06 | M0 | open |\n"
        self.assertEqual(self.errors(backlog, "M0"), [])  # a headerless row is not under an ID table

    def test_the_real_backlog_has_a_valid_shape(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "design/backlog.md").read_text(encoding="utf-8")
        self.assertEqual(self.errors(text, None), [])
        self.assertTrue(len(ci.table_rows(text)) > 250)  # 299 rows on 2026-10-25: catches a parser that reads nothing

    def test_main_refuses_an_unknown_exit_name(self) -> None:
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"FT_MILESTONE_EXIT": "m0"}):
            self.assertEqual(ci.main(), 1)


if __name__ == "__main__":
    unittest.main()
