"""Meta-tests for scripts/concept_counters.py (EP-23, EP-03; ADR-0024 Consequences; INV-02's "never reimplements").

The rule, from the spec: each kernel concept has one implementation per side, at its one home, which carries an
`@concept <name>` marker; Beancount is the oracle for tolerance, balance checks, account and currency grammar
(ADR-0024), so a second implementation anywhere else fails, with or without a marker. The check recognises an
implementation by what the code does. Each concept's planted shapes must not pass outside its home (EP-29); the
allowed forms beside them must; markers, reviewed sites and the real trees are checked last.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import concept_counters as cc  # noqa: E402

ENGINE = "engine/src/ledger_engine/protocol/planted.py"
BACKEND = "backend/src/app/platform/planted.py"
GRAMMAR = "engine/src/ledger_engine/ledger/grammar.py"
PORT = "engine/src/ledger_engine/ledger/beancount_port.py"
ROWS = "engine/src/ledger_engine/mirror/core/rows.py"

PLANTED: dict[str, tuple[str, ...]] = {
    "account-grammar": (
        # a regex on account names, through every way a pattern reaches `re`
        'import re\nA = re.compile(r"(Assets|Liabilities)(:[A-Z][A-Za-z0-9-]*)+")\n',
        'import re\nPATTERN = r"[A-Z][a-z]*(?::[A-Z0-9][\\w-]*)+"\nA = re.compile(PATTERN)\n',
        'import re as r\ndef ok(n):\n    return r.fullmatch(r"[\\p{Lu}][^:]*(:[^:]+)+", n)\n',
        'from re import match\ndef ok(n):\n    return match("[A-Z]+:.*", n)\n',
        'import re\ndef ok(n):\n    return re.search(rb"^[A-Z][^:]*:", n)\n',
        'import re\nROOT = "[A-Z]"\nA = re.compile(f"{ROOT}[a-z]*:[A-Z].*")\n',
        'import re\nA = re.compile("[A-Z]\\\\w*" + ":" + "[A-Z]\\\\w*")\n',
        'import regex\nA = regex.compile(r"\\p{Lu}\\w*(:\\p{Lu}\\w*)+")\n',
        'from pydantic import Field\nx = Field(pattern=r"^[A-Z][A-Za-z]*(:[A-Z]\\w*)+$")\n',
        # the structure of a name taken apart by hand
        'def leaf(n):\n    return n.split(":")[-1]\n',
        'def parent(n):\n    return n.rsplit(":", 1)[0]\n',
        'def root(n):\n    return n.partition(":")[0]\n',
        'def depth(n):\n    return n.count(":")\n',
        'def join(parts):\n    return ":".join(parts)\n',
        "def ok(c):\n    return c[0].isupper()\n",
        'import re\nA = re.compile(r"[a-z][a-z0-9-]*(:[a-z0-9-]+)+", re.IGNORECASE)\n',
        'import re\nA = re.compile(r"(?i)[a-z]\\w*(:\\w+)+")\n',
        'import re\nA = re.compile(r"^(assets|income):", flags=re.I)\n',
        'def leaf(n):\n    return n.split(sep=":")[-1]\n',
        'def leaf(n):\n    return str.split(n, ":")[-1]\n',
        'SEP = ":"\ndef leaf(n):\n    return n.split(SEP)[-1]\n',
        'SEP = ":"\ndef join(parts):\n    return SEP.join(parts)\n',
        'import re\ndef leaf(n):\n    return re.split(":", n)[-1]\n',
    ),
    "currency-grammar": (
        'import re\nC = re.compile(r"[A-Z]{3}")\n',
        'import re\nC = re.compile(r"^[A-Z][A-Z0-9\'._-]{0,22}[A-Z0-9]$")\n',
        'import re\ndef ok(c):\n    return re.fullmatch("[A-Z][A-Z0-9\'._-]*", c)\n',
    ),
    "account-roots": (
        'def roots(options):\n    return options["name_assets"]\n',
        'KEYS = ("name_assets", "name_liabilities")\n',
    ),
    "account-kinds": (
        'def kind(a):\n    return "asset" if a.startswith("Assets:") else "other"\n',
        'def kind(a):\n    return a.split(":", 1)[0] == "Income"\n',
        'HOLDING = ("Assets", "Liabilities")\n',
        'def holds(root):\n    return root in {"Assets", "Liabilities"}\n',
        'def kind(root):\n    match root:\n        case "Expenses":\n            return 1\n',
        'KIND = {"Assets": "asset", "Income": "income"}\n',
        'import re\nR = re.compile(r"^(Assets|Liabilities)")\n',
    ),
    "tolerance": (
        # a tolerance computed from a number's scale
        "from decimal import Decimal\ndef tol(n):\n    return Decimal(1).scaleb(n.as_tuple().exponent) / 2\n",
        "from decimal import Decimal\ndef tol(n):\n    e = n.as_tuple().exponent\n    return Decimal(10) ** e\n",
        "from decimal import Decimal\ndef tol(e):\n    return Decimal(5).scaleb(e - 1)\n",  # mutant scaleb-any
        "def tol(n):\n    return 10 ** -n\n",
        "from decimal import Decimal\nT = Decimal(10) ** -2\n",
        "def tol(n):\n    return pow(10, n.as_tuple().exponent)\n",
        "from decimal import Decimal\ndef tol(e):\n    return Decimal((0, (5,), e - 1))\n",
        'from decimal import Decimal\ndef tol(e):\n    return Decimal(f"1e{e}")\n',
        "def tol(n):\n    return -n.as_tuple().exponent + 1\n",
        "def tol(n, m):\n    return m * n.adjusted()\n",
        # Beancount's tolerance options read outside the port
        'def tol(options):\n    return options["inferred_tolerance_default"]\n',
        'def mult(options):\n    return options.get("inferred_tolerance_multiplier")\n',
        'def cost(options):\n    return options["infer_tolerance_from_cost"]\n',
    ),
    "balance-check": (
        # a magnitude compared with a threshold
        "def ok(residual, t):\n    return abs(residual) <= t\n",
        "def ok(residual, t):\n    return residual.copy_abs() < t\n",
        "def ok(r, t):\n    return -t <= r <= t\n",
        "def ok(r, tolerance):\n    return r > tolerance\n",
        "def ok(r, x):\n    return r <= x.tol\n",
        'from decimal import Decimal\ndef ok(r):\n    return r > Decimal("0.005")\n',
        "def ok(r):\n    return r < 0.01\n",
        "def ok(residual, t):\n    return max(abs(v) for v in residual.values()) <= t\n",
        "def ok(a, b, t):\n    return abs(a) + abs(b) < t\n",
        'from decimal import Decimal\ndef ok(r):\n    return r.quantize(Decimal("0.01")) == 0\n',
        'from decimal import Decimal\nEPS_LIMIT = Decimal("0.005")\ndef ok(r):\n    return r > EPS_LIMIT\n',
        "def ok(r, eps):\n    return r >= eps\n",
        # a sum of posting amounts compared with zero
        "def ok(postings):\n    return sum(p.units.number for p in postings) == 0\n",
        "from decimal import Decimal\ndef ok(ps):\n    s = Decimal(0)\n    for p in ps:\n        s += p.amount\n"
        "    return s != Decimal(0)\n",
        "from collections import defaultdict\ndef ok(ps):\n    sums = defaultdict(int)\n    for p in ps:\n"
        "        sums[p.currency] += p.amount\n    return {c: s for c, s in sums.items() if s != 0}\n",
        "def ok(ps, ctx):\n    total = 0\n    for p in ps:\n        total = ctx.add(total, p.units.number)\n"
        "    return total.is_zero()\n",
    ),
    "number-parsing": (
        'import re\nN = re.compile(r"-?\\d+(?:\\.\\d+)?")\n',
        'import re\nN = re.compile(r"[0-9]+[.][0-9]*")\n',
        'import re\nN = re.compile(rb"[0-9][0-9,.]{3,}")\n',
        'def parse(t):\n    return t.replace(",", "")\n',
        "import locale\ndef parse(t):\n    return locale.atof(t)\n",
        "import locale\ndef parse(t):\n    return locale.delocalize(t)\n",
    ),
}

ALLOWED = (
    # regexes that are not about names or numbers; colons that are not an account's
    'import re\nSHA = re.compile(r"[0-9a-f]{64}")\n',
    'import re\nTAG = re.compile(r"[A-Za-z0-9_]{1,64}-[A-Za-z0-9_]{1,64}")\n',
    'import re\nTAIL = re.compile(r"(?:-[0-9]{1,3})?")\n',
    'import re\nV = re.compile(r"[1-9][0-9]*/sha256:[0-9a-f]{64}")\n',
    'import re\nGEN = re.compile(r"gen-([1-9][0-9]{0,11})-([0-9a-f]{16})\\.db")\n',
    'import re\nFS = re.compile(r"[A-Za-z0-9_.-]{1,15}")\n',
    'import re\nID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")\n',
    'import re\nURL = re.compile(r"https?://[a-z.]+")\n',
    'import json\nx = json.dumps({}, separators=(",", ":"))\n',
    'def bad(text):\n    return ":" in text\n',
    'def f(t):\n    return t.split(",")\n',
    # account names as content, a root name as text that is no comparison
    'OPEN = "2026-01-01 open Assets:Bank:Checking USD"\n',
    'def post(p):\n    return p("Expenses:Groceries", "12.00")\n',
    'NAMES = ("Assets:Bank:Checking", "Expenses:Rent")\n',
    'HELP = "Assets are what you own"\n',
    # fixed scales, digit counts, money arithmetic
    "from decimal import Decimal\nx = Decimal(1234).scaleb(-2)\n",
    "x = 10 ** 6\n",
    "def two_decimals(a):\n    return a.as_tuple().exponent == -2\n",
    "def digits(ns):\n    return sum(int(n.as_tuple().exponent) for n in ns) + len(ns)\n",
    'from decimal import Decimal, ROUND_HALF_EVEN\ndef cents(x):\n    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)\n',
    'from decimal import Decimal\ndef tax(g):\n    return g * Decimal("0.22")\n',
    # comparisons that are not a balance check
    "def f(n, limit):\n    return n > limit\n",
    "def f(xs):\n    return len(xs) == 0\n",
    "def f(p):\n    return abs(p) == p\n",
    "def f(ps):\n    return [p for p in ps if p.units.number is not None]\n",
    "def f(ps):\n    return sum(p.count for p in ps) == 0\n",
    "def f(ps):\n    return sum(p.amount for p in ps)\n",
    "def f(ratio):\n    return ratio > 1.5\n",
)


def found(source: str, rel: str = ENGINE) -> list[str]:
    return cc.problems_in(source, rel)


class Planted(unittest.TestCase):
    def test_each_concepts_shapes_are_refused_outside_its_home(self) -> None:
        for concept, sources in PLANTED.items():
            for rel in (ENGINE, BACKEND):
                for source in sources:
                    with self.subTest(concept=concept, rel=rel, source=source):
                        got = found(source, rel)
                        self.assertTrue(any(f"EP-23: {concept}:" in p for p in got), got)

    def test_every_concept_has_a_planted_shape(self) -> None:
        self.assertEqual(set(PLANTED), set(cc.CONCEPTS))

    def test_allowed_forms_pass(self) -> None:
        for source in ALLOWED:
            for rel in (ENGINE, BACKEND):
                with self.subTest(rel=rel, source=source):
                    self.assertEqual(found(source, rel), [])

    def test_a_marker_does_not_make_a_second_home(self) -> None:
        # Must not pass: a copy of the grammar that carries the home's marker is still a second implementation.
        source = '# @concept account-grammar\nimport re\nA = re.compile(r"[A-Z]\\w*(:[A-Z]\\w*)+")\n'
        got = found(source, ENGINE)
        self.assertTrue(any("EP-23: account-grammar:" in p and "regex" in p for p in got), got)

    def test_a_home_may_hold_its_own_concepts_shapes(self) -> None:
        self.assertEqual(found('def account_root(n):\n    return n.split(":", 1)[0]\n', GRAMMAR), [])
        self.assertEqual(found('def roots(o):\n    return o["name_assets"]\n', GRAMMAR), [])
        self.assertEqual(found('HOLDING = ("Assets", "Liabilities")\n', ROWS), [])
        self.assertEqual(found('def t(o):\n    return o["inferred_tolerance_default"]\n', PORT), [])

    def test_a_home_is_the_home_of_its_concepts_only(self) -> None:
        # Must not pass: the grammar's home holds no tolerance and the port no account grammar.
        self.assertTrue(found("def tol(n):\n    return 10 ** -n\n", GRAMMAR))
        self.assertTrue(found('def leaf(n):\n    return n.split(":")[-1]\n', PORT))
        self.assertTrue(found('def leaf(n):\n    return n.split(":")[-1]\n', ROWS))

    def test_the_home_is_one_module(self) -> None:
        # Must not pass: a module named like a home in another package is not the home.
        source = 'def leaf(n):\n    return n.split(":")[-1]\n'
        self.assertTrue(found(source, "engine/src/ledger_engine/mirror/grammar.py"))
        self.assertTrue(found(source, "backend/src/app/ledger/grammar.py"))

    def test_a_file_that_does_not_parse_fails_closed(self) -> None:
        self.assertTrue(found("def (:\n"))

    def test_each_problem_names_its_file_line_and_home(self) -> None:
        got = found("x = 1\ndef depth(n):\n    return n.count(':')\n")
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].startswith(f"{ENGINE}:3: EP-23: account-grammar: "), got)
        self.assertIn("ledger_engine.ledger.grammar", got[0])


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class Markers(unittest.TestCase):
    CONCEPTS = {
        "account-grammar": cc.Concept("planted", {"engine": "ledger_engine.ledger.grammar"}),
        "tolerance": cc.Concept("planted", {"engine": "ledger_engine.ledger.beancount_port"}),
    }
    HOMES = {
        "engine/src/ledger_engine/ledger/grammar.py": "# @concept account-grammar\ndef parse_account(t):\n    pass\n",
        "engine/src/ledger_engine/ledger/beancount_port.py": "# @concept tolerance\ndef tolerance():\n    pass\n",
        "backend/src/app/__init__.py": "",
        "frontend/src/main.ts": "export {}\n",
    }

    def run_check(self, extra: dict[str, str], reviewed: dict[cc.Site, str] | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {**self.HOMES, **extra})
            return cc.check(root, self.CONCEPTS, reviewed or {})

    def test_marked_homes_pass(self) -> None:
        self.assertEqual(self.run_check({}), [])

    def test_a_home_without_its_marker_fails(self) -> None:
        got = self.run_check({"engine/src/ledger_engine/ledger/grammar.py": "def parse_account(t):\n    pass\n"})
        self.assertTrue(any("no `@concept account-grammar` marker" in p for p in got), got)

    def test_a_home_that_is_gone_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {k: v for k, v in self.HOMES.items() if "grammar" not in k})
            got = cc.check(root, self.CONCEPTS, {})
        self.assertTrue(any("ledger_engine.ledger.grammar" in p and "gone" in p for p in got), got)

    def test_a_second_marker_fails(self) -> None:
        # Must not pass: a second marked home, on the same side or the other.
        for rel in ("engine/src/ledger_engine/mirror/names.py", "backend/src/app/domain/names.py"):
            with self.subTest(rel=rel):
                got = self.run_check({rel: "# @concept account-grammar\nX = 1\n"})
                self.assertTrue(any(p.startswith(rel) and "second home" in p for p in got), got)

    def test_two_markers_in_the_home_fail(self) -> None:
        home = "# @concept account-grammar\n# @concept account-grammar\nX = 1\n"
        got = self.run_check({"engine/src/ledger_engine/ledger/grammar.py": home})
        self.assertTrue(any("more than one" in p for p in got), got)

    def test_an_unknown_concept_fails(self) -> None:
        got = self.run_check({"engine/src/ledger_engine/mirror/names.py": "# @concept account-gramar\nX = 1\n"})
        self.assertTrue(any("unknown concept" in p for p in got), got)

    def test_a_marker_in_a_docstring_is_no_marker(self) -> None:
        home = '"""The home (`# @concept account-grammar`)."""\nX = 1\n'
        got = self.run_check({"engine/src/ledger_engine/ledger/grammar.py": home})
        self.assertTrue(any("no `@concept account-grammar` marker" in p for p in got), got)

    def test_a_frontend_marker_fails_while_the_frontend_has_no_home(self) -> None:
        for text in (
            "// @concept tolerance\nexport {}\n",
            "/* @concept tolerance */\n",
            "<!-- @concept tolerance -->\n",
        ):
            with self.subTest(text=text):
                got = self.run_check({"frontend/src/domain/tolerance.ts": text})
                self.assertTrue(any("frontend/src/domain/tolerance.ts" in p and "second home" in p for p in got), got)

    def test_a_frontend_home_once_declared_passes_with_its_marker(self) -> None:
        concepts = {**self.CONCEPTS, "tolerance": cc.Concept("planted", {
            "engine": "ledger_engine.ledger.beancount_port", "frontend": "src/domain/tolerance.ts"})}  # fmt: skip
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {**self.HOMES, "frontend/src/domain/tolerance.ts": "// @concept tolerance\nexport {}\n"})
            self.assertEqual(cc.check(root, concepts, {}), [])

    def test_a_shape_in_an_unmarked_module_fails_in_the_tree(self) -> None:
        got = self.run_check(
            {"engine/src/ledger_engine/mirror/names.py": 'def leaf(n):\n    return n.split(":")[-1]\n'}
        )
        self.assertTrue(any("names.py:2: EP-23: account-grammar:" in p for p in got), got)

    def test_a_reviewed_site_passes_and_a_stale_one_fails(self) -> None:
        extra = {"engine/src/ledger_engine/mirror/names.py": 'def leaf(n):\n    return n.split(":")[-1]\n'}
        site = cc.Site("ledger_engine.mirror.names", "leaf", "account-grammar")
        self.assertEqual(self.run_check(extra, {site: "planted reason"}), [])
        # Must not pass: a reviewed site whose shape is gone, and one reviewed for another concept.
        got = self.run_check({}, {site: "planted reason"})
        self.assertTrue(any("stale" in p and "leaf" in p for p in got), got)
        other = cc.Site("ledger_engine.mirror.names", "leaf", "tolerance")
        got = self.run_check(extra, {other: "planted reason"})
        self.assertTrue(any("EP-23: account-grammar:" in p for p in got), got)

    def test_a_reviewed_site_covers_its_function_only(self) -> None:
        source = 'def leaf(n):\n    return n.split(":")[-1]\ndef depth(n):\n    return n.count(":")\n'
        site = cc.Site("ledger_engine.mirror.names", "leaf", "account-grammar")
        got = self.run_check({"engine/src/ledger_engine/mirror/names.py": source}, {site: "planted reason"})
        self.assertEqual(len(got), 1, got)
        self.assertIn(":4: EP-23: account-grammar:", got[0])

    def test_the_command_fails_on_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, self.HOMES)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cc.main(["--root", str(root)], self.CONCEPTS, {}), 0)
                _tree(root, {"backend/src/app/names.py": 'X = "a".split(":")\n'})
                self.assertEqual(cc.main(["--root", str(root)], self.CONCEPTS, {}), 1)
            self.assertIn("backend/src/app/names.py:1: EP-23: account-grammar:", out.getvalue())


class RealTrees(unittest.TestCase):
    def test_the_product_code_has_one_home_per_concept(self) -> None:
        # EP-23, INV-02: the check holds on the real engine, backend and frontend trees.
        self.assertEqual(cc.check(cc.ROOT), [])
        self.assertGreater(sum(1 for _ in cc.python_files(cc.ROOT)), 100)  # the trees were walked

    def test_every_engine_home_is_the_one_the_spec_names(self) -> None:
        # BL-0116, ARCHITECTURE §2.4: the engine's homes are `ledger.grammar` and `beancount_port`; the mirror's
        # account kind is computed by its row builders (ADR-0024: "the mirror computes a canonical account kind").
        homes = {name: c.homes for name, c in cc.CONCEPTS.items()}
        self.assertEqual(
            homes,
            {
                "account-grammar": {"engine": "ledger_engine.ledger.grammar"},
                "currency-grammar": {"engine": "ledger_engine.ledger.grammar"},
                "account-roots": {"engine": "ledger_engine.ledger.grammar"},
                "account-kinds": {"engine": "ledger_engine.mirror.core.rows"},
                "tolerance": {"engine": "ledger_engine.ledger.beancount_port"},
                "balance-check": {"engine": "ledger_engine.ledger.grammar"},
                "number-parsing": {"engine": "ledger_engine.ledger.beancount_port"},
            },
        )

    def test_every_concept_ep23_lists_is_held_somewhere(self) -> None:
        # S0094's lesson: the claim is exact. EP-23's rule lists the concepts; each has a line in EP23_COVERAGE.
        text = (cc.ROOT / "design" / "ENGINEERING.md").read_text(encoding="utf-8")
        rule = next(line for line in text.splitlines() if line.startswith("**Rule.** Each kernel concept"))
        listed = rule.split("per side: ", 1)[1].split(". Before", 1)[0].split(", ")
        self.assertEqual(sorted(listed), sorted(cc.EP23_COVERAGE))
        backlog = (cc.ROOT / "design" / "backlog.md").read_text(encoding="utf-8")
        for concept, held in cc.EP23_COVERAGE.items():
            with self.subTest(concept=concept):
                for row in re.findall(r"BL-\d{4}", held):
                    line = next(x for x in backlog.splitlines() if x.startswith(f"| {row} |"))
                    self.assertIn("| open |", line)
                for name in re.findall(r"\b[a-z]+(?:-[a-z]+)+\b(?= here|,)", held):
                    self.assertIn(name, cc.CONCEPTS)


if __name__ == "__main__":
    unittest.main()
