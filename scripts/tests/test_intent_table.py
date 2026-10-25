"""Structural checks on the per-mutator intended-change table (design/specs/write-path/intent-table.toml, S0004)."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "design/specs/write-path/intent-table.toml"
COLUMNS = ("id", "caps", "kind", "old", "edits", "files", "intent", "meta", "ack", "refuses", "oracle", "status",
           "milestone", "adrs")
KINDS = {"add", "change-one", "many"}  # ADR-0014's clearance kinds; "no old-app comparison" is old = "n/a"
EDITS = {"insert", "replace", "delete", "replace_token", "insert_meta", "delete_meta", "option_line", "new_file"}
# Error classes Beancount 3.2.0 reports (listed from the pinned package, S0004); an acknowledgeable key names one.
BEANCOUNT_ERRORS = {"BalanceError", "PadError", "ValidationError", "DocumentError", "BookingError", "ReductionError",
                    "AmbiguousMatchError", "InterpolationError", "CategorizationError", "SelfReduxError", "LexerError",
                    "ParserError", "ParserSyntaxError", "LoadError", "DeprecatedError", "ImplicitPriceError",
                    "UniquePricesError", "CheckCommodityError", "CoherentCostError", "CommodityError", "LeafOnlyError",
                    "OneCommodityError", "SellGainsError", "UnusedAccountError", "MatchBasisError", "ConfigError"}
# The S0002 harness models and the table rows they stand for (the table is the authority).
HARNESS_ROWS = {"update_transaction": "txn.edit", "rename_account": "account.rename", "import_append": "import.commit"}


def load() -> dict:
    with TABLE.open("rb") as f:
        return tomllib.load(f)


class IntentTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load()
        cls.rows = cls.table["mutator"]
        cls.ids = [r["id"] for r in cls.rows]
        with (ROOT / "design/capabilities.toml").open("rb") as f:
            caps = tomllib.load(f)
        cls.caps = {c["id"] for c in next(v for v in caps.values() if isinstance(v, list))}

    def test_every_row_has_every_column(self) -> None:
        for r in self.rows:
            with self.subTest(row=r.get("id")):
                self.assertEqual(set(r), set(COLUMNS))
                for col in ("id", "kind", "old", "files", "intent", "status", "milestone"):
                    self.assertTrue(str(r[col]).strip(), col)
                for col in ("caps", "edits", "oracle", "adrs"):
                    self.assertTrue(r[col], col)

    def test_ids_are_unique(self) -> None:
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_kinds_edits_and_milestones(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                self.assertIn(r["kind"], KINDS)
                self.assertLessEqual(set(r["edits"]), EDITS)
                self.assertRegex(r["milestone"], r"^M\d{1,2}[ab]?$")

    def test_acknowledgeable_errors_name_a_beancount_error(self) -> None:
        for r in self.rows:
            for ack in r["ack"]:
                with self.subTest(row=r["id"], ack=ack):
                    first = ack.split()[0]
                    self.assertTrue(first in BEANCOUNT_ERRORS or first == "as", ack)
                    self.assertIn(" on ", ack + (" on " if first == "as" else ""))

    def test_capabilities_and_adrs_exist(self) -> None:
        adrs = {p.name.split("-")[0] + "-" + p.name.split("-")[1] for p in (ROOT / "design/decisions").glob("ADR-*.md")}
        for r in self.rows:
            with self.subTest(row=r["id"]):
                self.assertLessEqual(set(r["caps"]), self.caps)
                self.assertLessEqual(set(r["adrs"]), adrs)

    def test_status_is_decided_draft_or_an_open_inbox_question(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                status = r["status"]
                if status.startswith("owner:"):
                    name = status.removeprefix("owner:")
                    self.assertTrue((ROOT / "design/inbox" / name).is_file() or (ROOT / "design/inbox/answered" / name).is_file(), name)
                else:
                    self.assertIn(status, ("decided", "draft"))

    def test_every_legacy_mutator_maps_to_a_row_or_a_cut(self) -> None:
        legacy = self.table["legacy"]
        self.assertEqual(len(legacy["mutators"]), 26)
        self.assertEqual(set(legacy["mutators"]), set(legacy["map"]))
        for name, targets in legacy["map"].items():
            for t in targets:
                with self.subTest(legacy=name, target=t):
                    self.assertTrue(t in self.ids or re.match(r"^not built: ADR-\d{4} ", t), t)

    def test_a_mutator_with_an_old_endpoint_names_its_oracle_classes(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                no_old = r["old"].startswith("n/a")
                self.assertEqual(no_old, any(o.startswith("none: no old endpoint") for o in r["oracle"]))

    def test_the_harness_models_name_existing_rows(self) -> None:
        model = (ROOT / "scripts/oracle/model.py").read_text(encoding="utf-8")
        for mutator, row in HARNESS_ROWS.items():
            with self.subTest(mutator=mutator):
                self.assertIn(row, self.ids)
                self.assertTrue(f'"{mutator}": "{row}"' in model, f"scripts/oracle/model.py INTENT_ROWS lacks {mutator}")


if __name__ == "__main__":
    unittest.main()
