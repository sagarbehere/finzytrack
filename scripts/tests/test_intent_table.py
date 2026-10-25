"""Structural checks on the per-mutator intended-change table (design/specs/write-path/intent-table.toml, S0004)."""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "design/specs/write-path/intent-table.toml"
COLUMNS = ("id", "caps", "kind", "old", "edits", "files", "intent", "meta", "ack", "refuses", "oracle", "status",
           "milestone", "adrs")
KINDS = {"add", "change-one", "many"}  # ADR-0014's clearance kinds; "no old-app comparison" is old = "n/a"
# ADR-0009's primitives, plus `replace_file` for the demo only (INV-25, ADR-0022).
EDITS = {"insert", "replace", "delete", "replace_token", "insert_meta", "delete_meta", "option_line", "new_file", "replace_file"}
# Error classes Beancount 3.2.0 reports (listed from the pinned package, S0004).
BEANCOUNT_ERRORS = {"BalanceError", "PadError", "ValidationError", "DocumentError", "BookingError", "ReductionError",
                    "AmbiguousMatchError", "InterpolationError", "CategorizationError", "SelfReduxError", "LexerError",
                    "ParserError", "ParserSyntaxError", "LoadError", "DeprecatedError", "ImplicitPriceError",
                    "UniquePricesError", "CheckCommodityError", "CoherentCostError", "CommodityError", "LeafOnlyError",
                    "OneCommodityError", "SellGainsError", "UnusedAccountError", "MatchBasisError", "ConfigError"}
# Never acknowledgeable (README "What a row means"): booking and parser errors.
NEVER_ACK = {"BookingError", "ReductionError", "AmbiguousMatchError", "InterpolationError", "CategorizationError",
             "SelfReduxError", "LexerError", "ParserError", "ParserSyntaxError"}
# ADR-0026 §4.1 app-written keys and review:28 §4.2 user vocabulary: the only keys a row may write.
APP_KEYS = {"import-key", "txn-date", "original", "statement-amount", "memo"}
VOCABULARY = {"name", "asset-class", "exposure", "fetch-symbol", "isin", "issue-date", "denomination", "account-type",
              "interest-rate", "maturity-date", "interest-account", "account_number", "ofx-bank-id", "ifsc_code",
              "swift_bic", "display-name", "description", "income-type", "dividend-of", "reason", "document*", "note",
              "user keys typed in the drawer"}
NOT_WRITTEN = {"id", "content_hash", "source_account", "external_id", "external_id_type", "source_rule", "status",
               "import-batch", "import-key-override", "advisor"}
ORACLE = re.compile(r"^(I2|I3|I10|M28)\b|^FIX-(?P<finding>[A-Z][A-Za-z0-9.]*(?:-\d+)?)\b|^none: no old endpoint|^non-comparable: ")


def load() -> dict:
    with TABLE.open("rb") as f:
        return tomllib.load(f)


def harness_rows() -> dict[str, str]:
    """`model.INTENT_ROWS`, read as code (a comment cannot satisfy the check)."""
    tree = ast.parse((ROOT / "scripts/oracle/model.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "INTENT_ROWS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("scripts/oracle/model.py has no INTENT_ROWS")


class IntentTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load()
        cls.rows = cls.table["mutator"]
        cls.ids = [r["id"] for r in cls.rows]
        with (ROOT / "design/capabilities.toml").open("rb") as f:
            caps = tomllib.load(f)
        cls.caps = {c["id"] for c in next(v for v in caps.values() if isinstance(v, list))}
        cls.adrs = {"-".join(p.name.split("-")[:2]) for p in (ROOT / "design/decisions").glob("ADR-*.md")}
        cls.backlog = (ROOT / "design/backlog.md").read_text(encoding="utf-8")

    def refers_to_a_row(self, text: str) -> bool:
        m = re.match(r"^as ([a-z][a-z.-]*[a-z])\b", text)
        return bool(m and m.group(1) in self.ids) or text == "as the undone write's row"

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
                if "replace_file" in r["edits"]:
                    self.assertEqual(r["id"], "ws.refresh-demo")  # the one replacement in the system (INV-25)

    def test_acknowledgeable_errors_are_beancount_errors_that_may_be_acknowledged(self) -> None:
        for r in self.rows:
            for ack in r["ack"]:
                with self.subTest(row=r["id"], ack=ack):
                    if ack.startswith("as "):
                        self.assertTrue(self.refers_to_a_row(ack), ack)
                        continue
                    first = ack.split()[0]
                    self.assertIn(first, BEANCOUNT_ERRORS - NEVER_ACK, ack)
                    self.assertIn(" on ", ack)

    def test_metadata_written_is_on_the_contract_list(self) -> None:
        for r in self.rows:
            for key in r["meta"]:
                with self.subTest(row=r["id"], key=key):
                    self.assertNotIn(key, NOT_WRITTEN)
                    m = re.match(r"^the vocabulary keys of ([a-z][a-z.-]*[a-z])$", key)
                    self.assertTrue(key in APP_KEYS | VOCABULARY or self.refers_to_a_row(key)
                                    or bool(m and m.group(1) in self.ids), key)

    def test_oracle_expectations_name_known_classes(self) -> None:
        for r in self.rows:
            for o in r["oracle"]:
                with self.subTest(row=r["id"], oracle=o):
                    m = ORACLE.match(o)
                    self.assertTrue(m or self.refers_to_a_row(o), o)
                    if m and m.group("finding"):
                        self.assertRegex(self.backlog, rf"(?m)^\| {re.escape(m.group('finding'))} \|", o)

    def test_capabilities_and_adrs_exist(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                self.assertLessEqual(set(r["caps"]), self.caps)
                self.assertLessEqual(set(r["adrs"]), self.adrs)

    def test_status_is_decided_draft_or_an_open_inbox_question(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                status = r["status"]
                if status.startswith("owner:"):
                    name = status.removeprefix("owner:")
                    self.assertTrue((ROOT / "design/inbox" / name).is_file() or (ROOT / "design/inbox/answered" / name).is_file(), name)
                    continue
                self.assertIn(status, ("decided", "draft"))
                text = " ".join(str(v) for v in r.values())
                self.assertNotIn("inbox", text, "a row that still points at an open question cannot be decided or draft")

    def test_every_legacy_mutator_maps_to_a_row_or_a_cut(self) -> None:
        # The list is the `@_serialized_write` set of the legacy LedgerManager at legacy-final (26, checked against
        # the legacy worktree in S0004; the worktree is not in CI).
        legacy = self.table["legacy"]
        self.assertEqual(len(legacy["mutators"]), 26)
        self.assertEqual(set(legacy["mutators"]), set(legacy["map"]))
        for name, targets in legacy["map"].items():
            for t in targets:
                with self.subTest(legacy=name, target=t):
                    cut = re.match(r"^not built: (ADR-\d{4}) ", t)
                    self.assertTrue(t in self.ids or (cut and cut.group(1) in self.adrs), t)

    def test_a_mutator_with_an_old_endpoint_names_its_oracle_classes(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                no_old = r["old"].startswith("n/a")
                self.assertEqual(no_old, any(o.startswith("none: no old endpoint") for o in r["oracle"]))

    def test_the_harness_models_name_existing_rows(self) -> None:
        rows = harness_rows()
        self.assertEqual(set(rows), {"update_transaction", "rename_account", "import_append"})
        self.assertLessEqual(set(rows.values()), set(self.ids))


if __name__ == "__main__":
    unittest.main()
