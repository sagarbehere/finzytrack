"""Meta-tests for the mirror surface spec and its check (S0021; design/specs/mirror/).

The real spec must pass; every rule has a must-not-pass case built by breaking one thing in a copy of the real
spec (EP-29), and each way the check could be loosened or bypassed has its own case (EP-30). The legacy
inventories are pinned by counts and, when the legacy tags are in the clone, compared with what the exporters'
own `CREATE TABLE` statements produce.
"""

from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_mirror_surface as ms  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def have_tag(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                          capture_output=True).returncode == 0


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.real = ms.load(ms.SPEC)
        cls.refs = ms.known_refs(ROOT)
        cls.inbox = ms.open_inbox(ROOT)

    def spec(self) -> dict:
        return copy.deepcopy(self.real)

    def problems(self, spec: dict) -> list[str]:
        return ms.problems(spec, self.refs, self.inbox)

    def table(self, spec: dict, name: str) -> dict:
        return next(t for t in spec["table"] if t["name"] == name)

    def column(self, spec: dict, table: str, name: str) -> dict:
        return next(c for c in self.table(spec, table)["column"] if c["name"] == name)

    def refused(self, spec: dict, fragment: str) -> None:
        found = self.problems(spec)
        self.assertTrue(any(fragment in p for p in found), f"expected a problem containing {fragment!r}, got {found}")


class RealSpec(Base):
    def test_the_spec_passes(self) -> None:
        self.assertEqual(self.problems(self.spec()), [])

    def test_pinned_inventories(self) -> None:
        # Counted from the exporters' DDL at S0021 (scratch run of ms.derive_tables on both tags).
        v022, v030 = self.real["legacy"]["v0.2.2"], self.real["legacy"]["v0.3.0"]
        self.assertEqual((len(v022["tables"]), sum(map(len, v022["tables"].values()))), (18, 121))
        self.assertEqual((len(v030["tables"]), sum(map(len, v030["tables"].values()))), (19, 126))
        self.assertEqual(v022["functions"], ["budget_for_range"])
        self.assertEqual(sorted(v030["functions"]), ["budget_for_range", "cash_instruments", "portfolio_returns",
                                                     "portfolio_series"])
        self.assertEqual((v022["tag"], v030["tag"]), ("v0.2.2", "legacy-final"))

    @unittest.skipUnless(have_tag("v0.2.2") and have_tag("legacy-final"), "legacy tags not in this clone")
    def test_pinned_inventories_match_the_legacy_code(self) -> None:
        self.assertEqual(ms.legacy_problems(self.real, ROOT), [])


class Derivation(unittest.TestCase):
    """The inventory is built by running the DDL, not by reading names."""

    def test_tables_come_from_executed_create_statements(self) -> None:
        src = '''
# CREATE TABLE in_a_comment (x TEXT)
DOC = "not a create: CREATE TABLE inside (x)"
def f(con):
    con.execute("""
        CREATE TABLE a (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            b TEXT NOT NULL, -- CREATE TABLE fake (y)
            c TEXT,
            UNIQUE(b, c)
        )""")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE INDEX i ON a(b)")
'''
        self.assertEqual(ms.derive_tables(src), {"a": [["id", "INTEGER"], ["b", "TEXT"], ["c", "TEXT"]],
                                                "meta": [["key", "TEXT"], ["value", "TEXT"]]})

    def test_functions_come_from_registrations(self) -> None:
        router = "registry.register(BudgetFn(reader))\n# registry.register(CommentedFn(reader))\n"
        sources = ['class BudgetFn(ComputeFunction):\n    name = "budget_for_range"\n',
                   'class UnusedFn(ComputeFunction):\n    name = "never_registered"\n',
                   'class CommentedFn(ComputeFunction):\n    name = "commented_out"\n']
        self.assertEqual(ms.derive_functions(router, sources), ["budget_for_range"])

    def test_inventory_mismatch_is_reported(self) -> None:
        pinned = {"tag": "t", "tables": {"a": [["x", "TEXT"], ["y", "INTEGER"]]}, "functions": ["f"]}
        same = {"a": [["x", "TEXT"], ["y", "INTEGER"]]}
        self.assertEqual(ms.inventory_problems("v", pinned, same, ["f"]), [])
        self.assertTrue(ms.inventory_problems("v", pinned, {"a": same["a"] + [["z", "TEXT"]]}, ["f"]))  # column hidden
        self.assertTrue(ms.inventory_problems("v", pinned, {**same, "b": [["q", "TEXT"]]}, ["f"]))  # table hidden
        self.assertTrue(ms.inventory_problems("v", pinned, {"a": same["a"][::-1]}, ["f"]))  # order is the view's order
        self.assertTrue(ms.inventory_problems("v", pinned, {"a": [["x", "TEXT"], ["y", "TEXT"]]}, ["f"]))  # type
        self.assertTrue(ms.inventory_problems("v", pinned, same, ["f", "g"]))  # function hidden

    def test_backlog_ids_come_from_id_tables_only(self) -> None:
        text = ("| Status | Meaning |\n|---|---|\n| open | not done |\n\n"
                "| ID | Title |\n|---|---|\n| N1 | a finding |\n| L1.M5 | a double |\n| BL-0001 | an item |\n\n"
                "| Double | Canonical |\n|---|---|\n| L2-02 | I7 |\n")
        self.assertEqual(ms.backlog_ids(text), {"N1", "L1.M5", "BL-0001"})


class Fields(Base):
    def test_a_missing_or_extra_field(self) -> None:
        s = self.spec()
        del self.column(s, "postings", "amount")["sign"]
        self.refused(s, "postings.amount: fields differ (missing ['sign']")
        s = self.spec()
        self.column(s, "postings", "amount")["colour"] = "x"
        self.refused(s, "postings.amount: fields differ (missing [], extra ['colour'])")
        s = self.spec()
        del self.table(s, "prices")["as_of"]
        self.refused(s, "prices: fields differ (missing ['as_of']")

    def test_empty_text(self) -> None:
        s = self.spec()
        self.column(s, "prices", "date")["meaning"] = "  "
        self.refused(s, "prices.date: meaning is empty")
        s = self.spec()
        self.table(s, "prices")["row"] = ""
        self.refused(s, "prices: row is empty")

    def test_closed_vocabularies(self) -> None:
        for field, bad, rule in (("type", "text", "format decimal is stored as TEXT, not 'text'"),
                                 ("type", "REAL", "format decimal is stored as TEXT, not 'REAL'"),
                                 ("format", "moneyish", "unknown format"), ("origin", "v0.2", "unknown origin"),
                                 ("status", "maybe", "status must be"), ("change", "tweaked", "unknown change"),
                                 ("user_sql", "yes", "user_sql must be")):
            with self.subTest(field=field, bad=bad):
                s = self.spec()
                self.column(s, "prices", "quote_number")[field] = bad
                self.refused(s, f"prices.quote_number: {rule}")
        s = self.spec()
        self.table(s, "prices")["as_of"] = "yesterday"
        self.refused(s, "prices: as_of must be")
        s = self.spec()
        self.table(s, "prices")["origin"] = "v0.1"
        self.refused(s, "prices: unknown origin")
        s = self.spec()
        self.table(s, "prices")["status"] = "maybe"
        self.refused(s, "prices: status must be")

    def test_money_is_text_with_a_sign(self) -> None:
        s = self.spec()
        self.column(s, "postings", "amount")["type"] = "INTEGER"
        self.refused(s, "postings.amount: format decimal is stored as TEXT")
        s = self.spec()
        self.column(s, "postings", "amount")["sign"] = "n/a"
        self.refused(s, "postings.amount: an exact number needs a sign")
        s = self.spec()
        self.column(s, "postings", "amount")["sign"] = ""
        self.refused(s, "postings.amount: an exact number needs a sign")
        s = self.spec()
        self.column(s, "postings", "currency")["sign"] = "positive is a debit"
        self.refused(s, "postings.currency: sign is only for exact numbers")

    def test_format_fixes_the_type(self) -> None:
        s = self.spec()
        self.column(s, "postings", "transaction_date")["type"] = "INTEGER"
        self.refused(s, "postings.transaction_date: format date is stored as TEXT")
        s = self.spec()
        self.column(s, "postings", "year")["type"] = "TEXT"
        self.refused(s, "postings.year: format int is stored as INTEGER")

    def test_names(self) -> None:
        s = self.spec()
        s["table"].append(copy.deepcopy(self.table(s, "prices")))
        self.refused(s, "duplicate table")
        s = self.spec()
        cols = self.table(s, "prices")["column"]
        cols.append(copy.deepcopy(cols[0]))
        self.refused(s, "duplicate column")
        s = self.spec()
        self.table(s, "prices")["base"] = "prices"
        self.refused(s, "prices: base must be m_prices")
        s = self.spec()
        self.table(s, "prices").update(name="r_prices", base="m_r_prices")
        self.refused(s, "r_prices: a public name")
        s = self.spec()
        self.table(s, "prices").update(name="m_prices", base="m_m_prices")
        self.refused(s, "m_prices: a public name")


class Shape(Base):
    def test_surface_version(self) -> None:
        for bad in (0, "1", None):
            with self.subTest(version=bad):
                s = self.spec()
                s["surface_version"] = bad
                self.refused(s, "surface_version must be a positive integer")

    def test_a_table_without_columns(self) -> None:
        s = self.spec()
        self.table(s, "events")["column"] = []
        self.refused(s, "events: no columns")

    def test_the_legacy_inventories_are_required(self) -> None:
        s = self.spec()
        del s["legacy"]["v0.3.0"]
        self.refused(s, "legacy: the v0.2.2 and v0.3.0 inventories are required")

    def test_compatibility_function_fields(self) -> None:
        s = self.spec()
        s["compat_function"][0]["extra"] = "x"
        self.refused(s, "budget_for_range: fields differ")
        s = self.spec()
        s["compat_function"][0]["status"] = "someday"
        self.refused(s, "budget_for_range: status must be")


class V022Accounting(Base):
    """ADR-0048: every v0.2.2 table and column keeps its name; none is dropped."""

    def test_a_same_column_whose_type_changed(self) -> None:
        s = self.spec()
        self.column(s, "ledger_errors", "line_number").update(type="TEXT", format="text")
        self.refused(s, "ledger_errors.line_number: type TEXT differs from v0.2.2's INTEGER")
        s = self.spec()
        self.column(s, "postings", "cost_date").update(type="INTEGER", format="int")
        self.refused(s, "postings.cost_date: type INTEGER differs from v0.3.0's TEXT")
        s = self.spec()  # a declared change may change the type
        self.column(s, "ledger_errors", "line_number").update(type="TEXT", format="text", change="changed",
                                                               cites=["ADR-0048"], why="x")
        self.assertEqual(self.problems(s), [])

    def test_a_v022_table_losing_its_public_view(self) -> None:
        s = self.spec()
        for c in self.table(s, "postings")["column"]:
            c["user_sql"] = False
        self.refused(s, "postings: a v0.2.2 table keeps its public view r_postings")

    def test_a_v022_table_left_out(self) -> None:
        s = self.spec()
        s["table"] = [t for t in s["table"] if t["name"] != "training_data"]
        self.refused(s, "training_data: a v0.2.2 table must keep its name")

    def test_a_v022_column_left_out(self) -> None:
        s = self.spec()
        t = self.table(s, "balance_assertions")
        t["column"] = [c for c in t["column"] if c["name"] != "pad_date"]
        self.refused(s, "balance_assertions.pad_date: a v0.2.2 column must stay")

    def test_a_v022_column_hidden_from_the_public_view(self) -> None:
        s = self.spec()
        self.column(s, "postings", "source_account")["user_sql"] = False
        self.refused(s, "postings.source_account: the compatibility view reads r_postings")
        s = self.spec()
        for c in self.table(s, "meta")["column"]:  # a table with no public view is served from its base table
            self.assertFalse(c["user_sql"])
        self.assertEqual(self.problems(s), [])

    def test_a_v022_column_relabelled_as_new(self) -> None:
        s = self.spec()
        col = self.column(s, "postings", "source_account")
        col.update(origin="v0.4", change="new")
        self.refused(s, "postings.source_account: origin v0.4 is not what the legacy inventories say (v0.2.2)")

    def test_a_v022_item_dropped(self) -> None:
        s = self.spec()
        t = self.table(s, "postings")
        t["column"] = [c for c in t["column"] if c["name"] != "source_account"]
        s["dropped"].append({"item": "column:postings.source_account", "origin": "v0.2.2",
                             "reason": "unused", "cites": ["ADR-0048"]})
        self.refused(s, "cannot be dropped (ADR-0048) [postings.source_account]")
        s = self.spec()
        s["table"] = [t for t in s["table"] if t["name"] != "meta"]
        s["dropped"].append({"item": "table:meta", "origin": "v0.2.2", "reason": "internal", "cites": ["ADR-0015"]})
        self.refused(s, "cannot be dropped (ADR-0048) [meta]")

    def test_a_v022_function_without_a_compatibility_entry(self) -> None:
        s = self.spec()
        s["compat_function"] = []
        self.refused(s, "budget_for_range: a v0.2.2 compute function needs a compatibility entry")
        for field in ("message", "located", "served_by", "milestone"):
            with self.subTest(field=field):
                s = self.spec()
                s["compat_function"][0][field] = ""
                self.refused(s, f"budget_for_range: {field} is empty")
        s = self.spec()
        s["compat_function"][0]["cites"] = []
        self.refused(s, "budget_for_range: a compatibility entry needs a citation")
        s = self.spec()
        s["compat_function"].append(dict(s["compat_function"][0], name="portfolio_series"))
        s["dropped"] = [d for d in s["dropped"] if d["item"] != "function:portfolio_series"]
        self.refused(s, "portfolio_series: only v0.2.2 compute functions")


class V030Accounting(Base):
    def test_a_v030_item_neither_kept_nor_dropped(self) -> None:
        s = self.spec()
        s["dropped"] = [d for d in s["dropped"] if d["item"] != "table:latest_prices"]
        self.refused(s, "latest_prices: a v0.3.0 table must be kept or dropped")
        s = self.spec()
        t = self.table(s, "postings")
        t["column"] = [c for c in t["column"] if c["name"] != "cost_date"]
        self.refused(s, "postings.cost_date: a v0.3.0 column must be kept or dropped")
        s = self.spec()
        s["dropped"] = [d for d in s["dropped"] if d["item"] != "function:portfolio_series"]
        self.refused(s, "portfolio_series: a v0.3.0 compute function must be kept or dropped")

    def test_a_v030_item_both_kept_and_dropped(self) -> None:
        s = self.spec()
        s["dropped"].append({"item": "column:postings.cost_date", "origin": "v0.3.0", "reason": "x",
                             "cites": ["ADR-0048"]})
        self.refused(s, "dropped column:postings.cost_date: also kept")

    def test_a_drop_without_a_citation(self) -> None:
        s = self.spec()
        next(d for d in s["dropped"] if d["item"] == "table:latest_prices")["cites"] = []
        self.refused(s, "dropped table:latest_prices: a drop needs a reason and a citation")
        s = self.spec()
        next(d for d in s["dropped"] if d["item"] == "table:latest_prices")["reason"] = ""
        self.refused(s, "dropped table:latest_prices: a drop needs a reason and a citation")
        s = self.spec()
        next(d for d in s["dropped"] if d["item"] == "table:latest_prices")["cites"] = ["ADR-9999"]
        self.refused(s, "dropped table:latest_prices: citation 'ADR-9999'")
        s = self.spec()
        next(d for d in s["dropped"] if d["item"] == "table:latest_prices")["origin"] = "v0.4"
        self.refused(s, "dropped table:latest_prices: origin v0.4 is not v0.3.0")
        s = self.spec()
        del next(d for d in s["dropped"] if d["item"] == "table:latest_prices")["reason"]
        self.refused(s, "dropped table:latest_prices: fields differ")

    def test_a_drop_of_something_no_version_had(self) -> None:
        s = self.spec()
        s["dropped"].append({"item": "table:imaginary", "origin": "v0.3.0", "reason": "x", "cites": ["ADR-0048"]})
        self.refused(s, "dropped table:imaginary: no legacy version had it")
        s = self.spec()
        s["dropped"].append({"item": "gadget:x", "origin": "v0.3.0", "reason": "x", "cites": ["ADR-0048"]})
        self.refused(s, "dropped gadget:x: no legacy version had it")
        s = self.spec()
        s["dropped"].append({"item": "column:postings.nothing", "origin": "v0.3.0", "reason": "x", "cites": ["ADR-0048"]})
        self.refused(s, "dropped column:postings.nothing: no legacy version had it")


class Origins(Base):
    def test_a_false_origin(self) -> None:
        s = self.spec()
        self.column(s, "postings", "account_kind").update(origin="v0.2.2", change="same")
        self.refused(s, "postings.account_kind: origin v0.2.2 is not what")
        s = self.spec()
        self.column(s, "postings", "account_kind").update(origin="v0.3.0", change="same")
        self.refused(s, "postings.account_kind: origin v0.3.0 is not what")
        s = self.spec()
        self.column(s, "postings", "cost_date")["origin"] = "v0.2.2"
        self.refused(s, "postings.cost_date: origin v0.2.2 is not what")
        s = self.spec()
        self.column(s, "postings", "cost_date").update(origin="v0.4", change="new")
        self.refused(s, "postings.cost_date: origin v0.4 is not what")
        s = self.spec()
        self.table(s, "prices")["origin"] = "v0.4"
        self.refused(s, "prices: origin v0.4 is not what")

    def test_a_new_column_cannot_say_same_or_changed(self) -> None:
        s = self.spec()
        self.column(s, "postings", "account_kind")["change"] = "same"
        self.refused(s, "postings.account_kind: change `new` is for v0.4")
        s = self.spec()
        self.column(s, "postings", "amount")["change"] = "new"
        self.refused(s, "postings.amount: change `new` is for v0.4")


class Citations(Base):
    def test_a_change_without_a_citation(self) -> None:
        s = self.spec()
        self.column(s, "postings", "source_account")["cites"] = []
        self.refused(s, "postings.source_account: a changed column needs")
        s = self.spec()
        self.column(s, "postings", "source_account")["why"] = " "
        self.refused(s, "postings.source_account: a changed column needs")

    def test_a_citation_that_does_not_resolve(self) -> None:
        for bad in ("ADR-9999", "CAP-NO-SUCH-ROW", "BL-9999", "INV-99", "owner:20990101-nothing.md", "ADR-55",
                    "see above", "ADR-0048 ", "Status", "Double", "ID"):
            with self.subTest(cite=bad):
                s = self.spec()
                self.column(s, "postings", "source_account")["cites"] = [bad]
                found = [p for p in self.problems(s) if p.startswith("postings.source_account: ")]
                self.assertTrue(any(("citation" in p or "not an open file" in p) and bad.strip() in p for p in found),
                                found)
        s = self.spec()
        self.column(s, "postings", "source_account")["cites"] = "ADR-0055"
        self.refused(s, "postings.source_account: cites must be a list")
        s = self.spec()
        self.column(s, "postings", "source_account")["cites"] = [55]
        self.refused(s, "postings.source_account: citation 55 is not text")

    def test_table_and_function_citations_resolve_too(self) -> None:
        s = self.spec()
        self.table(s, "prices")["cites"] = ["ADR-9999"]
        self.refused(s, "prices: citation 'ADR-9999'")
        s = self.spec()
        s["compat_function"][0]["cites"] = ["CAP-NO-SUCH-ROW"]
        self.refused(s, "budget_for_range: citation 'CAP-NO-SUCH-ROW'")
        s = self.spec()
        self.column(s, "postings", "account_kind")["cites"] = ["ADR-9999"]  # also on columns that did not change
        self.refused(s, "postings.account_kind: citation 'ADR-9999'")


class Status(Base):
    def test_owner_status_needs_an_open_inbox_file(self) -> None:
        s = self.spec()
        self.column(s, "prices", "date")["status"] = "owner:20990101-nothing.md"
        self.refused(s, "prices.date: status must be")
        answered = sorted(p.name for p in (ROOT / "design/inbox/answered").glob("*.md"))
        s = self.spec()
        self.column(s, "prices", "date")["status"] = f"owner:{answered[0]}"
        self.refused(s, "prices.date: status must be")  # an answered question must be folded into the row
        s = self.spec()
        self.column(s, "prices", "date")["status"] = "owner:README.md"
        self.refused(s, "prices.date: status must be")
        s = self.spec()
        self.table(s, "prices").update(as_of="owner:20990101-nothing.md", status="owner:20990101-nothing.md")
        self.refused(s, "prices: as_of must be")

    def test_a_decided_row_cannot_point_at_an_open_question(self) -> None:
        s = self.spec()
        col = self.column(s, "prices", "date")
        col["meaning"] += " (waits for owner:20261025-mirror-as-of-date.md)"
        self.refused(s, "prices.date: a decided column cannot point at an open owner question")
        s = self.spec()
        self.column(s, "prices", "date")["cites"] = ["owner:20261025-mirror-as-of-date.md"]
        self.refused(s, "prices.date: a decided column cannot point at an open owner question")
        s = self.spec()
        self.table(s, "prices")["cites"] = ["ADR-0048", "owner:20261025-mirror-as-of-date.md"]
        self.refused(s, "prices: a draft table cannot point at an open owner question")
        s = self.spec()
        self.column(s, "prices", "id")["source"] += " See design/inbox/x.md."
        self.refused(s, "prices.id: a draft column cannot point at an open owner question")
        s = self.spec()
        self.table(s, "account_balances")["status"] = "decided"
        self.refused(s, "account_balances: a decided table cannot point at an open owner question")

    def test_an_owner_question_is_marked_where_it_applies(self) -> None:
        s = self.spec()
        self.column(s, "account_balances", "balance")["status"] = "decided"
        self.refused(s, "account_balances.balance: its table waits for the owner")
        s = self.spec()
        self.column(s, "lots", "label")["status"] = "draft"
        self.refused(s, "lots.label: its table waits for the owner")


if __name__ == "__main__":
    unittest.main()
