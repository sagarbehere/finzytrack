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

    def test_pending_past_milestone_exit(self) -> None:
        self.assertEqual(self.run_check(GOOD, exit_ms="M2"), [])
        self.assertTrue(self.run_check(GOOD, exit_ms="M3"))

    def test_bad_status(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("pending (BL-0002, by M3)", "pending (BL-TBD, by M3)")))


if __name__ == "__main__":
    unittest.main()
