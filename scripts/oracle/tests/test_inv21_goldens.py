"""The INV-21 goldens (S0022; spec design/specs/mirror/inv21-goldens.md; INV-21, ADR-0026, ADR-0055, BL-0021).

The real corpus passes every check in `inv21`; each check has a must-not-pass test (EP-29): a corrupted copy of the
corpus that the check refuses, naming the specific rule, and a near-miss ledger per feature detector.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inv21  # noqa: E402


class RealCorpus(unittest.TestCase):
    def test_the_goldens_agree_with_the_spec_and_every_check_passes(self) -> None:
        self.assertEqual(inv21.run_all(), [])

    def test_pinned_counts(self) -> None:
        # Inventory and corpus sizes, so a silently shrunk corpus or inventory is noticed (journal Hand-off).
        doc = json.loads((inv21.CORPUS / "goldens.json").read_text(encoding="utf-8"))
        keys = tomllib.loads((inv21.CORPUS / "keys.toml").read_text(encoding="utf-8"))["key"]
        self.assertEqual(len(doc["cases"]), 12)
        self.assertEqual(sum(len(c["transactions"]) for c in doc["cases"].values()), 77)
        self.assertEqual(len(keys), 22)
        self.assertEqual(sum(k["class"] == "read" for k in keys), 18)


def have_tag(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(inv21.REPO), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                          capture_output=True).returncode == 0


class FirstDerivation(unittest.TestCase):
    @unittest.skipUnless(have_tag(inv21.LEGACY_TAG), "the v0.2.2 tag is not in this clone")
    def test_the_released_code_still_gives_the_committed_goldens(self) -> None:
        import gen_inv21_goldens
        self.assertEqual(gen_inv21_goldens.generate(), (inv21.CORPUS / "goldens.json").read_text(encoding="utf-8"))


class Mutated(unittest.TestCase):
    """A copy of the corpus with one corruption; run_all must report the named rule."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.corpus = Path(self._tmp.name) / "inv21"
        shutil.copytree(inv21.CORPUS, self.corpus)
        self.doc = json.loads((self.corpus / "goldens.json").read_text(encoding="utf-8"))
        self.invariants = Path(self._tmp.name) / "INVARIANTS.md"
        shutil.copy(inv21.REPO / "design" / "INVARIANTS.md", self.invariants)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def save(self) -> None:
        (self.corpus / "goldens.json").write_text(json.dumps(self.doc), encoding="utf-8")

    def problems(self) -> list[str]:
        self.save()
        return inv21.run_all(self.corpus, self.invariants)

    def assertReports(self, fragment: str) -> None:
        problems = self.problems()
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def rows(self, case: str) -> list[dict]:
        return self.doc["cases"][case]["transactions"]

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.corpus / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1), encoding="utf-8")

    # --- goldens vs Beancount and the spec ---
    def test_a_dropped_golden_row(self) -> None:
        self.rows("fallback-twins").pop(1)
        self.assertReports("not exactly Beancount's")

    def test_reordered_golden_rows(self) -> None:
        r = self.rows("source-account")
        r[0], r[1] = r[1], r[0]
        self.assertReports("not exactly Beancount's")

    def test_a_changed_id(self) -> None:
        r = self.rows("fallback-twins")[2]
        r["transaction_id"] = r["v022_transaction_id"] = r["sha256"] + "-5"
        self.assertReports("transaction_id golden")

    def test_twins_given_no_ordinal(self) -> None:
        for r in self.rows("fallback-twins")[:3]:
            r["transaction_id"] = r["v022_transaction_id"] = r["sha256"]
        self.assertReports("transaction_id golden")

    def test_hash_and_input_changed_together(self) -> None:
        import hashlib
        r = self.rows("source-account")[1]
        r["hash_input"] = r["hash_input"].replace("Expenses:Food", "Assets:Bank")
        r["sha256"] = hashlib.sha256(r["hash_input"].encode()).hexdigest()
        self.assertReports("hash_input golden")

    def test_a_hash_that_is_not_the_hash_of_its_input(self) -> None:
        self.rows("custom-roots")[0]["hash_input"] += " "
        self.assertReports("not the SHA-256 of hash_input")

    def test_metadata_json_changed(self) -> None:
        r = self.rows("metadata-keys")[0]
        r["metadata_json"] = r["metadata_json"].replace('"memo"', '"Memo"')
        self.assertReports("metadata_json golden")

    def test_posting_metadata_json_changed(self) -> None:
        self.rows("metadata-keys")[3]["posting_metadata_json"][1] = None
        self.assertReports("posting_metadata_json golden")

    def test_a_missing_directive(self) -> None:
        self.doc["cases"]["metadata-keys"]["directives"].pop()
        self.assertReports("directive metadata JSON differs")

    def test_directives_pinned_where_not_declared(self) -> None:
        self.doc["cases"]["ids-legacy"]["directives"] = []
        self.assertReports("directives pinned without directive_metadata")

    def test_a_ledger_that_beancount_refuses(self) -> None:
        self.edit("custom-roots/main.beancount", "Gastos:Comida    20.00 EUR", "Gastos:Comida    21.00 EUR")
        self.assertReports("Beancount reports")

    # --- the v0.2.2-equality promise ---
    def test_a_v04_id_that_differs_without_an_override(self) -> None:
        self.rows("ids-legacy")[1]["transaction_id"] = "something-else"
        self.assertReports("without an override")

    def test_an_override_on_a_case_without_import_key(self) -> None:
        self.edit("cases.toml", "[metadata-keys]", '[custom-roots.v04_overrides]\n"main.beancount:17" = "x"\n\n[metadata-keys]')
        self.assertReports("v04_overrides on a case without a posting import-key")

    def test_an_idle_override(self) -> None:
        old = self.rows("import-key")[4]["v022_transaction_id"]
        self.edit("cases.toml", '"main.beancount:60"', f'"main.beancount:37" = "{old}"\n"main.beancount:60"')
        self.rows("import-key")[4]["transaction_id"] = old
        self.assertReports("an override equal to v0.2.2's id")

    def test_an_override_that_is_not_its_golden(self) -> None:
        self.edit("cases.toml", '"k:v1:ofx:FIT0001"', '"k:v1:ofx:FIT9999"')
        self.assertReports("is not its v04_overrides value")

    def test_an_override_of_an_arbitrary_text(self) -> None:
        self.edit("cases.toml", '"main.beancount:60" = "35c4', '"main.beancount:60" = "zz35c4')
        self.rows("import-key")[8]["transaction_id"] = "zz" + self.rows("import-key")[8]["transaction_id"]
        self.assertReports("neither `k:` nor a content hash")

    def test_an_override_for_no_transaction(self) -> None:
        self.edit("cases.toml", '"main.beancount:60"', '"main.beancount:61" = "k:x"\n"main.beancount:60"')
        self.assertReports("which is no transaction")

    # --- ledgers v0.2.2 could not read ---
    def test_a_v022_id_claimed_on_a_ledger_v022_could_not_read(self) -> None:
        self.rows("source-account-amount")[0]["v022_transaction_id"] = "x"
        self.assertReports("a v0.2.2 id on a ledger v0.2.2 could not read")

    def test_a_spec_only_hash_input_changed_with_its_golden(self) -> None:
        self.edit("cases.toml", '|0|10.00 USD"', '|10.00 USD|10.00 USD"')
        self.assertReports("hash_input is not its v04_expected value")

    def test_v04_expected_missing_a_transaction(self) -> None:
        self.edit("cases.toml", '"main.beancount:10" = "2020-07-20', '"main.beancount:11" = "2020-07-20')
        self.assertReports("v04_expected must give the hash input of exactly every transaction")

    def test_v04_expected_leaving_a_transaction_out(self) -> None:
        self.edit("cases.toml", '"main.beancount:10" = "2020-07-20|Grocer|amount as source account|0|10.00 USD"', "")
        self.assertReports("v04_expected must give the hash input of exactly every transaction")

    def test_v022_fails_without_v04_expected_or_reason(self) -> None:
        self.edit("cases.toml", "[source-account-amount.v04_expected]", "[source-account-amount.not_expected]")
        problems = self.problems()
        self.assertTrue(any("go together" in p for p in problems), problems)

    def test_v022_fails_with_an_empty_reason(self) -> None:
        self.edit("cases.toml", 'v022_fails = "v0.2.2 compares', 'v022_fails = " "\nnote_was = "v0.2.2 compares')
        self.assertReports("v022_fails must say why")

    def test_v022_fails_with_overrides(self) -> None:
        self.edit("cases.toml", "[source-account-amount.v04_expected]",
                  '[source-account-amount.v04_overrides]\n"main.beancount:10" = "k:x"\n\n[source-account-amount.v04_expected]')
        self.assertReports("has no v0.2.2 ids to override")

    def test_a_missing_v022_id_elsewhere(self) -> None:
        self.rows("custom-roots")[0]["v022_transaction_id"] = None
        self.assertReports("without an override")

    # --- labels and coverage ---
    def test_a_declared_feature_that_is_not_there(self) -> None:
        self.edit("cases.toml", 'features = ["custom-root-names",', 'features = ["custom-root-names", "pipe-collision",')
        self.assertReports("declares 'pipe-collision' but its detector does not find it")

    def test_a_feature_no_case_declares(self) -> None:
        self.edit("cases.toml", '"thousands-separator", "exponent-amount"', '"exponent-amount"')
        self.assertReports("coverage: no case declares 'thousands-separator'")

    def test_an_unknown_feature(self) -> None:
        self.edit("cases.toml", '"padding-transaction"]', '"padding-transaction", "made-up"]')
        self.assertReports("unknown features")

    def test_an_unknown_field(self) -> None:
        self.edit("cases.toml", "[fallback-twins]\n", "[fallback-twins]\nskip = true\n")
        self.assertReports("unknown fields")

    def test_a_case_with_no_features(self) -> None:
        self.edit("cases.toml", 'features = ["twins", "twins-differ-outside-hash"]\n\n[twins-reorder-b]',
                  'features = []\n\n[twins-reorder-b]')
        self.assertReports("twins-reorder-a: no features declared")

    def test_a_variant_of_no_case(self) -> None:
        self.edit("cases.toml", 'variant_of = "twins-reorder-a"', 'variant_of = "twins-reorder-z"')
        self.assertReports("variant_of names no case")

    def test_a_folder_without_a_table(self) -> None:
        shutil.copytree(self.corpus / "custom-roots", self.corpus / "extra-case")
        self.assertReports("extra-case: a case folder without a cases.toml table")

    def test_a_case_without_goldens(self) -> None:
        del self.doc["cases"]["custom-roots"]
        self.assertReports("custom-roots: in cases.toml or goldens.json but not both")

    def test_a_variant_whose_ids_do_not_change(self) -> None:
        shutil.copy(self.corpus / "twins-reorder-a" / "main.beancount", self.corpus / "twins-reorder-b" / "main.beancount")
        self.doc["cases"]["twins-reorder-b"] = self.doc["cases"]["twins-reorder-a"]
        self.assertReports("declares 'reorder-variant' but its detector does not find it")

    def test_header(self) -> None:
        self.doc["legacy_tag"] = "v0.3.0"
        self.doc["beancount"] = "3.1.0"
        del self.doc["legacy_code"]["backend/app/core/constants.py"]
        problems = self.problems()
        for fragment in ("legacy_tag is", "produced with Beancount", "legacy_code does not name"):
            self.assertTrue(any(fragment in p for p in problems), fragment)

    # --- the key inventory ---
    def keys_edit(self, old: str, new: str) -> None:
        self.edit("keys.toml", old, new)

    def test_an_inventory_key_missing_from_the_ledger(self) -> None:
        self.edit("metadata-keys/main.beancount", '  source_rule: "example-bank/debit"\n', "")
        self.assertReports("inventory key 'source_rule' (transaction) is not on the golden ledger")

    def test_a_ledger_key_missing_from_the_inventory(self) -> None:
        self.keys_edit('[[key]]\nkey = "source_rule"', '[[ignored]]\nkey = "source_rule"')
        self.assertReports("key 'source_rule' (transaction) is neither in keys.toml")

    def test_user_keys_hiding_an_inventory_key(self) -> None:
        self.edit("cases.toml", 'user_keys = ["issuer",', 'user_keys = ["memo", "issuer",')
        self.assertReports("user_keys hides the inventory key 'memo'")

    def test_a_user_key_not_on_the_ledger(self) -> None:
        self.edit("cases.toml", 'user_keys = ["issuer",', 'user_keys = ["ghost", "issuer",')
        self.assertReports("user key 'ghost' is not on the ledger")

    def test_an_inv21_key_passed_through(self) -> None:
        self.keys_edit('key = "external_id_type"\non = "transaction"\nclass = "read"',
                       'key = "external_id_type"\non = "transaction"\nclass = "pass"')
        self.assertReports("INV-21 names 'external_id_type' (transaction), which must be listed as read")

    def test_a_read_key_without_a_cited_reader(self) -> None:
        self.keys_edit('reader = "the close reason (CAP-ACCT-CLOSE)"', 'reader = "the close reason"')
        self.assertReports("a read key needs its v0.4 reader")

    def test_a_bad_class_or_place(self) -> None:
        self.keys_edit('key = "type"\non = "commodity"\nclass = "pass"', 'key = "type"\non = "price"\nclass = "keep"')
        problems = self.problems()
        self.assertTrue(any("`on` must be one of" in p for p in problems), problems)
        self.assertTrue(any("class must be read or pass" in p for p in problems), problems)

    def test_no_writers(self) -> None:
        self.keys_edit('writers = ["v0.2.x backend/app/core/beancount_engine.py:421 (close_account)"]', 'writers = []')
        self.assertReports("writers must name at least one writer")

    def test_an_extra_inventory_field(self) -> None:
        self.keys_edit('key = "reason"\non = "close"', 'key = "reason"\nstatus = "kept"\non = "close"')
        self.assertReports("fields must be key, on, class, writers")

    def test_an_inventory_key_on_the_wrong_directive(self) -> None:
        self.edit("metadata-keys/main.beancount", '  memo: "POS 1234 GROCER"\n', '  memo: "POS 1234 GROCER"\n  reason: "x"\n')
        self.assertReports("key 'reason' (transaction) is neither in keys.toml")

    def test_inv21_rule_not_found(self) -> None:
        text = self.invariants.read_text(encoding="utf-8")
        self.invariants.write_text(text.replace("### INV-21: Data contract", "### INV-21 Data contract"), encoding="utf-8")
        self.assertReports("INV-21's rule not found")

    def test_a_key_listed_twice(self) -> None:
        text = (self.corpus / "keys.toml").read_text(encoding="utf-8")
        (self.corpus / "keys.toml").write_text(text + '\n[[key]]\nkey = "reason"\non = "close"\nclass = "pass"\n'
                                               'writers = ["x"]\n', encoding="utf-8")
        self.assertReports("listed twice")

    def test_a_new_key_named_by_inv21(self) -> None:
        text = self.invariants.read_text(encoding="utf-8")
        self.invariants.write_text(text.replace("transaction-level `ofx_memo`", "transaction-level `ofx_memo`, `ofx_fitid`"),
                                   encoding="utf-8")
        self.assertReports("INV-21 now names keys this check does not know")


OPENS = """
option "operating_currency" "USD"
2020-01-01 open Assets:Bank USD
2020-01-01 open Assets:Savings USD
2020-01-01 open Liabilities:Card USD
2020-01-01 open Expenses:Food USD
2020-01-01 open Expenses:Snacks USD
2020-01-01 open Income:Salary USD
2020-01-01 open Equity:Opening USD
"""

COFFEE = '2020-02-01 * "Cafe" "coffee"\n  Expenses:Food  2 USD\n  Assets:Bank\n'
SNACK = '2020-02-01 * "Cafe" "coffee"\n  Expenses:Snacks  2 USD\n  Assets:Bank\n'

# Per feature: a ledger that comes close but does not show it. The detector must say no (EP-29).
NEAR_MISS: dict[str, str] = {
    "legacy-id": '2020-02-01 * "A" "b"\n  ids: "x"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "legacy-transaction-id": '2020-02-01 * "A" "b"\n  id: "x"\n  transaction_id: "y"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "id-over-transaction-id": '2020-02-01 * "A" "b"\n  transaction_id: "y"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "non-text-id": '2020-02-01 * "A" "b"\n  id: "42"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "shared-legacy-id": '2020-02-01 * "A" "b"\n  id: "x"\n  Expenses:Food  1 USD\n  Assets:Bank\n\n'
                        '2020-02-01 * "A" "b"\n  id: "x2"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "id-over-import-key": '2020-02-01 * "A" "b"\n  id: "x"\n  import-key: "v1:ofx:A"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "identified-twin-takes-no-ordinal": COFFEE + "\n" + COFFEE.replace('"coffee"\n', '"coffee"\n  id: "x"\n'),
    "twins": COFFEE + "\n" + COFFEE.replace('"coffee"\n', '"coffee"\n  id: "x"\n'),
    "twins-differ-outside-hash": COFFEE + "\n" + COFFEE,
    "pipe-collision": COFFEE + "\n" + SNACK,
    "no-payee": '2020-02-01 * "" "b"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "non-ascii": '2020-02-01 * "A" "b"\n  note: "Café"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "padded-text": '2020-02-01 * "   " "Corner  Shop\ta"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "inner-whitespace": '2020-02-01 * " Cafe " "  coffee to go  "\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "blank-text": '2020-02-01 * " a " ""\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "empty-text-payee": '2020-02-01 * "narration only"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "unicode-space": '2020-02-01 * "Café ₹" " a\tb "\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "multi-line-text": '2020-02-01 * "A\tB" "line one, line two"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "whitespace-only-difference": COFFEE + "\n" + COFFEE.replace('"Cafe"', '"Cafx"') + "\n" + COFFEE,
    "padding-transaction": '2020-02-01 ! "A" "pending"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "lineno-tie": COFFEE + "\n" + COFFEE,
    "source-account-key": '2020-02-01 * "A" "b"\n  source_account: "Assets:Bank"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "empty-source-account": '2020-02-01 * "A" "b"\n  source_account: ""\n  Assets:Bank  -1 USD\n  Expenses:Food\n',
    "padded-source-account": '2020-02-01 * "A" "b"\n  source_account: "  "\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "unmatched-source-account": '2020-02-01 * "A" "b"\n  source_account: " Assets:Other"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "falsy-non-text-source-account": '2020-02-01 * "A" "b"\n  source_account: 1\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "liabilities-source": '2020-02-01 * "A" "b"\n  Liabilities:Card  -1 USD\n  Expenses:Food\n',
    "no-balance-sheet-posting": '2020-02-01 * "A" "b"\n  source_account: "Expenses:Food"\n  Expenses:Food  1 USD\n'
                                '  Income:Salary\n',
    "repeated-source-posting": '2020-02-01 * "A" "b"\n  Expenses:Food  2 USD\n  Assets:Bank  -1 USD\n  Assets:Bank  -1 USD\n',
    "interpolated-source-amount": '2020-02-01 * "A" "b"\n  Assets:Bank  -1 USD\n  Expenses:Food\n',
    "thousands-separator": '2020-02-01 * "A" "1,000.00 here"\n  ; 1,000.00 USD in a comment\n  Expenses:Food  1000.00 USD\n'
                           '  Assets:Bank\n',
    "custom-root-names": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n  Income:Salary\n',
    "import-key-id": '2020-02-01 * "A" "b"\n  import-key: "v1:ofx:A"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "import-key-second-posting": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n    import-key: "v1:ofx:A"\n  Assets:Bank\n',
    "import-key-several-postings": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n    import-key: "v1:ofx:A"\n'
                                   '  Assets:Bank\n    import-key: "v1:ofx:A"\n',
    "import-key-several-elements": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n  Assets:Bank\n    import-key: " v1:ofx:A "\n',
    "import-key-on-transaction": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n  Assets:Bank\n    import-key: "v1:ofx:A"\n',
    "exponent-amount": '2020-02-01 * "A" "b"\n  Expenses:Food  0.000001 USD\n  Assets:Bank\n',
    "empty-text-id": '2020-02-01 * "A" "b"\n  id: " "\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "truthy-non-text-source-account": '2020-02-01 * "A" "b"\n  source_account: TRUE\n  Expenses:Food  1 USD\n  Assets:Bank\n\n'
                                      '2020-02-01 * "A" "c"\n  source_account: 5\n  Expenses:Food  1 USD\n  Assets:Bank\n\n'
                                      '2020-02-01 * "A" "d"\n  source_account: "2020-01-01"\n  Expenses:Food  1 USD\n'
                                      '  Assets:Bank\n',
    "amount-source-account": '2020-02-01 * "A" "b"\n  source_account: "10.00 USD"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    "hash-shaped-legacy-id": '2020-02-01 * "A" "b"\n  transaction_id: "' + "ab" * 32 + '"\n  Expenses:Food  1 USD\n'
                             '  Assets:Bank\n\n' + COFFEE,
    "import-key-empty": '2020-02-01 * "A" "b"\n  Expenses:Food  1 USD\n  Assets:Bank\n    import-key: "  x"\n',
}
# Features whose near miss needs more than one ledger text or a case setting.
NEAR_MISS_SPECIAL = {"reorder-variant", "move-variant", "every-released-key"}


class Detectors(unittest.TestCase):
    def load(self, text: str, extra: dict[str, str] | None = None) -> inv21.Case:
        tmp = Path(self._tmp.name)
        folder = tmp / "src" / f"c{len(list((tmp / 'src').glob('*'))) if (tmp / 'src').exists() else 0}"
        folder.mkdir(parents=True)
        (folder / "main.beancount").write_text(textwrap.dedent(OPENS).lstrip() + "\n" + text, encoding="utf-8")
        for rel, body in (extra or {}).items():
            (folder / rel).write_text(body, encoding="utf-8")
        return inv21.load_case(folder, Path(tempfile.mkdtemp(dir=tmp)))

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def detect(self, feature: str, case: inv21.Case, cfg: dict | None = None,
               cases: dict | None = None, goldens: dict | None = None) -> bool:
        self.assertEqual(case.errors, [], f"{feature}: the near-miss ledger must load cleanly")
        rows = inv21.derive(case, False)["transactions"]
        return inv21.FEATURES[feature][1](case, rows, cases or {}, goldens or {}, cfg or {})

    def test_every_detector_has_a_near_miss(self) -> None:
        self.assertEqual(set(inv21.FEATURES), set(NEAR_MISS) | NEAR_MISS_SPECIAL)

    def test_near_misses_are_refused(self) -> None:
        for feature, text in NEAR_MISS.items():
            with self.subTest(feature=feature):
                self.assertFalse(self.detect(feature, self.load(text)))

    def test_the_lineno_tie_needs_two_files(self) -> None:
        main = COFFEE
        case = self.load(main, {"other.beancount": "\n" * 9 + COFFEE})
        # same date, but the included file's entry is not on main's line: no tie
        self.assertFalse(self.detect("lineno-tie", case))

    def test_variants(self) -> None:
        a = self.load(COFFEE + "\n" + SNACK)
        same = self.load(COFFEE + "\n" + SNACK)
        g = {a.name: inv21.derive(a, False), same.name: inv21.derive(same, False)}
        cs = {a.name: a, same.name: same}
        for feature in ("reorder-variant", "move-variant"):
            with self.subTest(feature=feature):
                self.assertFalse(self.detect(feature, same, {"variant_of": a.name}, cs, g))
        swapped = self.load(SNACK + "\n" + COFFEE)
        g[swapped.name], cs[swapped.name] = inv21.derive(swapped, False), swapped
        self.assertTrue(self.detect("reorder-variant", swapped, {"variant_of": a.name}, cs, g))
        self.assertFalse(self.detect("move-variant", swapped, {"variant_of": a.name}, cs, g))
        other = self.load(COFFEE + "\n" + COFFEE.replace("Cafe", "Bar"))
        g[other.name], cs[other.name] = inv21.derive(other, False), other
        self.assertFalse(self.detect("reorder-variant", other, {"variant_of": a.name}, cs, g))

    def test_a_hash_shaped_legacy_id_is_no_fallback_twin(self) -> None:
        import hashlib
        h = hashlib.sha256(b"2020-02-01|Cafe|coffee|-2 USD|Assets:Bank").hexdigest()
        case = self.load(COFFEE.replace('"coffee"\n', f'"coffee"\n  transaction_id: "{h}"\n') + "\n" + COFFEE)
        rows = inv21.derive(case, False)["transactions"]
        self.assertEqual(rows[0]["transaction_id"], rows[1]["transaction_id"])  # the collision itself
        for feature in ("twins", "twins-differ-outside-hash", "identified-twin-takes-no-ordinal"):
            with self.subTest(feature=feature):
                self.assertFalse(self.detect(feature, case))
        self.assertTrue(self.detect("hash-shaped-legacy-id", case))

    def test_every_released_key_needs_directive_metadata(self) -> None:
        self.assertFalse(self.detect("every-released-key", self.load(COFFEE), {}))


class SpecDerivation(unittest.TestCase):
    """Small hand-checked values of the spec formula, independent of the goldens."""

    def test_hash_input_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src" / "x"
            src.mkdir(parents=True)
            (src / "main.beancount").write_text(textwrap.dedent(OPENS).lstrip() + "\n" + COFFEE + "\n" + COFFEE,
                                                encoding="utf-8")
            case = inv21.load_case(src, Path(tmp))
            self.assertEqual(inv21.hash_input(case.txns[0]), "2020-02-01|Cafe|coffee|-2 USD|Assets:Bank")
            ids = inv21.transaction_ids(case.txns)
            self.assertEqual(ids[1], ids[0] + "-1")


if __name__ == "__main__":
    unittest.main()
