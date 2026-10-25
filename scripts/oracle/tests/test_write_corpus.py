"""The D1 write corpus in Beancount 3.2.0 (S0020; review:D1 §6, §8, §9 guardrail 1; ADR-0059).

Every `load` case loads with exactly the errors `cases.toml` declares; a `refuse` case is never loaded, and its
encrypted file is one Beancount itself would decrypt; no `load` case holds such a file. The include tree the stdlib
check computes is the one Beancount reads, and features with a load-level effect are confirmed on Beancount's
output, each probe with its must-not-pass over every case that does not declare the feature (EP-29).
Cases are copied to a temp folder first, so neither bytecode nor Beancount's pickle cache lands in the repo.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from beancount import loader
from beancount.core import data
from beancount.core.number import MISSING
from beancount.parser import parser
from beancount.utils.encryption import is_encrypted_file

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import check_write_corpus as cw  # noqa: E402

Loaded = tuple[list[data.Directive], list[object], dict[str, object], list[data.Directive]]


def at(entry_or_posting: object) -> tuple[str, int]:
    meta = entry_or_posting.meta  # type: ignore[attr-defined]
    return meta["filename"], meta["lineno"]


def count(entries: list[data.Directive], kind: type) -> int:
    return sum(isinstance(e, kind) for e in entries)


def postings_by_line(entries: list[data.Directive]) -> dict[tuple[str, int], data.Posting]:
    return {at(p): p for e in entries if isinstance(e, data.Transaction) for p in e.postings}


def p_implicit_prices(lo: Loaded) -> bool:
    return count(lo[0], data.Price) > count(lo[3], data.Price)


def p_documents_folders(lo: Loaded) -> bool:
    return count(lo[0], data.Document) > count(lo[3], data.Document)


def p_plugin_modifies_entries(lo: Loaded) -> bool:
    parsed = {at(e): e for e in lo[3] if isinstance(e, data.Transaction)}
    fields = ("flag", "payee", "narration", "tags", "links")
    return any(
        at(e) in parsed and any(getattr(e, f) != getattr(parsed[at(e)], f) for f in fields)
        for e in lo[0] if isinstance(e, data.Transaction)
    )


def p_elided_amount(lo: Loaded) -> bool:
    loaded = postings_by_line(lo[0])
    return any(
        p.units is MISSING and loc in loaded and loaded[loc].units is not None and loaded[loc].units.number is not MISSING
        for loc, p in postings_by_line(lo[3]).items()
    )


def p_empty_cost_reduction(lo: Loaded) -> bool:
    loaded = postings_by_line(lo[0])
    return any(
        isinstance(p.cost, data.CostSpec) and p.cost.number_per is MISSING and p.cost.number_total is None
        and p.units is not MISSING and p.units.number < 0
        and loc in loaded and isinstance(loaded[loc].cost, data.Cost) and loaded[loc].cost.number is not None
        for loc, p in postings_by_line(lo[3]).items()
    )


PROBES: dict[str, Callable[[Loaded], bool]] = {
    "implicit-prices": p_implicit_prices,
    "documents-folders": p_documents_folders,
    "plugin-modifies-entries": p_plugin_modifies_entries,
    "elided-amount": p_elided_amount,
    "empty-cost-reduction": p_empty_cost_reduction,
}


def unmatched_errors(declared: list[dict[str, str]], errors: list[object]) -> tuple[list[dict[str, str]], list[str]]:
    """Match declared {type, message fragment} against Beancount's errors one to one; return what is left over."""
    left = list(errors)
    missing = []
    for want in declared:
        hit = next((e for e in left if type(e).__name__ == want["type"] and want["message"] in e.message), None)  # type: ignore[attr-defined]
        if hit is None:
            missing.append(want)
        else:
            left.remove(hit)
    return missing, [f"{type(e).__name__}: {e.message}" for e in left]  # type: ignore[attr-defined]


class WriteCorpus(unittest.TestCase):
    loaded: dict[str, Loaded]
    reached: dict[str, set[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = cw.REPO / cw.CORPUS
        cls.cases, cls.folders, problems = cw.read_corpus(cls.root)
        assert not problems, problems
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.loaded, cls.reached = {}, {}
        sys.dont_write_bytecode = True
        for name, entry in sorted(cls.cases.items()):
            case = tmp / name
            shutil.copytree(cls.root / name, case, ignore=shutil.ignore_patterns("__pycache__"))
            cls.reached[name] = {str(case / p) for p in cw.Tree(cls.folders[name]).reached}
            if entry["expect"] != "load" or any(is_encrypted_file(f) for f in case.rglob("*") if f.is_file()):
                continue  # never hand Beancount a file it would decrypt (it would start gpg)
            entries, errors, options = loader.load_file(str(case / cw.ROOT_FILE))
            parsed = [e for f in options["include"] for e in parser.parse_file(f)[0]]
            cls.loaded[name] = (entries, errors, options, parsed)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def load_cases(self) -> list[str]:
        return sorted(n for n, e in self.cases.items() if e["expect"] == "load")

    def test_every_load_case_was_loaded(self) -> None:
        self.assertEqual(sorted(self.loaded), self.load_cases())
        self.assertTrue(self.load_cases())

    def test_refuse_cases_hold_a_file_beancount_decrypts(self) -> None:
        refused = sorted(n for n, e in self.cases.items() if e["expect"] == "refuse")
        self.assertEqual(refused, ["encrypted-asc", "encrypted-gpg"])
        for name in refused:
            with self.subTest(case=name):
                self.assertTrue(any(is_encrypted_file(f) for f in self.reached[name]))

    def test_no_load_case_holds_a_file_beancount_decrypts(self) -> None:
        for name in self.load_cases():
            for f in sorted((self.root / name).rglob("*")):
                if f.is_file():
                    self.assertFalse(is_encrypted_file(f), f"{name}: {f.name}")

    def test_errors_are_exactly_the_declared_ones(self) -> None:
        for name in self.load_cases():
            with self.subTest(case=name):
                missing, extra = unmatched_errors(self.cases[name]["errors"], self.loaded[name][1])
                self.assertEqual((missing, extra), ([], []))

    def test_include_tree_matches_beancount(self) -> None:
        for name in self.load_cases():
            with self.subTest(case=name):
                self.assertEqual(set(self.loaded[name][2]["include"]), self.reached[name])

    def test_probes(self) -> None:
        for feature, probe in PROBES.items():
            for name in self.load_cases():
                declared = feature in self.cases[name]["features"]
                with self.subTest(feature=feature, case=name):
                    self.assertEqual(probe(self.loaded[name]), declared)
            self.assertTrue(any(feature in self.cases[n]["features"] for n in self.load_cases()), feature)


class ErrorMatching(unittest.TestCase):
    """The comparator itself: declared errors are a multiset, matched by type and message fragment (EP-29)."""

    class LoadError:
        def __init__(self, message: str) -> None:
            self.message = message

    def test_exact_match(self) -> None:
        errs = [self.LoadError('Duplicate filename parsed: "/x/a.beancount"')]
        self.assertEqual(unmatched_errors([{"type": "LoadError", "message": "Duplicate filename"}], errs), ([], []))

    def test_an_undeclared_error_is_left_over(self) -> None:
        self.assertEqual(unmatched_errors([], [self.LoadError("boom")])[1], ["LoadError: boom"])

    def test_a_declared_error_that_did_not_happen_is_missing(self) -> None:
        want = {"type": "LoadError", "message": "x"}
        self.assertEqual(unmatched_errors([want], []), ([want], []))

    def test_one_error_does_not_satisfy_two_declarations(self) -> None:
        want = {"type": "LoadError", "message": "x"}
        self.assertEqual(unmatched_errors([want, want], [self.LoadError("x")])[0], [want])

    def test_type_must_match(self) -> None:
        self.assertTrue(unmatched_errors([{"type": "ParserError", "message": "x"}], [self.LoadError("x")])[0])


if __name__ == "__main__":
    unittest.main()
