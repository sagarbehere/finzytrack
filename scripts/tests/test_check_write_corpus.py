"""Meta-tests for the D1 write corpus check (S0020; review:D1 §6, §8, §9 guardrail 1; ADR-0059).

Each detector has a case that must pass and one that must not (EP-29); expected features come from the spec's
lists, not from running the detectors.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_write_corpus as cw  # noqa: E402

HEAD = 'option "title" "t"\n2020-01-01 open Assets:Cash USD\n2020-01-01 open Expenses:Food USD\n'
TXN = '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  10.50 USD\n  Assets:Cash  -10.50 USD\n'


def found(main: str | bytes, **extra: str | bytes) -> set[str]:
    files = {"main.beancount": main if isinstance(main, bytes) else main.encode()}
    for name, data in extra.items():
        files[name.replace("__", "/").replace("_DOT_", ".")] = data if isinstance(data, bytes) else data.encode()
    return cw.detect(files)


class FixedList(unittest.TestCase):
    def test_features_are_the_spec_list(self) -> None:
        d1_section_6 = {"crlf", "no-final-newline", "tabs", "unicode-payee", "comment-between-postings",
                        "comment-after-postings", "comment-inline-posting", "posting-metadata", "pushtag", "pushmeta",
                        "org-headings", "glob-include", "nested-include", "implicit-prices", "documents-folders",
                        "elided-amount", "empty-cost-reduction", "insert-entry"}
        guardrail_1 = {"glob-include", "nested-include", "implicit-prices", "documents-folders", "pythonpath-plugin",
                       "duplicate-include"}
        d1_section_8 = {"plugin-modifies-entries", "total-price"}
        adr_0059 = {"encrypted-gpg", "encrypted-asc", "asc-plain"}
        self.assertEqual(set(cw.FEATURES), d1_section_6 | guardrail_1 | d1_section_8 | adr_0059)

    def test_every_feature_has_a_detector(self) -> None:
        self.assertEqual(set(cw.DETECTORS), set(cw.FEATURES))

    def test_a_plain_ledger_shows_nothing(self) -> None:
        self.assertEqual(found(HEAD + TXN), set())


class Detectors(unittest.TestCase):
    """Positive and must-not-pass per feature."""

    def test_crlf(self) -> None:
        self.assertIn("crlf", found((HEAD + TXN).replace("\n", "\r\n").encode()))
        self.assertNotIn("crlf", found(HEAD + TXN))
        mixed = (HEAD.replace("\n", "\r\n") + TXN).encode()
        self.assertNotIn("crlf", found(mixed))
        self.assertNotIn("crlf", found((HEAD + TXN).replace("\n", "\r\n").encode() + b"x\ry\r\n"))  # lone CR

    def test_no_final_newline(self) -> None:
        self.assertIn("no-final-newline", found((HEAD + TXN).rstrip("\n")))
        self.assertNotIn("no-final-newline", found(HEAD + TXN))
        self.assertNotIn("no-final-newline", found((HEAD + TXN).replace("\n", "\r\n").encode()))

    def test_tabs(self) -> None:
        self.assertIn("tabs", found(HEAD + '2024-01-05 * "S" "l"\n\tExpenses:Food\t1 USD\n\tAssets:Cash\n'))
        self.assertNotIn("tabs", found(HEAD + '2024-01-05 * "S\tx" "l"\n  Expenses:Food  1 USD\n  Assets:Cash\n'
                                       + "; a\ttab in a comment\n"))

    def test_unicode_payee(self) -> None:
        self.assertIn("unicode-payee", found(HEAD + TXN.replace('"Shop"', '"Café"')))
        self.assertNotIn("unicode-payee", found(HEAD + TXN.replace('"lunch"', '"Café"')))  # narration only
        self.assertNotIn("unicode-payee", found(HEAD + '2024-01-05 * "Café"\n  Expenses:Food  1 USD\n  Assets:Cash\n'))
        self.assertNotIn("unicode-payee", found(HEAD + TXN.replace('"lunch"', '"lunch" ; "Café" "x"')))

    def test_comment_between_postings(self) -> None:
        between = '2024-01-05 * "S" "l"\n  Expenses:Food  1 USD\n  ; between\n  Assets:Cash\n'
        self.assertIn("comment-between-postings", found(HEAD + between))
        before = '2024-01-05 * "S" "l"\n  ; before\n  Expenses:Food  1 USD\n  Assets:Cash\n'
        self.assertNotIn("comment-between-postings", found(HEAD + before))

    def test_comment_after_postings(self) -> None:
        self.assertIn("comment-after-postings", found(HEAD + TXN + "; after the entry\n"))
        self.assertIn("comment-after-postings", found(HEAD + TXN + "  ; indented, after the last posting\n"))
        self.assertNotIn("comment-after-postings", found(HEAD + TXN + "\n; after a blank line\n"))

    def test_comment_inline_posting(self) -> None:
        self.assertIn("comment-inline-posting", found(HEAD + TXN.replace("10.50 USD\n", "10.50 USD ; note\n", 1)))
        self.assertNotIn("comment-inline-posting", found(HEAD + TXN.replace('"lunch"', '"lunch" ; header note')))
        lot = '2024-01-05 * "B" "b"\n  Assets:Fund  1 F {1 USD, "a;b"}\n  Assets:Cash\n'
        self.assertNotIn("comment-inline-posting", found(HEAD + lot))

    def test_posting_metadata(self) -> None:
        self.assertIn("posting-metadata", found(HEAD + TXN.replace("10.50 USD\n", "10.50 USD\n    receipt: \"r\"\n", 1)))
        self.assertNotIn("posting-metadata", found(HEAD + TXN.replace('"lunch"\n', '"lunch"\n  id: "x"\n')))
        self.assertNotIn("posting-metadata",  # same depth as the posting: entry-level layout, not posting metadata
                         found(HEAD + TXN.replace("10.50 USD\n", "10.50 USD\n  receipt: \"r\"\n", 1)))

    def test_pushtag_and_pushmeta(self) -> None:
        self.assertIn("pushtag", found(HEAD + "pushtag #trip\n" + TXN + "poptag #trip\n"))
        self.assertNotIn("pushtag", found(HEAD + "; pushtag #trip\n" + TXN + "; poptag #trip\n"))
        self.assertNotIn("pushtag", found(HEAD + "pushtag #trip\n" + TXN))  # never popped
        self.assertIn("pushmeta", found(HEAD + 'pushmeta loc: "x"\n' + TXN + "popmeta loc:\n"))
        self.assertNotIn("pushmeta", found(HEAD + 'pushmeta loc: "x"\n' + TXN + "popmeta other:\n"))

    def test_org_headings(self) -> None:
        self.assertIn("org-headings", found("* Options\n" + HEAD + "** Jan\n" + TXN))
        self.assertNotIn("org-headings", found(HEAD + TXN))  # the txn flag * is not a heading
        self.assertNotIn("org-headings", found(HEAD + " * not at the line start\n" + TXN))

    def test_glob_and_nested_include(self) -> None:
        kw = {"y__a.beancount": TXN, "y__b.beancount": 'include "c.bean"\n', "y__c_DOT_bean": TXN}
        f = found(HEAD + 'include "y/*.beancount"\n', **kw)
        self.assertTrue({"glob-include", "nested-include"} <= f)
        self.assertNotIn("glob-include", found(HEAD + 'include "y/a.beancount"\n', **{"y__a.beancount": TXN}))
        self.assertNotIn("glob-include", found(HEAD + 'include "z/*.beancount"\n'))  # matches nothing
        self.assertNotIn("glob-include", found(HEAD + '; include "y/*.beancount"\n', **kw))
        flat = found(HEAD + 'include "a.beancount"\ninclude "b.beancount"\n', a_DOT_beancount=TXN, b_DOT_beancount=TXN)
        self.assertNotIn("nested-include", flat)
        self.assertNotIn("nested-include",  # the second-level include names a file that does not exist
                         found(HEAD + 'include "a.beancount"\n', a_DOT_beancount='include "missing.bean"\n'))
        # A file that is not included is not part of the case's ledger: its include does not count.
        self.assertNotIn("nested-include", found(HEAD + TXN, **{"lone.beancount": 'include "x.bean"\n', "x_DOT_bean": TXN}))

    def test_glob_star_stays_in_one_folder(self) -> None:
        self.assertNotIn("glob-include", found(HEAD + 'include "*.bean"\n', **{"y__a.bean": TXN}))
        self.assertIn("glob-include", found(HEAD + 'include "*/*.bean"\n', **{"y__a.bean": TXN}))
        self.assertNotIn("glob-include", found(HEAD + 'include "y/*"\n', **{"y__z__a.bean": TXN}))  # y/* is not y/z/a

    def test_implicit_prices(self) -> None:
        plugin = 'plugin "beancount.plugins.implicit_prices"\n'
        priced = '2024-01-05 * "X" "fx"\n  Assets:Cash  100 EUR @ 1.1 USD\n  Expenses:Food\n'
        self.assertIn("implicit-prices", found(plugin + HEAD + priced))
        self.assertNotIn("implicit-prices", found(plugin + HEAD + TXN))  # nothing priced
        self.assertNotIn("implicit-prices", found('; ' + plugin + HEAD + priced))

    def test_documents_folders(self) -> None:
        main = 'option "documents" "docs"\n' + HEAD.replace("Assets:Cash USD", "Assets:Bank:Checking USD")
        self.assertIn("documents-folders", found(main, **{"docs__Assets__Bank__Checking__2024-02-05.s_DOT_txt": "x"}))
        self.assertNotIn("documents-folders", found(main, **{"docs__Assets__Bank__Checking__statement_DOT_txt": "x"}))
        self.assertNotIn("documents-folders", found(main, **{"docs__Assets__Bank__Savings__2024-02-05.s_DOT_txt": "x"}))
        self.assertNotIn("documents-folders", found(HEAD, **{"docs__Assets__Cash__2024-02-05.s_DOT_txt": "x"}))
        named = main + '2024-02-05 document Assets:Bank:Checking "docs/Assets/Bank/Checking/2024-02-05.s.txt"\n'
        self.assertNotIn("documents-folders",  # named by a directive only: the folder scan is not exercised
                         found(named, **{"docs__Assets__Bank__Checking__2024-02-05.s_DOT_txt": "x"}))

    def test_elided_amount(self) -> None:
        self.assertIn("elided-amount", found(HEAD + TXN.replace("  Assets:Cash  -10.50 USD", "  Assets:Cash")))
        self.assertIn("elided-amount", found(HEAD + TXN.replace("  Assets:Cash  -10.50 USD", "  Assets:Cash ; rest")))
        self.assertNotIn("elided-amount", found(HEAD + TXN))

    def test_empty_cost_reduction(self) -> None:
        sell = '2024-02-06 * "B" "s"\n  Assets:Fund  -1 FUND {} @ 6 USD\n  Assets:Cash\n'
        self.assertIn("empty-cost-reduction", found(HEAD + sell))
        self.assertNotIn("empty-cost-reduction", found(HEAD + sell.replace("{}", "{5 USD}")))
        self.assertNotIn("empty-cost-reduction", found(HEAD + sell.replace("-1 FUND", "1 FUND")))  # augmentation
        self.assertNotIn("empty-cost-reduction", found(HEAD + sell.replace("{} @ 6 USD", "@ 6 USD ; -1 FUND {}")))

    def test_insert_entry(self) -> None:
        self.assertIn("insert-entry", found(HEAD + '2020-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\n'))
        self.assertNotIn("insert-entry", found(HEAD + '2020-01-01 custom "fava-option" "language" "en"\n'))

    def test_pythonpath_plugin(self) -> None:
        main = 'option "insert_pythonpath" "TRUE"\nplugin "tagger"\n' + HEAD
        f = found(main, tagger_DOT_py="__plugins__ = []\n")
        self.assertTrue({"pythonpath-plugin", "plugin-modifies-entries"} <= f)
        self.assertNotIn("pythonpath-plugin", found(main))  # no local module
        self.assertNotIn("pythonpath-plugin", found(main.replace('"TRUE"', '"FALSE"'), tagger_DOT_py="x"))
        self.assertNotIn("pythonpath-plugin",
                         found('plugin "tagger"\n' + HEAD, tagger_DOT_py="x"))  # not on the path

    def test_duplicate_include(self) -> None:
        self.assertIn("duplicate-include", found('include "a.beancount"\ninclude "a.beancount"\n', a_DOT_beancount=HEAD))
        self.assertIn("duplicate-include",  # the same file reached by two spellings
                      found('include "a.beancount"\ninclude "./a.beancount"\n', a_DOT_beancount=HEAD))
        self.assertNotIn("duplicate-include", found('include "a.beancount"\ninclude "b.beancount"\n',
                                                    a_DOT_beancount=HEAD, b_DOT_beancount=TXN))

    def test_total_price(self) -> None:
        self.assertIn("total-price", found(HEAD + '2024-01-05 * "B" "b"\n  Assets:Tok  7 TOK @@ 10 USD\n  Assets:Cash\n'))
        self.assertNotIn("total-price", found(HEAD + '2024-01-05 * "B" "b"\n  Assets:Tok  7 TOK @ 10 USD\n  Assets:Cash\n'))
        self.assertNotIn("total-price", found(HEAD + TXN.replace('"lunch"', '"@@ in a narration"')))

    def test_encrypted(self) -> None:
        inc = 'include "s.beancount.gpg"\n'
        self.assertIn("encrypted-gpg", found(HEAD + inc, **{"s.beancount_DOT_gpg": "x"}))
        self.assertNotIn("encrypted-gpg", found(HEAD, **{"s.beancount_DOT_gpg": "x"}))  # not included
        self.assertNotIn("encrypted-gpg", found(HEAD + inc.replace("gpg", "GPG"), **{"s.beancount_DOT_GPG": "x"}))
        armour = "-----BEGIN PGP MESSAGE-----\n\nx\n-----END PGP MESSAGE-----\n"
        asc = 'include "s.beancount.asc"\n'
        self.assertIn("encrypted-asc", found(HEAD + asc, **{"s.beancount_DOT_asc": armour}))
        self.assertIn("asc-plain", found(HEAD + asc, **{"s.beancount_DOT_asc": TXN}))
        self.assertNotIn("encrypted-asc", found(HEAD + asc, **{"s.beancount_DOT_asc": TXN}))
        self.assertNotIn("asc-plain", found(HEAD + asc, **{"s.beancount_DOT_asc": armour}))

    def test_encrypted_file_text_is_not_read_for_includes(self) -> None:
        f = found(HEAD + 'include "s.beancount.gpg"\n', **{"s.beancount_DOT_gpg": 'include "x.bean"\n', "x_DOT_bean": TXN})
        self.assertNotIn("nested-include", f)


class Rules(unittest.TestCase):
    def setUp(self) -> None:
        self.cases, self.folders, problems = cw.read_corpus(cw.REPO / cw.CORPUS)
        self.assertEqual(problems, [])

    def problems(self) -> list[str]:
        return cw.check(self.cases, self.folders)

    def test_the_real_corpus_passes(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_main_passes_on_the_repo(self) -> None:
        self.assertEqual(cw.main(), 0)

    def test_folder_without_entry(self) -> None:
        self.folders["extra"] = {"main.beancount": (HEAD + TXN).encode()}
        self.assertTrue(any("extra: folder has no entry" in p for p in self.problems()))

    def test_entry_without_folder(self) -> None:
        self.cases["ghost"] = {"features": ["crlf"], "expect": "load", "errors": []}
        self.assertTrue(any("ghost: entry" in p for p in self.problems()))

    def test_declared_but_absent(self) -> None:
        self.cases["crlf"]["features"].append("tabs")  # type: ignore[index]
        self.assertTrue(any("declares 'tabs'" in p for p in self.problems()))

    def test_present_but_undeclared(self) -> None:
        self.cases["comments"]["features"].remove("comment-inline-posting")  # type: ignore[index]
        self.assertTrue(any("shows 'comment-inline-posting'" in p for p in self.problems()))

    def test_unknown_feature(self) -> None:
        self.cases["crlf"]["features"].append("utf16")  # type: ignore[index]
        self.assertTrue(any("unknown feature 'utf16'" in p for p in self.problems()))

    def test_coverage_gap(self) -> None:
        del self.cases["total-price"], self.folders["total-price"]
        self.assertTrue(any("no case covers 'total-price'" in p for p in self.problems()))

    def test_malformed_entries(self) -> None:
        bad: list[object] = [
            {"features": [], "expect": "load", "errors": []},
            {"features": ["crlf"], "expect": "maybe", "errors": []},
            {"features": ["crlf"], "expect": "load"},
            {"features": ["crlf"], "expect": "load", "errors": [{"type": "LoadError"}]},
            {"features": ["crlf"], "expect": "load", "errors": [], "skip": True},
            {"features": ["crlf", "crlf"], "expect": "load", "errors": []},
            "crlf",
        ]
        for entry in bad:
            with self.subTest(entry=entry):
                self.cases["crlf"] = entry
                self.assertTrue(any(p.startswith("crlf:") for p in self.problems()))

    def test_refuse_without_encrypted_file(self) -> None:
        self.cases["crlf"]["expect"] = "refuse"  # type: ignore[index]
        self.assertTrue(any("crlf: expect = \"refuse\"" in p for p in self.problems()))

    def test_refused_case_declares_no_errors(self) -> None:
        self.cases["encrypted-gpg"]["errors"] = [{"type": "LoadError", "message": "x"}]  # type: ignore[index]
        self.assertTrue(any("encrypted-gpg: a refused case" in p for p in self.problems()))

    def test_loaded_case_with_a_file_beancount_might_decrypt(self) -> None:
        for name, data in (("x.GPG", b"x"), ("notes.asc", b"; plain\n" * 200 + b"-----BEGIN PGP MESSAGE-----\n")):
            with self.subTest(name=name):
                self.folders["crlf"][name] = data
                self.assertTrue(any(f"crlf: {name} might be decrypted" in p for p in self.problems()))
                del self.folders["crlf"][name]

    def test_case_without_root_file(self) -> None:
        self.folders["crlf"] = {"other.beancount": b""}
        self.assertTrue(any("crlf: no main.beancount" in p for p in self.problems()))


class Disk(unittest.TestCase):
    def test_symlinks_and_stray_files_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cases.toml").write_text("")
            (root / "stray.beancount").write_text("")
            (root / "a").mkdir()
            (root / "a" / "main.beancount").write_text("")
            os.symlink(root / "a" / "main.beancount", root / "a" / "link.beancount")
            _, folders, problems = cw.read_corpus(root)
            self.assertTrue(any("stray.beancount: stray file" in p for p in problems))
            self.assertTrue(any("a/link.beancount: symlinks are refused" in p for p in problems))
            self.assertNotIn("link.beancount", folders["a"])

    def test_unreadable_cases_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cases.toml").write_text("[broken")
            self.assertTrue(cw.read_corpus(Path(tmp))[2])


class Attributes(unittest.TestCase):
    def test_text_must_be_unset(self) -> None:
        paths = ["c/a", "c/b", "c/d", "c/e"]
        out = "c/a\0text\0unset\0c/b\0text\0set\0c/d\0text\0unspecified\0"
        problems = cw.check_attributes(paths, out)
        self.assertEqual([p.split(":")[0] for p in problems], ["c/b", "c/d", "c/e"])

    def test_auto_is_not_unset(self) -> None:
        self.assertTrue(cw.check_attributes(["c/a"], "c/a\0text\0auto\0"))


if __name__ == "__main__":
    unittest.main()
