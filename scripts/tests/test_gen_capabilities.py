"""Meta-tests for the capability index generator (design/capabilities.md is an index; the detail stays in
design/capabilities.toml, S0011)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gen_capabilities as gc  # noqa: E402


class Short(unittest.TestCase):
    def test_cut_at_the_first_clause(self) -> None:
        self.assertEqual(gc.short("Pick the default currency; written as an option"), "Pick the default currency")
        self.assertEqual(gc.short("Open a ledger. Then switch."), "Open a ledger")

    def test_long_text_is_cut_on_a_word_with_an_ellipsis(self) -> None:
        text = "word " * 40
        out = gc.short(text)
        self.assertLessEqual(len(out), gc.SHORT + 1)
        self.assertTrue(out.endswith("…"))
        self.assertFalse(out[:-1].endswith(" "))

    def test_a_decimal_point_is_not_a_sentence_end(self) -> None:
        self.assertEqual(gc.short("Supports v0.2 installs"), "Supports v0.2 installs")

    def test_pipes_cannot_break_the_table(self) -> None:
        self.assertNotIn("|", gc.short("a | b").replace("\\|", ""))


class Index(unittest.TestCase):
    ROW = {
        "id": "CAP-X-Y", "area": "Area", "capability": "Do a thing; with details", "v04": "change",
        "v04_note": "long note that stays in the toml", "status": "planned", "milestone": "M2",
        "release_blocking": "yes", "legacy_entry": "`old.py`",
    }

    def test_index_row_has_the_index_columns_and_not_the_detail(self) -> None:
        text = gc.render([self.ROW])
        self.assertIn("| CAP-X-Y | Do a thing | change | planned | M2 | yes |", text)
        self.assertNotIn("long note that stays in the toml", text)
        self.assertNotIn("old.py", text)

    def test_header_points_to_the_source_for_detail(self) -> None:
        self.assertIn("capabilities.toml", gc.render([self.ROW]).split("## ")[0])


if __name__ == "__main__":
    unittest.main()
