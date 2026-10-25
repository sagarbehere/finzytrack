"""Harness guards (INV-24) and reporting, without starting the legacy app: every refusal happens before it."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path

from helpers import OPENS, LedgerDir

import classify
import harness
import semantic_diff as sd

TXN = '2020-01-02 * "Shop" "lunch"\n  id: "t-1"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n'


def run_main(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = harness.main(list(argv))
    return code, err.getvalue()


class MainRefusesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.d = LedgerDir({"src/main.beancount": OPENS + TXN, "live/main.beancount": OPENS, "cases.json": "[]"})
        self.base = self.d.path
        self.old = os.environ.get("FT_LIVE_DATA")
        os.environ["FT_LIVE_DATA"] = str(self.base / "live")

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("FT_LIVE_DATA", None)
        else:
            os.environ["FT_LIVE_DATA"] = self.old
        self.d.close()

    def args(self, *extra: str, src: str = "src") -> list[str]:
        return ["--ledger-dir", str(self.base / src), "--root-file", "main.beancount", "--cases", str(self.base / "cases.json"),
                "--work", str(self.base / "work"), "--port", "18999", *extra]

    def test_the_live_data_folder_as_ledger_dir(self) -> None:
        code, err = run_main(*self.args(src="live"))
        self.assertEqual(code, 2, err)
        self.assertFalse((self.base / "work").exists())

    def test_a_report_inside_the_ledger_folder(self) -> None:
        code, err = run_main(*self.args("--report", str(self.base / "src/report.json")))
        self.assertEqual(code, 2, err)
        self.assertFalse((self.base / "src/report.json").exists())

    def test_a_report_inside_the_live_data_folder(self) -> None:
        self.assertEqual(run_main(*self.args("--report", str(self.base / "live/report.json")))[0], 2)

    def test_a_work_folder_inside_the_ledger_folder(self) -> None:
        argv = self.args()
        argv[argv.index("--work") + 1] = str(self.base / "src/work")
        self.assertEqual(run_main(*argv)[0], 2)

    def test_case_ids_that_are_paths(self) -> None:
        for bad in ("../../escaped", "/abs", "a/b", "", "..", "x" * 65):
            with self.subTest(bad=bad):
                (self.base / "cases.json").write_text(json.dumps([{"id": bad, "mutator": "rename_account", "params": {}}]))
                self.assertEqual(run_main(*self.args())[0], 2)
        self.assertFalse((self.base / "escaped").exists())

    def test_an_include_that_leaves_the_ledger_folder(self) -> None:
        self.d.write({"src/main.beancount": 'include "../live/main.beancount"\n' + TXN})
        code, err = run_main(*self.args())
        self.assertEqual(code, 2, err)
        self.assertIn("outside", err)

    def test_an_absolute_include_into_the_source_escapes_the_copy(self) -> None:
        self.d.write({"src/main.beancount": f'include "{self.base / "src/sub.beancount"}"\n' + TXN, "src/sub.beancount": OPENS})
        self.assertEqual(sd.parse_set(self.base / "src", "main.beancount").outside, ())  # inside the source...
        self.d.write({"copy/main.beancount": (self.base / "src/main.beancount").read_text(), "copy/sub.beancount": OPENS})
        with self.assertRaises(harness.Unsafe):  # ...but outside a copy of it
            harness.check_contained(self.base / "copy", "main.beancount")


class SourceUnchangedTest(unittest.TestCase):
    def test_a_changed_source_folder_stops_the_run(self) -> None:
        d = LedgerDir({"main.beancount": OPENS + TXN})
        try:
            run = harness.Run(d.path, "main.beancount", sd.parse_set(d.path, "main.beancount"),
                              sd.load_facts(d.path, "main.beancount"), harness.ledger_files_bytes(d.path), d.path, d.path, 0)
            harness.assert_source_unchanged(run, "c1")  # unchanged: fine
            d.write({"main.beancount": OPENS})
            with self.assertRaises(harness.Unsafe):
                harness.assert_source_unchanged(run, "c1")
            d.write({"main.beancount": OPENS + TXN, "new.txt": "x"})
            with self.assertRaises(harness.Unsafe):
                harness.assert_source_unchanged(run, "c1")
        finally:
            d.close()


class ReportingTest(unittest.TestCase):
    def test_redact_keeps_counts_classes_and_ids_only(self) -> None:
        r = {"id": "upd-000", "mutator": "update_transaction", "verdict": "UNEXPLAINED", "classes": {"I10": 2},
             "divergences": [{"intended": "2020-01-02 * ..."}], "note": "Expenses:Food", "legacy_message": "m",
             "legacy_details": {"x": 1}, "legacy_http": 200, "legacy_code": None, "seconds": {"total": 1.0}}
        out = harness.redact(r)
        self.assertEqual(set(out), {"id", "mutator", "verdict", "classes", "legacy_http", "legacy_code", "seconds"})
        self.assertNotIn("Expenses", json.dumps(out))

    def test_stray_files(self) -> None:
        d = LedgerDir({"src/main.beancount": 'include "y/*.beancount"\n' + OPENS, "src/y/2020.beancount": TXN,
                       "src/notes.txt": "n", "out/main.beancount": 'include "y/*.beancount"\n' + OPENS,
                       "out/y/2020.beancount": TXN, "out/notes.txt": "changed", "out/y/*.beancount": "", "out/new.txt": "x"})
        try:
            src, out = d.path / "src", d.path / "out"
            before = sd.parse_set(src, "main.beancount")
            run = harness.Run(src, "main.beancount", before, sd.load_facts(src, "main.beancount"),
                              harness.ledger_files_bytes(src), src, src, 0)
            got = {(x.cls, x.file) for x in harness.stray_files(run, out, sd.parse_set(out, "main.beancount"))}
        finally:
            d.close()
        self.assertEqual(got, {("I2", "y/*.beancount"), ("UNEXPLAINED", "notes.txt"), ("UNEXPLAINED", "new.txt")})

    def test_an_unexplained_case_fails_the_run_summary(self) -> None:
        s = harness.summarize([{"verdict": "UNEXPLAINED", "classes": {"UNEXPLAINED": 1}, "seconds": {"total": 1.0}},
                               {"verdict": "MATCH", "classes": {}, "seconds": {"total": 3.0}}])
        self.assertEqual((s["verdicts"], s["classes"], s["seconds_per_case"]["max"]),
                         ({"UNEXPLAINED": 1, "MATCH": 1}, {"UNEXPLAINED": 1}, 3.0))
        self.assertIn("UNEXPLAINED", classify.BLOCKING)


if __name__ == "__main__":
    unittest.main()
