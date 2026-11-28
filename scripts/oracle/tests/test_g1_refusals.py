"""The G1 refusal-rate tool (S0163): its table, its cases, its guards, its output; one run end to end."""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import unittest
import unittest.mock
from pathlib import Path

from helpers import OPENS, LedgerDir

import g1_refusals as g1
import semantic_diff as sd

TXNS = """
2020-01-02 * "Corner Shop" "lunch with Priya"
  Expenses:Food  10.50 USD
  Assets:Cash  -10.50 USD
2020-01-03 * "Landlord" "rent"
  Expenses:Rent  700.00 USD
  Assets:Cash  -700.00 USD
"""
SECRETS = ("Corner Shop", "Priya", "Landlord", "Expenses:Food", "Expenses:Rent", "Assets:Cash", "10.50", "700.00")


def run_main(argv: list[str], runner: g1.Runner | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = g1.main(argv, runner=runner) if runner else g1.main(argv)
    return code, out.getvalue(), err.getvalue()


class Env(unittest.TestCase):
    """A temp T-local folder (`tier/`) with a ledger copy in `tier/books/`."""

    files = {"tier/books/main.beancount": OPENS + TXNS}

    def setUp(self) -> None:
        self.d = LedgerDir(self.files)
        self.base = self.d.path.resolve()
        self.tier = self.base / "tier"
        self.src = self.tier / "books"
        self.old = os.environ.get("FT_LOCAL_TIER_DIR")
        os.environ["FT_LOCAL_TIER_DIR"] = str(self.tier)
        self.ran: list[object] = []

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("FT_LOCAL_TIER_DIR", None)
        else:
            os.environ["FT_LOCAL_TIER_DIR"] = self.old
        self.d.close()

    def fake_runner(self, answers: dict[str, dict[str, object]] | None = None) -> g1.Runner:
        """A runner that answers each case from *answers* (default: saved) and records that it ran."""

        def runner(src: Path, root_file: str, work: Path, cases: list[dict[str, object]]) -> g1.RunnerResult:
            self.ran.append((src, root_file, work, len(cases)))
            lines = [json.dumps((answers or {}).get(str(c["id"]), {"id": c["id"], "outcome": "saved"})) for c in cases]
            return g1.RunnerResult(0, lines, "")

        return runner

    def argv(self, *extra: str, src: Path | None = None, root: str = "main.beancount") -> list[str]:
        return ["--ledger-dir", str(src or self.src), "--root-file", root, "--per-mutator", "2", *extra]


class TableTest(unittest.TestCase):
    """Must not pass: a refusal counted as justified without its decision's condition in the copy, or any key the
    table does not name counted as justified."""

    yes = g1.Facts(encrypted=True, push_region=True, edits_encrypted=True)
    no = g1.Facts(encrypted=False, push_region=False)

    def test_each_justified_key_needs_its_condition(self) -> None:
        for key in g1.JUSTIFIED:
            code, step, reason = key
            with self.subTest(key=key):
                verdict, cite = g1.classify(code, step or "apply", reason, self.yes)
                self.assertEqual(verdict, "justified")
                self.assertRegex(cite or "", r"ADR-\d{4}")
                self.assertEqual(g1.classify(code, step or "apply", reason, self.no)[0], "unjustified")

    def test_everything_else_is_unjustified(self) -> None:
        for code, step, reason in [
            ("INTERNAL", None, None),
            ("SOMETHING_NEW", "plan", "insert_in_push_region"),
            ("WRITE_REFUSED_CHECK_FAILED", "plan", "fault"),
            ("WRITE_REFUSED_CHECK_FAILED", "load", "insert_in_push_region"),
            ("WRITE_REFUSED_UNINTENDED_CHANGE", "diff", "not_intended_change"),
            ("WRITE_REFUSED_UNINTENDED_CHANGE", "apply", None),
            ("CHANGE_NOT_SAVED", None, "outside_folder"),
            ("NO_ANSWER", None, None),
            ("TOOL_ERROR", None, None),
        ]:
            with self.subTest(code=code, step=step, reason=reason):
                self.assertEqual(g1.classify(code, step, reason, self.yes), ("unjustified", None))

    def test_an_edit_of_a_plain_asc_file_is_justified_an_insert_beside_it_is_not(self) -> None:
        # The write corpus's `asc-plain`: Beancount loads a plain `.asc` include; the write core refuses any edit that
        # touches it, by its name (ADR-0010 guardrail 4, S0115: justified), and also an insert into another file of
        # that ledger (unjustified: no decision forbids it).
        ledger = g1.Facts(encrypted=True, push_region=False)
        refusal = {"outcome": "refused", "code": "WRITE_REFUSED_CHECK_FAILED", "step": "plan", "reason": "encrypted_file"}
        cases = [{"id": "upd-000", "mutator": "update_transaction", "model": "valid",
                  "edits": [{"kind": "replace", "path": "notes.beancount.ASC"}]},
                 {"id": "imp-000", "mutator": "import_append", "model": "valid",
                  "edits": [{"kind": "insert", "default_file": "main.beancount"}]}]
        _, summary = g1.report_of(cases, {"upd-000": refusal, "imp-000": refusal}, ledger)
        self.assertEqual((summary["justified"], summary["unjustified"]), (1, 1))

    def test_the_facts_are_read_from_the_copy(self) -> None:
        d = LedgerDir({"a/main.beancount": OPENS, "b/main.beancount": OPENS + "pushtag #trip\n",
                       "c/main.beancount": OPENS + 'include "secret.beancount.ASC"\n',
                       "c/secret.beancount.ASC": "x\n", "a/stray.gpg": "not in the ledger set\n"})
        try:
            for name, want in (("a", self.no), ("b", g1.Facts(False, True)), ("c", g1.Facts(True, False))):
                before = sd.parse_set(d.path / name, "main.beancount")
                self.assertEqual(g1.facts_of(d.path / name, before), want, name)
        finally:
            d.close()


class CasesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.d = LedgerDir({"main.beancount": OPENS + 'include "more.beancount"\n' + TXNS,
                            "more.beancount": '2020-01-01 open Assets:Bank USD\n  note: "Assets:Bank"\n'
                            '2020-01-04 balance Assets:Cash  -710.50 USD\n'
                            '2020-01-05 custom "budget" Expenses:Rent "monthly" 700 USD\n'})
        self.before = sd.parse_set(self.d.path, "main.beancount")

    def tearDown(self) -> None:
        self.d.close()

    def test_an_update_replaces_the_transaction_at_its_line(self) -> None:
        cases = g1.gen_updates_at(self.before, 3, random.Random(1))
        self.assertEqual(len(cases), 2)  # every transaction, by place: no legacy id needed
        for c in cases:
            (edit,) = c["edits"]
            self.assertEqual(edit["kind"], "replace")
            self.assertEqual((edit["path"], edit["keyword"]), ("main.beancount", "txn"))
            self.assertEqual(c["model"], "valid")
            text = (self.d.path / "main.beancount").read_text().splitlines()[edit["line"] - 1]
            self.assertTrue(text.startswith(edit["date"]), text)

    def test_a_rename_is_one_token_edit_per_line_naming_the_account(self) -> None:
        edits = g1.rename_edits(self.before, "Assets:Cash", "Assets:CashOracle")
        assert edits is not None
        got = sorted((e["path"], e["keyword"], e["lineno"]) for e in edits)
        main = (self.d.path / "main.beancount").read_text().splitlines()
        lines = [i + 1 for i, t in enumerate(main) if "Assets:Cash" in t]
        self.assertEqual(got, [("main.beancount", k, n) for k, n in
                               zip(["open", "txn", "txn"], lines)] + [("more.beancount", "balance", 3)])
        for e in edits:
            self.assertEqual((e["kind"], e["old"], e["new"]), ("token", "Assets:Cash", "Assets:CashOracle"))

    def test_a_rename_the_primitives_cannot_express_is_not_run(self) -> None:
        # Must not pass: a rename that would leave a metadata value or a `custom` value naming the old account is
        # sent as token edits (a partial rename, not the intended change).
        self.assertIsNone(g1.rename_edits(self.before, "Assets:Bank", "Assets:BankOracle"))  # a metadata value
        self.assertIsNone(g1.rename_edits(self.before, "Expenses:Rent", "Expenses:RentOracle"))  # a custom value

    def test_an_import_is_an_insert_into_the_root(self) -> None:
        cases = g1.generate(self.before, 5, 9)
        imports = [c for c in cases if c["mutator"] == "import_append"]
        self.assertTrue(imports)
        for c in imports:
            (edit,) = c["edits"]
            self.assertEqual((edit["kind"], edit["default_file"]), ("insert", "main.beancount"))
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        self.assertEqual({c["model"] for c in cases}, {"valid"})
        self.assertIn(None, [c["edits"] for c in cases if c["mutator"] == "rename_account"])


class GuardsTest(Env):
    """Must not pass: any of these starts the runner (each is exit 2, nothing run)."""

    def refused(self, argv: list[str]) -> str:
        code, _, err = run_main(argv, self.fake_runner())
        self.assertEqual(code, 2, err)
        self.assertEqual(self.ran, [])
        return err

    def test_the_tier_unset(self) -> None:
        del os.environ["FT_LOCAL_TIER_DIR"]
        self.assertIn("FT_LOCAL_TIER_DIR", self.refused(self.argv()))

    def test_a_folder_outside_the_tier(self) -> None:
        self.d.write({"other/main.beancount": OPENS + TXNS})
        self.refused(self.argv(src=self.base / "other"))

    def test_the_tier_itself(self) -> None:
        self.refused(self.argv(src=self.tier, root="books/main.beancount"))

    def test_a_link_in_the_folder(self) -> None:
        self.d.write({"outside/x.beancount": OPENS})
        (self.src / "x.beancount").symlink_to(self.base / "outside/x.beancount")
        self.assertIn("cp -RL", self.refused(self.argv()))

    def test_an_include_leaving_the_folder(self) -> None:
        self.d.write({"tier/books/main.beancount": OPENS + 'include "../other.beancount"\n',
                      "tier/other.beancount": TXNS})
        self.refused(self.argv())

    def test_a_root_file_outside_the_folder(self) -> None:
        self.d.write({"tier/main.beancount": OPENS})
        self.refused(self.argv(root="../main.beancount"))
        self.refused(self.argv(root=str(self.tier / "main.beancount")))

    def test_a_ledger_that_does_not_parse_names_no_text(self) -> None:
        (self.src / "main.beancount").write_bytes(b"\xff\xfe Corner Shop\n")
        err = self.refused(self.argv())
        self.assertNotIn("Corner Shop", err)

    def test_a_failure_while_reading_names_no_text(self) -> None:
        # Must not pass: an exception's message (it could quote the ledger) printed for a real-ledger copy.
        def boom(*_: object) -> None:
            raise ValueError("Corner Shop")

        with unittest.mock.patch.object(g1, "generate", boom):
            err = self.refused(self.argv())
        self.assertNotIn("Corner Shop", err)
        self.assertIn("ValueError", err)


class OutputTest(Env):
    def test_a_runner_answer_carrying_text_is_never_printed(self) -> None:
        # Must not pass: a code, step, reason or outcome outside the fixed vocabulary reaches the output.
        answers = {
            "upd-000": {"id": "upd-000", "outcome": "refused", "code": "Corner Shop", "step": "plan", "reason": "x"},
            "upd-001": {"id": "upd-001", "outcome": "refused", "code": "INTERNAL", "step": "Priya", "reason": None},
            "imp-001": {"id": "imp-001", "outcome": "refused", "code": "INTERNAL", "step": "plan", "reason": "Landlord"},
            "imp-000": {"id": "imp-000", "outcome": "Expenses:Food"},
            "ren-000": {"id": "Assets:Cash", "outcome": "saved"},
        }
        code, out, err = run_main(self.argv(), self.fake_runner(answers))
        for s in SECRETS:
            self.assertNotIn(s, out + err)
        report = json.loads(out[out.index("{"):])
        self.assertEqual(report["unjustified"], report["refused"])  # every malformed answer is an unjustified refusal
        self.assertGreaterEqual(report["refused"], 5)
        self.assertEqual(code, 1)

    def test_a_failed_runner_shows_nothing_of_a_copy(self) -> None:
        # Must not pass: the runner's error output (it could hold ledger text) printed for a real-ledger copy.
        def runner(src: Path, root_file: str, work: Path, cases: list[dict[str, object]]) -> g1.RunnerResult:
            return g1.RunnerResult(1, [], "Traceback: Corner Shop Expenses:Food 10.50")

        code, out, err = run_main(self.argv(), runner)
        self.assertEqual(code, 3)
        for s in SECRETS:
            self.assertNotIn(s, out + err)

    def test_a_source_changed_during_the_run_stops_it(self) -> None:
        # Must not pass: a run whose source folder changed is reported as a result.
        def runner(src: Path, root_file: str, work: Path, cases: list[dict[str, object]]) -> g1.RunnerResult:
            (src / "main.beancount").write_text(OPENS, encoding="utf-8")
            return g1.RunnerResult(0, [], "")

        code, out, err = run_main(self.argv(), runner)
        self.assertEqual(code, 3, err)
        self.assertIn("STOPPED", err)
        self.assertNotIn("{", out)

    def test_a_copy_is_worked_on_inside_the_tier_and_the_work_removed(self) -> None:
        # Must not pass: a real ledger's case copies made outside FT_LOCAL_TIER_DIR (the temp folder), or left there.
        run_main(self.argv(), self.fake_runner())
        ((src, root, work, n),) = self.ran
        self.assertEqual((src, root, work.parent), (self.src, "main.beancount", self.tier))
        self.assertFalse(work.exists())
        self.assertGreater(n, 0)

    def test_the_rate_and_the_exit(self) -> None:
        code, out, _ = run_main(self.argv(), self.fake_runner())
        report = json.loads(out[out.index("{"):])
        self.assertEqual((code, report["unjustified_rate"], report["refused"]), (0, 0.0, 0))
        self.assertEqual(report["cases_run"], sum(report["outcomes"].values()))


@unittest.skipUnless(g1.RUNNER_PYTHON.is_file(), "needs the backend's environment (backend/.venv)")
class EndToEndTest(Env):
    """The real runner: the engine's preview and the confirm's save, one fresh copy per case."""

    files = {"tier/books/main.beancount": OPENS + TXNS + "2020-02-01 balance Assets:Cash  -710.50 USD\n"
             "pushtag #trip\n"}

    def test_a_run_on_a_copy(self) -> None:
        before = {p: p.read_bytes() for p in self.src.rglob("*")}
        code, out, err = run_main(self.argv("--per-mutator", "1"))
        self.assertEqual({p: p.read_bytes() for p in self.src.rglob("*")}, before)  # the source only read
        self.assertEqual(sorted(p.name for p in self.tier.iterdir()), ["books"])  # no work folder left in the tier
        for s in SECRETS + (str(self.base),):
            self.assertNotIn(s, out + err)
        report = json.loads(out[out.index("{"):])
        lines = {ln.split()[0]: ln for ln in out.splitlines() if ln[:4] in ("upd-", "ren-", "imp-")}
        self.assertIn("saved", lines["upd-000"])
        # the import lands after the open pushtag region: refused by ADR-0009, justified
        self.assertIn("refused WRITE_REFUSED_CHECK_FAILED/plan/insert_in_push_region justified", lines["imp-000"])
        self.assertEqual(report["unjustified"], 0, out + err)
        self.assertEqual(code, 0, out + err)

    def test_save_anyway_is_not_a_refusal(self) -> None:
        # the import adds a second -10.50 to Assets:Cash before the balance assertion: a new error, Save anyway
        self.d.write({"tier/books/main.beancount": OPENS + TXNS + "2020-02-01 balance Assets:Cash  -710.50 USD\n"})
        code, out, err = run_main(self.argv("--per-mutator", "1", "--seed", "3"))
        report = json.loads(out[out.index("{"):])
        self.assertEqual(report["outcomes"].get("save_anyway"), 1, out + err)
        self.assertEqual((report["refused"], code), (0, 0), out + err)
        self.assertFalse(any(p.name != "books" for p in self.tier.iterdir()))


if __name__ == "__main__":
    unittest.main()
