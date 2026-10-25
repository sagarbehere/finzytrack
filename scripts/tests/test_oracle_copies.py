"""Meta-tests for the oracle's copy guards (INV-24): what it refuses, what it stages, what it may print."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "oracle"))

import copies  # noqa: E402

CONFIG = 'setup_complete: false\n\n# File paths\nledger_file: "./data/ledgers/one.beancount"\nserver:\n  port: 8001\n'


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class CopiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.live, self.legacy = base / "live", base / "legacy"
        for d in (self.live, self.legacy):
            d.mkdir()
        self.old = {k: os.environ.get(k) for k in ("FT_LIVE_DATA", "FT_LEGACY_ROOT")}
        os.environ.update({"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy)})
        self.src = base / "src"
        (self.src / "y").mkdir(parents=True)
        (self.src / "main.beancount").write_text('include "y/*.beancount"\n', encoding="utf-8")
        (self.src / "y/2020.beancount").write_text("2020-01-01 open Assets:Cash\n", encoding="utf-8")
        self.template = base / "template"
        (self.template / "config").mkdir(parents=True)
        (self.template / "config/config.yaml").write_text(CONFIG, encoding="utf-8")
        self.base = base

    def tearDown(self) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_refuses_the_live_data_folder_the_legacy_worktree_and_the_repo(self) -> None:
        for bad in (self.live, self.live / "data/ledgers", self.legacy, self.legacy / "backend", copies.REPO, self.base):
            with self.subTest(bad=bad):
                self.assertIsNotNone(copies.refuse_dir(bad))
        self.assertIsNone(copies.refuse_dir(self.src))

    def test_refuses_a_symlink_into_the_live_data_folder(self) -> None:
        link = self.base / "innocent"
        link.symlink_to(self.live)
        self.assertIsNotNone(copies.refuse_dir(link))

    def test_stage_copies_the_ledger_and_points_the_config_at_the_copy(self) -> None:
        before = snapshot(self.src)
        root = self.base / "case1"
        ledger = copies.stage_root(self.src, "main.beancount", self.template, root)
        self.assertEqual(ledger, root / "data/ledgers/main.beancount")
        self.assertEqual(snapshot(root / "data/ledgers"), before)
        cfg = (root / "config/config.yaml").read_text(encoding="utf-8")
        self.assertIn('ledger_file: "./data/ledgers/main.beancount"', cfg)
        self.assertIn("setup_complete: true", cfg)
        (root / "data/ledgers/main.beancount").write_text("changed\n", encoding="utf-8")
        self.assertEqual(snapshot(self.src), before)  # the source is never written

    def test_stage_refuses_a_root_file_outside_the_ledger_dir(self) -> None:
        with self.assertRaises(ValueError):
            copies.stage_root(self.src, "../escape.beancount", self.template, self.base / "case2")

    def test_stage_refuses_a_forbidden_source(self) -> None:
        with self.assertRaises(ValueError):
            copies.stage_root(self.live, "main.beancount", self.template, self.base / "case3")

    def test_stage_refuses_an_existing_root(self) -> None:
        (self.base / "case4").mkdir()
        with self.assertRaises(ValueError):
            copies.stage_root(self.src, "main.beancount", self.template, self.base / "case4")

    def test_entry_text_only_for_corpus_or_generated_ledgers(self) -> None:
        self.assertFalse(copies.may_show_entries(self.src))  # could be a copy of a real ledger
        copies.mark_generated(self.src)
        self.assertTrue(copies.may_show_entries(self.src))
        self.assertTrue(copies.may_show_entries(copies.REPO / "corpora/ledgers/oracle"))


if __name__ == "__main__":
    unittest.main()
