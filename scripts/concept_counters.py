#!/usr/bin/env python3
"""Concept counters: each kernel concept has one home per side, and no second implementation (EP-23, EP-03; ADR-0024
Consequences; INV-02's "Finzytrack never reimplements them"; BL-0116, S0104).

A concept's one home carries the marker `# @concept <name>` (a comment, never a docstring), and `CONCEPTS` names
that home per side. The check fails when:
- a home has no marker, more than one, or is gone; a marker names an unknown concept or stands outside the home
  (a second home, on its side or another);
- **a second implementation appears outside the home, marked or not**: the check recognises one by what the code
  does, from these AST shapes, each counted against one concept (the home may hold its own concept's shapes, never
  another's):
  - `account-grammar` (`ledger.grammar.parse_account`, Beancount's parser): a regex (the pattern given to `re` or
    `regex`, or any call's `pattern=`/`regex=` argument; a module constant, an f-string or a `+` of texts is read
    through; `(?i)`, `re.I` and `re.IGNORECASE` make `a-z` count as upper case) that holds a `:` beside an
    upper-case class (`[A-Z`, `\\p{Lu}`, `\\p{L`, `[:upper:]`) or a root name;
    an account name taken apart or put together by hand (`split`, `rsplit`, `partition`, `rpartition`, `count`,
    `index`, `find`, `rfind` given `":"` as any argument or as `sep=`, `":".join`, `re.split(":", …)`; the `":"`
    may be a module constant); a character test `.isupper()` or `.istitle()`.
  - `currency-grammar` (`ledger.grammar.parse_currency`): a regex made only of upper-case classes (no `a-z`),
    quantifiers, groups, alternation, anchors and the marks Beancount allows (`'._-`).
  - `account-roots` (`ledger.grammar.account_roots`, from the `name_*` options): the text of a root option
    (`"name_assets"` and the others).
  - `account-kinds` (`mirror.core.rows.kinds`, the mirror's canonical kind; ADR-0024): a default root name
    (`"Assets"`, …, or `"Assets:"`) compared, matched in a `case`, given to `startswith`/`endswith`/`removeprefix`,
    listed in a tuple, list or set, or a key of a dict literal; a root name in a regex.
  - `tolerance` (`beancount_port`: `check_transaction`, `balance_tolerance`, `quantize_fill`): a number made from a
    scale: `scaleb` with an argument that is not a whole-number literal, `10 ** e` or `pow(10, e)` (with `10`,
    `Decimal(10)` or `Decimal("10")`) unless `e` is a non-negative literal, `Decimal((sign, digits, exponent))`,
    `Decimal(f"1e{e}")`; a number's `.as_tuple().exponent` (or `.as_tuple()[2]`) or `.adjusted()` used directly in
    arithmetic or given to `scaleb`/`pow`; the text of a tolerance option (`"inferred_tolerance_default"`,
    `"inferred_tolerance_multiplier"`, `"infer_tolerance_from_cost"`).
  - `balance-check` (`ledger.grammar.transaction_balance`, `balance_assertion`): an ordering comparison (`<`, `<=`,
    `>`, `>=`) of a value holding `abs(x)` or `x.copy_abs()` (`max(abs(r) …) <= t` too), of a name or field called `tol`, `eps`, `epsilon` or `*toleran*`, or
    of a fraction (a float or `Decimal("0.005")` strictly between -1 and 1 and not 0, written there or bound to a
    module constant), and `-t <= x <= t`; a `.quantize(...)` compared with zero; a function
    that sums posting amounts (`+=`, `sum(...)`, `.add(...)` or `.fsum(...)` of a field named `number`, `amount`,
    `units` or `weight`) and compares a value with zero (`0`, `Decimal(0)`, `Decimal("0")`, `Decimal()`,
    `.is_zero()`).
  - `number-parsing` (`beancount_port.written_numbers`, Beancount's lexer): a regex with digits, a point and digits
    (`\\d+\\.\\d`, `[0-9]+[.][0-9]`), or a class holding digits, `.` and `,`; `.replace(",", …)`; `locale.atof`,
    `atoi`, `delocalize`, `localeconv`.
- a shape outside its home is not a reviewed site (`REVIEWED`: module, function, concept, with the reason), or a
  reviewed site no longer holds its shape (stale; default-deny both ways).

Scope: the product's Python code (`engine/src`, `backend/src`) for shapes and markers; the frontend's `src/` for
markers only (`//`, `/* */` or `<!-- -->` comments in `.ts`, `.vue`, `.js`): its shapes are ESLint bans
(`frontend/eslint.config.js`, `[EP-23]` in `tests/unit/gates.test.ts`). The frontend and the main sidecar have no
home today (the frontend asks the backend, ADR-0024; the sidecar asks the engine, ADR-0054); a planned port (the
frontend tolerance indicator, L3-09) adds its home here and its exemption there.

Other EP-23 concepts have their own checks: "today" (`scripts/check_money_dates.py`, the frontend's lint), the SQL
runner (`backend/tests/architecture/test_one_query_runner.py`). Stated limits (review covers them): a pattern
built at run time from something that is not a constant of the module; a regex method reached through an alias of
a compiled pattern's method; a function passed as a value (`map(abs, xs)`); a comparison folded through a
helper (`within(r, t)` defined elsewhere is caught only where it compares); a scale read through `str(n)`'s text;
a sum of amounts under another field name. A file that does not parse fails closed.
Usage: python3 scripts/concept_counters.py [--root DIR]   (exit 0 = clean, 1 = not)
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SIDES = {"engine": "engine/src", "backend": "backend/src"}
FRONTEND = "frontend"
FRONTEND_TREE = "frontend/src"
FRONTEND_SUFFIXES = (".ts", ".vue", ".js", ".mts", ".tsx")


@dataclass(frozen=True)
class Concept:
    """What the concept is, and its one home per side: a dotted module (engine, backend) or a path under
    `frontend/` (frontend)."""

    what: str
    homes: dict[str, str] = field(default_factory=dict[str, str])


CONCEPTS: dict[str, Concept] = {
    "account-grammar": Concept(
        "whether a text is an account name, and its parts (Beancount's parser)",
        {"engine": "ledger_engine.ledger.grammar"},
    ),
    "currency-grammar": Concept(
        "whether a text is a commodity code (Beancount's parser)", {"engine": "ledger_engine.ledger.grammar"}
    ),
    "account-roots": Concept(
        "the five root names, from the ledger's `name_*` options", {"engine": "ledger_engine.ledger.grammar"}
    ),
    "account-kinds": Concept(
        "an account's canonical kind from its root (the mirror computes it; ADR-0024)",
        {"engine": "ledger_engine.mirror.core.rows"},
    ),
    "tolerance": Concept("the tolerance Beancount infers or allows", {"engine": "ledger_engine.ledger.beancount_port"}),
    "balance-check": Concept(
        "whether a transaction balances or a balance assertion holds", {"engine": "ledger_engine.ledger.grammar"}
    ),
    "number-parsing": Concept(
        "reading a number from ledger text (Beancount's lexer)", {"engine": "ledger_engine.ledger.beancount_port"}
    ),
}


EP23_COVERAGE: dict[str, str] = {
    "amount parse/format": "number-parsing here (the engine reads numbers only through Beancount's lexer); the "
    "sidecar's and the frontend's amount parse and format: not built (BL-0250)",
    '"today"': "scripts/check_money_dates.py and the frontend's lint (INV-07)",
    "account roots/kinds": "account-roots, account-kinds here",
    "SQL runners": "backend/tests/architecture/test_one_query_runner.py (ADR-0016)",
    "balance and tolerance checks": "balance-check, tolerance here",
    "metadata-key filters": "not built (BL-0250)",
    "LLM prompt loaders": "not built (BL-0250)",
    "tree builders": "not built (BL-0250)",
    "draft converters": "not built (BL-0250)",
}
"""Each concept EP-23's rule lists -> what holds it to one home (the meta-tests read the rule's list from
`design/ENGINEERING.md`, so a concept added there needs a line here)."""


class Site(NamedTuple):
    """A reviewed shape outside its home: the module, the function (`<module>` at the top level, `Class.method`),
    and the concept."""

    module: str
    function: str
    concept: str


REVIEWED: dict[Site, str] = {
    Site("ledger_engine.protocol.app_folder", "parse_identity", "account-grammar"): (
        "splits the app folder's `<device>:<inode>` identity text (S0102), not an account name"
    ),
    Site("ledger_engine.ledger.precision", "<module>", "number-parsing"): (
        "`_LONG_RUN` only picks the lines worth lexing; the numbers on them are read by Beancount's own lexer "
        "(`beancount_port.written_numbers`)"
    ),
    Site("ledger_engine.mirror.core.rows", "account_balance_rows", "balance-check"): (
        "sums each account's units for the `account_balances` table and leaves out a zero sum; it judges no "
        "transaction or assertion"
    ),
    Site("app.platform.errors.registry", "<module>", "currency-grammar"): (
        "`CODE` is the shape of our error codes (`UPPER_SNAKE`), not of a commodity"
    ),
}
"""(module, function, concept) -> why the shape there is not a second implementation."""

ROOT_NAMES = frozenset({"Assets", "Liabilities", "Equity", "Income", "Expenses"})
ROOT_OPTIONS = frozenset({"name_assets", "name_liabilities", "name_equity", "name_income", "name_expenses"})
TOLERANCE_OPTIONS = frozenset(
    {"inferred_tolerance_default", "inferred_tolerance_multiplier", "infer_tolerance_from_cost"}
)
REGEX_FUNCS = frozenset({"compile", "match", "fullmatch", "search", "split", "findall", "finditer", "sub", "subn"})
REGEX_MODULES = frozenset({"re", "regex"})
PATTERN_KEYWORDS = frozenset({"pattern", "regex"})
COLON_METHODS = frozenset({"split", "rsplit", "partition", "rpartition", "count", "index", "find", "rfind"})
CHAR_TESTS = frozenset({"isupper", "istitle"})
PREFIX_METHODS = frozenset({"startswith", "endswith", "removeprefix", "removesuffix"})
AMOUNT_FIELDS = frozenset({"number", "amount", "units", "weight"})
SUM_METHODS = frozenset({"add", "fsum"})
LOCALE_PARSERS = frozenset({"atof", "atoi", "delocalize", "localeconv"})
TOLERANCE_NAMES = frozenset({"tol", "eps", "epsilon"})
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
MARKER = re.compile(r"@concept\s+([A-Za-z0-9_-]+)")
FRONTEND_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/|<!--.*?-->", re.DOTALL)

_GROUP_OPENERS = re.compile(r"\(\?(?:[:=!]|<[=!]|P<\w+>|P=\w+\)|[aiLmsux-]+[:)])")
_UPPER_CLASS = re.compile(r"\[[^\]]*A-Z|\\p\{Lu?|\[:upper:\]")
_ROOT_WORD = re.compile(r"Assets|Liabilities|Equity|Income|Expenses")
_CLASS = re.compile(r"\[(\^?)((?:\\.|[^\]\\])*)\]")
_DIGIT = r"(?:\\d|\[[^\]]*(?:0-9|\\d)[^\]]*\])"
_NUMBER = re.compile(_DIGIT + r"(?:[*+?]|\{[\d,]*\})?\??\(?(?:\\\.|\[\\?\.\]|\[[.,]{2}\])" + _DIGIT)
_CURRENCY_REST = re.compile(r"[\^$()|?*+'._\-]|\\[AZbz]|\{\d+(?:,\d*)?\}|\\-|\\\.")


def _strip_groups(pattern: str) -> str:
    return _GROUP_OPENERS.sub("(", pattern)


def regex_concepts(pattern: str, ignore_case: bool = False) -> list[tuple[str, str]]:
    """The concepts a regex pattern implements, each with what it is (`ignore_case`: a lower-case class matches
    upper case too)."""
    ignore_case = ignore_case or bool(re.search(r"\(\?[aLmsux]*i", pattern))
    p = _strip_groups(pattern)
    out: list[tuple[str, str]] = []
    upper, root = bool(_UPPER_CLASS.search(p)), bool(re.search(_ROOT_WORD.pattern, p, re.IGNORECASE if ignore_case else 0))
    upper = upper or (ignore_case and bool(re.search(r"\[[^\]]*a-z", p)))
    if ":" in p and (upper or root):
        out.append(("account-grammar", "a regex on account names"))
    elif root:
        out.append(("account-kinds", "a root name in a regex"))
    classes = list(_CLASS.finditer(p))
    if classes and all("a-z" not in c.group(2) and "A-Z" in c.group(2) and not c.group(1) for c in classes):
        rest = _CURRENCY_REST.sub("", _CLASS.sub("", p))
        if rest == "":
            out.append(("currency-grammar", "a regex on commodity codes"))
    digit_classes = [c.group(2) for c in classes if "0-9" in c.group(2) or "\\d" in c.group(2)]
    if _NUMBER.search(p) or any("." in c and "," in c for c in digit_classes):
        out.append(("number-parsing", "a regex on written numbers"))
    return out


class Finding(NamedTuple):
    line: int
    function: str
    concept: str
    what: str


def module_of(rel: str) -> tuple[str, str] | None:
    """(side, module) of a product Python file, by its path from the repository root."""
    for side, tree in PYTHON_SIDES.items():
        prefix = tree + "/"
        if rel.startswith(prefix) and rel.endswith(".py"):
            parts = rel[len(prefix) : -len(".py")].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return side, ".".join(parts)
    return None


def _is_ten(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value in (10, "10") and not isinstance(node.value, bool):
        return True
    return (
        isinstance(node, ast.Call)
        and _callee(node) == "Decimal"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in (10, "10")
    )


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _whole_literal(node: ast.expr, non_negative: bool = False) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd) and not non_negative:
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _is_scale(node: ast.expr) -> bool:
    """`x.as_tuple().exponent`, `x.as_tuple()[2]`, `x.adjusted()`."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "adjusted":
        return True
    as_tuple = (
        node.value
        if isinstance(node, ast.Attribute) and node.attr == "exponent"
        else node.value
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == 2
        else None
    )
    return (
        isinstance(as_tuple, ast.Call) and isinstance(as_tuple.func, ast.Attribute) and as_tuple.func.attr == "as_tuple"
    )


def _is_zero(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in (0, "0") and not isinstance(node.value, bool) and not isinstance(node.value, str)
    if isinstance(node, ast.Call) and _callee(node) == "Decimal":
        if not node.args:
            return True
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and not isinstance(arg.value, bool) and isinstance(arg.value, int | str):
            try:
                return Decimal(str(arg.value)) == 0
            except InvalidOperation:
                return False
    return False


def _fraction(node: ast.expr) -> bool:
    """A literal strictly between -1 and 1, not 0: a fixed tolerance."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
    value: Decimal | None = None
    if isinstance(node, ast.Constant) and isinstance(node.value, float):
        value = Decimal(repr(node.value))
    elif (
        isinstance(node, ast.Call)
        and _callee(node) == "Decimal"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        try:
            value = Decimal(node.args[0].value)
        except InvalidOperation:
            return False
    return value is not None and value.is_finite() and 0 < abs(value) < 1


def _tolerance_named(node: ast.expr) -> bool:
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
    return name is not None and (name.lower() in TOLERANCE_NAMES or "toleran" in name.lower())


def _has_amount_field(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr in AMOUNT_FIELDS for n in ast.walk(node))


def _root_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value in ROOT_NAMES or (node.value.endswith(":") and node.value[:-1] in ROOT_NAMES))
    )


class _Scanner:
    """Every shape of one module, with the function it stands in."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.found: list[Finding] = []
        self.texts = self._module_texts()
        self.fractions = {
            t.id
            for n in tree.body
            if isinstance(n, ast.Assign | ast.AnnAssign) and n.value is not None and _fraction(n.value)
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            if isinstance(t, ast.Name)
        }
        self.re_modules = {
            a.asname or a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
            if a.name in REGEX_MODULES
        }
        self.re_names = {
            a.asname or a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module in REGEX_MODULES and n.level == 0
            for a in n.names
            if a.name in REGEX_FUNCS
        }
        self.locale_modules = {
            a.asname or a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
            if a.name == "locale"
        }
        self.locale_names = {
            a.asname or a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "locale" and n.level == 0
            for a in n.names
            if a.name in LOCALE_PARSERS
        }

    def _module_texts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for node in self.tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if value is None:
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    text = self.text(value, {})
                    if text is not None:
                        out[t.id] = text
        return out

    def text(self, node: ast.expr, names: Mapping[str, str] | None = None) -> str | None:
        """The text a pattern expression holds: a literal, a module constant, an f-string's literal parts, a `+`
        of texts, the left of a `%`."""
        names = self.texts if names is None else names
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            if isinstance(node.value, bytes):
                return node.value.decode("latin-1")
            return None
        if isinstance(node, ast.Name):
            return names.get(node.id)
        if isinstance(node, ast.JoinedStr):
            return "".join(self.text(v, names) or "" for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return self.text(node.value, names) or ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.text(node.left, names), self.text(node.right, names)
            return None if left is None and right is None else (left or "") + (right or "")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return self.text(node.left, names)
        return None

    def say(self, node: ast.AST, function: str, concept: str, what: str) -> None:
        self.found.append(Finding(getattr(node, "lineno", 0), function, concept, what))

    def run(self) -> list[Finding]:
        self.visit(self.tree, "<module>")
        return self.found

    def visit(self, node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = child.name if function == "<module>" else f"{function}.{child.name}"
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.posting_sum(child, name)
                self.visit(child, name)
            else:
                self.shape(child, function)
                self.visit(child, function)

    def is_regex_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in REGEX_FUNCS:
            return isinstance(func.value, ast.Name) and func.value.id in self.re_modules
        return isinstance(func, ast.Name) and func.id in self.re_names

    def shape(self, node: ast.AST, function: str) -> None:  # noqa: C901 - one dispatch over node kinds
        if isinstance(node, ast.Call):
            self.call(node, function)
        elif isinstance(node, ast.Compare):
            self.compare(node, function)
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow) and _is_ten(node.left) and not _whole_literal(node.right, True):
                self.say(node, function, "tolerance", "a power of ten from a scale")
            elif any(_is_scale(side) for side in (node.left, node.right)):
                self.say(node, function, "tolerance", "arithmetic on a number's scale")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and _is_scale(node.operand):
            self.say(node, function, "tolerance", "arithmetic on a number's scale")
        elif isinstance(node, ast.JoinedStr):
            for left, right in zip(node.values, node.values[1:], strict=False):
                if (
                    isinstance(left, ast.Constant)
                    and isinstance(left.value, str)
                    and re.search(r"\d[eE][-+]?$", left.value)
                    and isinstance(right, ast.FormattedValue)
                ):
                    self.say(node, function, "tolerance", "a number written from a scale")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in ROOT_OPTIONS:
                self.say(node, function, "account-roots", f"the root option {node.value!r} read by hand")
            elif node.value in TOLERANCE_OPTIONS:
                self.say(node, function, "tolerance", f"the tolerance option {node.value!r} read by hand")
        elif isinstance(node, ast.Tuple | ast.List | ast.Set):
            if any(_root_literal(e) for e in node.elts):
                self.say(node, function, "account-kinds", "a list of root names")
        elif isinstance(node, ast.Dict):
            if any(k is not None and _root_literal(k) for k in node.keys):
                self.say(node, function, "account-kinds", "a table keyed by root names")
        elif isinstance(node, ast.MatchValue) and _root_literal(node.value):
            self.say(node, function, "account-kinds", "a root name matched")

    def call(self, node: ast.Call, function: str) -> None:
        func, name = node.func, _callee(node)
        patterns: list[ast.expr] = []
        if self.is_regex_call(node):
            patterns += node.args[:1]
            patterns += [k.value for k in node.keywords if k.arg == "pattern"]
        else:
            patterns += [k.value for k in node.keywords if k.arg in PATTERN_KEYWORDS]
        ignore_case = any(
            isinstance(n, ast.Attribute) and n.attr in ("I", "IGNORECASE")
            for a in [*node.args[1:], *(k.value for k in node.keywords if k.arg == "flags")]
            for n in ast.walk(a)
        )
        for p in patterns:
            text = self.text(p)
            for concept, what in regex_concepts(text or "", ignore_case):
                self.say(node, function, concept, what)
        first = node.args[0] if node.args else None
        if self.is_regex_call(node) and _callee(node) == "split" and node.args and self.text(node.args[0]) == ":":
            self.say(node, function, "account-grammar", "an account name taken apart by hand (re.split)")
        if isinstance(func, ast.Attribute):
            given = [*node.args, *(k.value for k in node.keywords if k.arg == "sep")]
            colon = any(self.text(a) == ":" for a in given)
            if func.attr in COLON_METHODS and colon:
                self.say(node, function, "account-grammar", f"an account name taken apart by hand (.{func.attr})")
            elif func.attr == "join" and self.text(func.value) == ":":
                self.say(node, function, "account-grammar", "an account name put together by hand")
            elif func.attr in CHAR_TESTS:
                self.say(node, function, "account-grammar", f"a name's characters tested by hand (.{func.attr})")
            elif func.attr in PREFIX_METHODS and any(
                _root_literal(n) for a in node.args for n in (a.elts if isinstance(a, ast.Tuple) else [a])
            ):
                self.say(node, function, "account-kinds", f"a root name compared (.{func.attr})")
            elif func.attr == "replace" and isinstance(first, ast.Constant) and first.value in (",", b","):
                self.say(node, function, "number-parsing", "a number's grouping removed by hand")
            elif func.attr == "scaleb" and first is not None and not _whole_literal(first):
                self.say(node, function, "tolerance", "a number scaled from a scale")
            elif (
                func.attr in LOCALE_PARSERS
                and isinstance(func.value, ast.Name)
                and func.value.id in self.locale_modules
            ):
                self.say(node, function, "number-parsing", f"a number read by locale.{func.attr}")
        elif isinstance(func, ast.Name) and func.id in self.locale_names:
            self.say(node, function, "number-parsing", f"a number read by locale.{func.id}")
        if name == "pow" and len(node.args) >= 2 and _is_ten(node.args[0]) and not _whole_literal(node.args[1], True):
            self.say(node, function, "tolerance", "a power of ten from a scale")
        if name == "Decimal" and node.args and isinstance(node.args[0], ast.Tuple | ast.List):
            self.say(node, function, "tolerance", "a number built from its digits and scale")
        if any(_is_scale(a) for a in node.args) and name in ("scaleb", "pow"):
            self.say(node, function, "tolerance", "arithmetic on a number's scale")

    def compare(self, node: ast.Compare, function: str) -> None:
        sides = [node.left, *node.comparators]
        ordering = any(isinstance(op, ORDERING) for op in node.ops)
        if ordering:
            for s in sides:
                magnitude = any(isinstance(n, ast.Call) and _callee(n) in ("abs", "copy_abs") for n in ast.walk(s))
                if magnitude or _tolerance_named(s):
                    self.say(node, function, "balance-check", "a magnitude compared with a tolerance")
                    break
                if _fraction(s) or (isinstance(s, ast.Name) and s.id in self.fractions):
                    self.say(node, function, "balance-check", "a value compared with a fixed tolerance")
                    break
            else:
                first, last = sides[0], sides[-1]
                if (
                    len(sides) == 3
                    and isinstance(first, ast.UnaryOp)
                    and isinstance(first.op, ast.USub)
                    and ast.dump(first.operand) == ast.dump(last)
                ):
                    self.say(node, function, "balance-check", "a value compared within plus or minus a bound")
        if any(_root_literal(s) for s in sides):
            self.say(node, function, "account-kinds", "a root name compared")
        rounded = any(isinstance(s, ast.Call) and _callee(s) == "quantize" for s in sides)
        if rounded and any(_is_zero(s) for s in sides):
            self.say(node, function, "balance-check", "a value rounded and compared with zero")

    def posting_sum(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> None:
        """A function that sums posting amounts and compares a value with zero: a balance check."""
        sums = zero = None
        for n in _own_nodes(fn):
            if (
                isinstance(n, ast.AugAssign)
                and isinstance(n.op, ast.Add | ast.Sub)
                and _has_amount_field(n.value)
                or isinstance(n, ast.Call)
                and (
                    (isinstance(n.func, ast.Name) and n.func.id == "sum")
                    or (isinstance(n.func, ast.Attribute) and n.func.attr in SUM_METHODS)
                )
                and any(_has_amount_field(a) for a in n.args)
            ):
                sums = sums or n
            if isinstance(n, ast.Compare) and any(_is_zero(s) for s in (n.left, *n.comparators)):
                zero = zero or n
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "is_zero":
                zero = zero or n
        if sums is not None and zero is not None:
            self.say(zero, name, "balance-check", "posting amounts summed and compared with zero")


def _own_nodes(fn: ast.AST) -> Iterator[ast.AST]:
    """The nodes of a function, not those of the functions and classes nested in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(n))


def findings(source: str, rel: str, concepts: Mapping[str, Concept] = CONCEPTS) -> list[Finding] | str:
    """The shapes of one file outside their home (a file that does not parse: the message)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"{rel}:{e.lineno or 0}: does not parse, so it cannot be checked"
    where = module_of(rel)
    out: list[Finding] = []
    seen: set[tuple[int, str, str]] = set()
    for f in sorted(_Scanner(tree).run()):
        concept = concepts.get(f.concept)
        if concept is None or (where is not None and concept.homes.get(where[0]) == where[1]):
            continue
        if (f.line, f.concept, f.what) not in seen:
            seen.add((f.line, f.concept, f.what))
            out.append(f)
    return out


def _message(rel: str, f: Finding, concepts: Mapping[str, Concept]) -> str:
    homes = ", ".join(f"{side} `{home}`" for side, home in sorted(concepts[f.concept].homes.items())) or "none"
    return (
        f"{rel}:{f.line}: EP-23: {f.concept}: {f.what}, a second implementation of "
        f"{concepts[f.concept].what}; its one home: {homes}"
    )


def problems_in(source: str, rel: str, concepts: Mapping[str, Concept] = CONCEPTS) -> list[str]:
    """Every shape of one file outside its home, as `rel:line: message` (no reviewed site applied)."""
    got = findings(source, rel, concepts)
    if isinstance(got, str):
        return [got]
    return [_message(rel, f, concepts) for f in got]


def python_markers(source: str) -> list[tuple[int, str]]:
    """`@concept` markers in comments (never in strings or docstrings)."""
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                out.extend((tok.start[0], m.group(1)) for m in MARKER.finditer(tok.string))
    except (tokenize.TokenError, SyntaxError):
        pass  # the file's parse failure is reported by `findings`
    return out


def frontend_markers(source: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for comment in FRONTEND_COMMENT.finditer(source):
        line = source.count("\n", 0, comment.start()) + 1
        out.extend((line, m.group(1)) for m in MARKER.finditer(comment.group(0)))
    return out


def python_files(root: Path) -> Iterator[Path]:
    for tree in PYTHON_SIDES.values():
        base = root / tree
        if base.is_dir():
            yield from sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def frontend_files(root: Path) -> Iterator[Path]:
    base = root / FRONTEND_TREE
    if base.is_dir():
        yield from sorted(p for p in base.rglob("*") if p.suffix in FRONTEND_SUFFIXES and "node_modules" not in p.parts)


def check(
    root: Path = ROOT, concepts: Mapping[str, Concept] = CONCEPTS, reviewed: Mapping[Site, str] = REVIEWED
) -> list[str]:
    out: list[str] = []
    met: set[Site] = set()
    marks: dict[tuple[str, str], list[tuple[str, int, str]]] = {}  # (concept, side) -> (rel, line, home key)

    def mark(rel: str, line: int, name: str, side: str, key: str) -> None:
        if name not in concepts:
            out.append(f"{rel}:{line}: EP-23: unknown concept `{name}` in an `@concept` marker")
        else:
            marks.setdefault((name, side), []).append((rel, line, key))

    for path in python_files(root):
        rel = path.relative_to(root).as_posix()
        where = module_of(rel)
        if where is None:
            continue
        source = path.read_text(encoding="utf-8")
        got = findings(source, rel, concepts)
        if isinstance(got, str):
            out.append(got)
            continue
        for f in got:
            site = Site(where[1], f.function, f.concept)
            if site in reviewed:
                met.add(site)
            else:
                out.append(_message(rel, f, concepts))
        for line, name in python_markers(source):
            mark(rel, line, name, where[0], where[1])
    for path in frontend_files(root):
        rel = path.relative_to(root).as_posix()
        for line, name in frontend_markers(path.read_text(encoding="utf-8")):
            mark(rel, line, name, FRONTEND, path.relative_to(root / FRONTEND).as_posix())

    for name, concept in concepts.items():
        for side in (*PYTHON_SIDES, FRONTEND):
            home = concept.homes.get(side)
            placed = marks.get((name, side), [])
            for rel, line, key in placed:
                if key != home:
                    out.append(
                        f"{rel}:{line}: EP-23: `@concept {name}` outside its home ({side}: "
                        f"{home or 'none'}): a second home"
                    )
            at_home = [p for p in placed if p[2] == home]
            if home is None:
                continue
            if not _home_exists(root, side, home):
                out.append(f"EP-23: {name}: its home `{home}` ({side}) is gone; update concept_counters.CONCEPTS")
            elif not at_home:
                out.append(f"EP-23: {name}: its home `{home}` ({side}) has no `@concept {name}` marker")
            elif len(at_home) > 1:
                out.append(f"{at_home[1][0]}:{at_home[1][1]}: EP-23: more than one `@concept {name}` marker")
    out.extend(
        f"EP-23: reviewed site {site.module}:{site.function} ({site.concept}) no longer holds its shape: stale; "
        "remove it from concept_counters.REVIEWED"
        for site in sorted(set(reviewed) - met)
    )
    return out


def _home_exists(root: Path, side: str, home: str) -> bool:
    if side == FRONTEND:
        return (root / FRONTEND / home).is_file()
    base = root / PYTHON_SIDES[side] / home.replace(".", "/")
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def main(argv: list[str], concepts: Mapping[str, Concept] = CONCEPTS, reviewed: Mapping[Site, str] = REVIEWED) -> int:
    root = ROOT
    if argv[:1] == ["--root"] and len(argv) == 2:
        root = Path(argv[1]).resolve()
    elif argv:
        print("usage: concept_counters.py [--root DIR]", file=sys.stderr)
        return 2
    found = check(root, concepts, reviewed)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
