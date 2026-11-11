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
  A name is read as the text it is bound to (a plain assignment in the same function, the class body or the
  module, read flow-insensitively, or a constant imported from a module of ours: `from m import X`, `m.X`,
  relative imports), and an f-string's literal parts, a `+` of texts, the left of `%` and the receiver of
  `.format(...)` are read through. The shapes:
  - `account-grammar` (`ledger.grammar.parse_account`, Beancount's parser): a regex (the pattern given to `re` or
    `regex`, or any call's `pattern=`/`regex=` argument; `(?i)`, `re.I` and `re.IGNORECASE` make `a-z` count as
    upper case) that holds a `:` beside an upper-case class (`[A-Z`, `\\p{Lu}`, `\\p{L`, `[:upper:]`) or a root
    name; an account name taken apart or put together by hand (`split`, `rsplit`, `partition`, `rpartition`,
    `count`, `index`, `find`, `rfind` given `":"` as any argument or as `sep=`, `":".join`, `re.split(":", …)`);
    a parent tested by hand (`startswith`/`endswith` of a text ending in `:`); `.isupper()` or `.istitle()`.
  - `currency-grammar` (`ledger.grammar.parse_currency`): a regex made only of upper-case classes (no `a-z`),
    quantifiers, groups, alternation, anchors and the marks Beancount allows (`'._-`); `c == c.upper()`.
  - `account-roots` (`ledger.grammar.account_roots`, from the `name_*` options): the text of a root option
    (`"name_assets"` and the others), or one built from its kind (`f"name_{k}"`, `"name_" + k`).
  - `account-kinds` (`mirror.core.rows.kinds`, the mirror's canonical kind; ADR-0024): a default root name
    (`"Assets"`, …, or `"Assets:"`, written or through a name) compared, matched in a `case`, given to
    `startswith`/`endswith`/`removeprefix`; a root name listed in a tuple, list or set, a key or a value of a dict
    literal, a member of an `Enum`, or in a regex.
  - `tolerance` (`beancount_port`: `check_transaction`, `balance_tolerance`, `quantize_fill`): a number made from a
    scale: `scaleb` with an argument that is not a whole-number literal, `10 ** e` or `pow(10, e)` (with `10`,
    `Decimal(10)` or `Decimal("10")`) unless `e` is a non-negative literal, `Decimal((sign, digits, exponent))`,
    `Decimal(f"1e{e}")`, `Decimal("1e%d" % e)`, `Decimal("1e{}".format(e))`; a number's `.as_tuple().exponent`
    (or `.as_tuple()[2]`) or `.adjusted()` used directly in arithmetic or given to `scaleb`/`pow`; the text of a
    tolerance option (`"inferred_tolerance_default"`, `"inferred_tolerance_multiplier"`,
    `"infer_tolerance_from_cost"`).
  - `balance-check` (`ledger.grammar.transaction_balance`, `balance_assertion`): an ordering comparison (`<`, `<=`,
    `>`, `>=`) of a value holding `abs(x)` or `x.copy_abs()` (`max(abs(r) …) <= t`, or a local name bound to such a
    value), of a name or field called `tol`, `eps`, `epsilon` or `*toleran*`, or of a fraction (a float or
    `Decimal("0.005")` strictly between -1 and 1 and not 0, written there or bound to a module constant);
    `-t <= x <= t` and `x < t and x > -t` in any order; `math.isclose(…, abs_tol=…)` or `rel_tol=`; a
    `.quantize(...)` compared with zero; a function, a lambda or the module's top level that sums posting amounts
    (`+=`, `sum(...)`, `.add(...)` or `.fsum(...)` of a field named `number`, `amount`, `units` or `weight`) and
    tests a value for zero: compared with `0`, `Decimal(0)`, `Decimal("0")`, `Decimal()` or a module constant
    bound to one, `.is_zero()`, or the sum's truth (the sum, a name it is bound to or accumulated in, or a name
    taken from that total's `.items()`/`.values()`, in an `if`, `while`, `assert`, conditional expression,
    comprehension filter or `not`).
  - `number-parsing` (`beancount_port.written_numbers`, Beancount's lexer): a regex with digits, a point and digits
    (`\\d+\\.\\d`, `[0-9]+[.][0-9]`, `[0-9]*\\.?[0-9]+`), or a class holding digits and `.` with `,` or no letter
    (`[0-9.]`); grouping removed by hand (`.replace(",", …)`, `re.sub(",", …)`, `.translate` or `maketrans` with
    `","`); `locale.atof`, `atoi`, `delocalize`, `localeconv`.
  - `number-format` (`mirror.core.rows.number`, the mirror's plain notation): `format(x, spec)`; an f-string
    holding an amount (`x.amount`, `x.units`, `x.weight`, or the `.number` of one of them, of `x.price` or
    `x.cost`).
- a shape outside its home is not a reviewed site (`REVIEWED`: module, function, concept and the shape's text,
  with the reason; another shape in the same function is not covered), or a reviewed site no longer holds its
  shape (stale; default-deny both ways).

Scope: the product's Python code (`engine/src`, `backend/src`) for shapes and markers; the frontend's `src/` for
markers only (`//`, `/* */` or `<!-- -->` comments in every script and `.vue` suffix): its shapes are ESLint bans
(`frontend/eslint.config.js`, `[EP-23]` in `tests/unit/gates.test.ts`). The frontend and the main sidecar have no
home today (the frontend asks the backend, ADR-0024; the sidecar asks the engine, ADR-0054); a planned port (the
frontend tolerance indicator, L3-09) adds its home here and its exemption there.

Other EP-23 concepts have their own checks: "today" (`scripts/check_money_dates.py`, the frontend's lint), the SQL
runner (`backend/tests/architecture/test_one_query_runner.py`); `EP23_COVERAGE` maps each.

Stated limits (EP-30: detection by shape is never complete; the verifier's `[EP-23]` question covers the rest).
No data flow beyond one plain assignment: a name rebound by a call, a loop or a branch, a value passed through a
parameter, a helper that sums in one function while another compares (`total(ps) == 0`), or a sum held in an
object (`Inventory.add_amount` then `is_empty()`) is not followed. A pattern built at run time from anything but
constants; a regex method reached through an alias of a compiled pattern's method; a function passed as a value
(`map(abs, xs)`); a sum of plain numbers with no posting field (`sum(nums, Decimal(0)) == 0`); a scale read
through `str(n)`'s text; a comparison spelled through `compare()`; a name's characters tested with `isalpha` or
`len`. A file that does not parse fails closed.
Usage: python3 scripts/concept_counters.py [--root DIR]   (exit 0 = clean, 1 = not)
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SIDES = {"engine": "engine/src", "backend": "backend/src"}
FRONTEND = "frontend"
FRONTEND_TREE = "frontend/src"
FRONTEND_SUFFIXES = (".ts", ".mts", ".cts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".vue")


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
    "number-format": Concept(
        "writing a number as text (the mirror's plain notation)", {"engine": "ledger_engine.mirror.core.rows"}
    ),
}


EP23_COVERAGE: dict[str, tuple[tuple[str, ...], str]] = {
    "amount parse/format": (
        ("number-parsing", "number-format"),
        "the main sidecar writes amounts only in the demo's ledger text (a reviewed site; BL-0063 moves it to "
        "`ledger.core`'s text builders); the sidecar's and the frontend's amount parse and display format are not "
        "built (BL-0250)",
    ),
    '"today"': ((), "scripts/check_money_dates.py and the frontend's lint (INV-07)"),
    "account roots/kinds": (("account-roots", "account-kinds"), ""),
    "SQL runners": ((), "backend/tests/architecture/test_one_query_runner.py (ADR-0016)"),
    "balance and tolerance checks": (("balance-check", "tolerance"), ""),
    "metadata-key filters": ((), "not built (BL-0250)"),
    "LLM prompt loaders": ((), "not built (BL-0250)"),
    "tree builders": ((), "not built (BL-0250)"),
    "draft converters": ((), "not built (BL-0250)"),
}
"""Each concept EP-23's rule lists -> (the concepts counted here, what else holds it to one home). The meta-tests
read the rule's list from `design/ENGINEERING.md`, so a concept added there needs a line here."""


class Site(NamedTuple):
    """A reviewed shape outside its home: the module, the function (`<module>` at the top level, `Class.method`),
    the concept and the shape (the finding's text): another shape in the same function is not covered."""

    module: str
    function: str
    concept: str
    what: str


REVIEWED: dict[Site, str] = {
    Site(
        "ledger_engine.protocol.app_folder",
        "parse_identity",
        "account-grammar",
        "an account name taken apart by hand (.partition)",
    ): "splits the app folder's `<device>:<inode>` identity text (S0102), not an account name",
    Site("ledger_engine.ledger.precision", "<module>", "number-parsing", "a regex on written numbers"): (
        "`_LONG_RUN` only picks the lines worth lexing; the numbers on them are read by Beancount's own lexer "
        "(`beancount_port.written_numbers`)"
    ),
    Site(
        "ledger_engine.mirror.core.rows",
        "account_balance_rows",
        "balance-check",
        "posting amounts summed and compared with zero",
    ): (
        "sums each account's units for the `account_balances` table and leaves out a zero sum; it judges no "
        "transaction or assertion"
    ),
    Site("app.platform.errors.registry", "<module>", "currency-grammar", "a regex on commodity codes"): (
        "`CODE` is the shape of our error codes (`UPPER_SNAKE`), not of a commodity"
    ),
    Site("ledger_engine.ledger.precision", "_part", "number-format", "a number written into text by hand (format)"): (
        "the warning shows a number in the mirror's plain notation (`format(x, 'f')`, as `rows.number`); the "
        "ledger package does not import the mirror's builders"
    ),
    Site(
        "ledger_engine.ledger.precision", "filled_in", "number-format", "a number written into text by hand (format)"
    ): "the warning's numbers, as in `_part`",
    Site(
        "app.content.demo.ledger_text",
        "transaction",
        "number-format",
        "an amount written into text by hand (f-string)",
    ): (
        "the demo generator writes its two-decimal amounts as ledger text and names one in its refusal; it moves "
        "to `ledger.core`'s text builders with BL-0063"
    ),
}
"""(module, function, concept, shape) -> why the shape there is not a second implementation."""

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
_NUMBER = re.compile(_DIGIT + r"(?:[*+?]|\{[\d,]*\})?\??\(?(?:\\\.|\[\\?\.\]|\[[.,]{2}\])\??" + _DIGIT)
_CURRENCY_REST = re.compile(r"[\^$()|?*+'._\-]|\\[AZbz]|\{\d+(?:,\d*)?\}|\\-|\\\.")


def _strip_groups(pattern: str) -> str:
    return _GROUP_OPENERS.sub("(", pattern)


def regex_concepts(pattern: str, ignore_case: bool = False) -> list[tuple[str, str]]:
    """The concepts a regex pattern implements, each with what it is (`ignore_case`: a lower-case class matches
    upper case too)."""
    ignore_case = ignore_case or bool(re.search(r"\(\?[aLmsux]*i", pattern))
    p = _strip_groups(pattern)
    out: list[tuple[str, str]] = []
    upper, root = (
        bool(_UPPER_CLASS.search(p)),
        bool(re.search(_ROOT_WORD.pattern, p, re.IGNORECASE if ignore_case else 0)),
    )
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
    point_classes = [
        c for c in digit_classes if "." in c and ("," in c or not re.search(r"[A-Za-z_]", c.replace("\\d", "")))
    ]
    if _NUMBER.search(p) or point_classes:
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
    """Every shape of one module, with the function it stands in. A name is read as the text it is bound to in the
    function, the class or the module, or in a module of ours it is imported from (`index`)."""

    def __init__(
        self, tree: ast.Module, module: str | None = None, index: Mapping[str, Mapping[str, str]] | None = None
    ) -> None:
        self.tree = tree
        self.found: list[Finding] = []
        self.index = index or {}
        self.imported: dict[str, str] = {}
        self.modules: dict[str, str] = {}
        self._imports(module)
        self.scopes: list[dict[str, str]] = []
        self.enter(tree.body)
        self.zeros = {n for n, v in _bound(tree.body) if _is_zero(v)}
        self.fractions = {n for n, v in _bound(tree.body) if _fraction(v)}
        self.magnitudes: list[set[str]] = [set()]
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

    def _imports(self, module: str | None) -> None:
        """Constants and modules of ours bound by this module's imports."""
        package = (module or "").split(".")
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in self.index:
                        self.modules[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join([*package[: len(package) - node.level], *([base] if base else [])])
                for a in node.names:
                    local = a.asname or a.name
                    if f"{base}.{a.name}" in self.index:
                        self.modules[local] = f"{base}.{a.name}"
                    elif a.name in self.index.get(base, {}):
                        self.imported[local] = self.index[base][a.name]

    def enter(self, nodes: Iterable[ast.AST]) -> None:
        """Open a scope holding the names `nodes` bind to text, each read with the names bound before it."""
        scope: dict[str, str] = {}
        self.scopes.append(scope)
        for name, value in _bound(nodes):
            got = self.text(value)
            if got is not None:
                scope[name] = got

    def text(self, node: ast.expr) -> str | None:
        """The text an expression holds: a literal, a name bound to one (the function, the class, the module, an
        import from a module of ours), `module.NAME` of ours, an f-string's literal parts, a `+` of texts, the left of
        a `%`."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            if isinstance(node.value, bytes):
                return node.value.decode("latin-1")
            return None
        if isinstance(node, ast.Name):
            for scope in reversed(self.scopes):
                if node.id in scope:
                    return scope[node.id]
            return self.imported.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in self.modules:
            return self.index.get(self.modules[node.value.id], {}).get(node.attr)
        if isinstance(node, ast.JoinedStr):
            return "".join(self.text(v) or "" for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return self.text(node.value) or ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.text(node.left), self.text(node.right)
            return None if left is None and right is None else (left or "") + (right or "")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return self.text(node.left)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return self.text(node.func.value)
        return None

    def root_ref(self, node: ast.expr) -> bool:
        """A root name, written or through a name bound to one."""
        if _root_literal(node):
            return True
        if isinstance(node, ast.Name | ast.Attribute):
            t = self.text(node)
            return t is not None and _root_text(t)
        return False

    def zero_ref(self, node: ast.expr) -> bool:
        return _is_zero(node) or (isinstance(node, ast.Name) and node.id in self.zeros)

    def say(self, node: ast.AST, function: str, concept: str, what: str) -> None:
        self.found.append(Finding(getattr(node, "lineno", 0), function, concept, what))

    def run(self) -> list[Finding]:
        self.posting_sum(self.tree, "<module>")
        self.visit(self.tree, "<module>")
        return self.found

    def visit(self, node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = child.name if function == "<module>" else f"{function}.{child.name}"
                if isinstance(child, ast.ClassDef):
                    self.enum(child, name)
                    self.enter(child.body)
                    mags: set[str] = set()
                else:
                    self.posting_sum(child, name)
                    own = list(_own_nodes(child))
                    self.enter(own)
                    mags = {n for n, v in _bound(own) if _holds_magnitude(v)}
                self.magnitudes.append(mags)
                self.visit(child, name)
                self.scopes.pop()
                self.magnitudes.pop()
            else:
                if isinstance(child, ast.Lambda):
                    self.posting_sum(child, function)
                self.shape(child, function)
                self.visit(child, function)

    def is_regex_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in REGEX_FUNCS:
            return isinstance(func.value, ast.Name) and func.value.id in self.re_modules
        return isinstance(func, ast.Name) and func.id in self.re_names

    def enum(self, node: ast.ClassDef, function: str) -> None:
        enum_base = any(
            (isinstance(b, ast.Name) and b.id.endswith("Enum"))
            or (isinstance(b, ast.Attribute) and b.attr.endswith("Enum"))
            for b in node.bases
        )
        if enum_base and any(self.root_ref(v) for _, v in _bound(node.body)):
            self.say(node, function, "account-kinds", "an enum of root names")

    def shape(self, node: ast.AST, function: str) -> None:  # noqa: C901 - one dispatch over node kinds
        if isinstance(node, ast.Call):
            self.call(node, function)
        elif isinstance(node, ast.Compare):
            self.compare(node, function)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            self.bounds(node, function)
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow) and _is_ten(node.left) and not _whole_literal(node.right, True):
                self.say(node, function, "tolerance", "a power of ten from a scale")
            elif any(_is_scale(side) for side in (node.left, node.right)):
                self.say(node, function, "tolerance", "arithmetic on a number's scale")
            elif isinstance(node.op, ast.Add) and _text_is(node.left, ROOT_OPTION_PREFIX):
                self.say(node, function, "account-roots", "a root option built from its kind")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and _is_scale(node.operand):
            self.say(node, function, "tolerance", "arithmetic on a number's scale")
        elif isinstance(node, ast.JoinedStr):
            self.fstring(node, function)
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
            elif any(_root_literal(v) for v in node.values):
                self.say(node, function, "account-kinds", "a table of root names")
        elif isinstance(node, ast.MatchValue) and _root_literal(node.value):
            self.say(node, function, "account-kinds", "a root name matched")

    def fstring(self, node: ast.JoinedStr, function: str) -> None:
        for left, right in zip(node.values, node.values[1:], strict=False):
            if not (
                isinstance(left, ast.Constant) and isinstance(left.value, str) and isinstance(right, ast.FormattedValue)
            ):
                continue
            if re.search(r"\d[eE][-+]?$", left.value):
                self.say(node, function, "tolerance", "a number written from a scale")
            if left.value.endswith(ROOT_OPTION_PREFIX):
                self.say(node, function, "account-roots", "a root option built from its kind")
        for v in node.values:
            if isinstance(v, ast.FormattedValue) and any(_amount_attr(n) for n in ast.walk(v.value)):
                self.say(node, function, "number-format", "an amount written into text by hand (f-string)")

    def call(self, node: ast.Call, function: str) -> None:  # noqa: C901 - one dispatch over call shapes
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
        if self.is_regex_call(node) and node.args:
            pattern = self.text(node.args[0])
            if name == "split" and pattern == ":":
                self.say(node, function, "account-grammar", "an account name taken apart by hand (re.split)")
            if name in ("sub", "subn") and pattern is not None and re.sub(r"[\[\]+]", "", pattern) == ",":
                self.say(node, function, "number-parsing", "a number's grouping removed by hand (re.sub)")
        if name == "isclose" and any(k.arg in ("abs_tol", "rel_tol") for k in node.keywords):
            self.say(node, function, "balance-check", "values compared within a tolerance (isclose)")
        if name == "format" and isinstance(func, ast.Name) and len(node.args) == 2:
            self.say(node, function, "number-format", "a number written into text by hand (format)")
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
                self.root_ref(n) for a in node.args for n in (a.elts if isinstance(a, ast.Tuple) else [a])
            ):
                self.say(node, function, "account-kinds", f"a root name compared (.{func.attr})")
            elif func.attr in PREFIX_METHODS and any(
                (t := self.text(a)) is not None and t.endswith(":") for a in node.args
            ):
                self.say(node, function, "account-grammar", f"a parent account tested by hand (.{func.attr})")
            elif func.attr == "replace" and first is not None and self.text(first) == ",":
                self.say(node, function, "number-parsing", "a number's grouping removed by hand")
            elif func.attr in ("translate", "maketrans") and any(
                isinstance(n, ast.Constant) and n.value == "," for a in node.args for n in ast.walk(a)
            ):
                self.say(node, function, "number-parsing", "a number's grouping removed by hand (translate)")
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
        if name == "Decimal" and node.args and re.search(r"\de(?:%|\{)", self.text(node.args[0]) or ""):
            self.say(node, function, "tolerance", "a number written from a scale")
        if name == "Decimal" and node.args and isinstance(node.args[0], ast.Tuple | ast.List):
            self.say(node, function, "tolerance", "a number built from its digits and scale")
        if any(_is_scale(a) for a in node.args) and name in ("scaleb", "pow"):
            self.say(node, function, "tolerance", "arithmetic on a number's scale")

    def magnitude(self, node: ast.expr) -> bool:
        return _holds_magnitude(node) or (isinstance(node, ast.Name) and node.id in self.magnitudes[-1])

    def compare(self, node: ast.Compare, function: str) -> None:
        sides = [node.left, *node.comparators]
        ordering = any(isinstance(op, ORDERING) for op in node.ops)
        if ordering:
            for s in sides:
                if self.magnitude(s) or _tolerance_named(s):
                    self.say(node, function, "balance-check", "a magnitude compared with a tolerance")
                    break
                if _fraction(s) or (isinstance(s, ast.Name) and s.id in self.fractions):
                    self.say(node, function, "balance-check", "a value compared with a fixed tolerance")
                    break
            else:
                first, last = sides[0], sides[-1]
                if len(sides) == 3 and _negation_of(first, last):
                    self.say(node, function, "balance-check", "a value compared within plus or minus a bound")
        if any(self.root_ref(s) for s in sides):
            self.say(node, function, "account-kinds", "a root name compared")
        for x in sides:
            for y in sides:
                if (
                    isinstance(y, ast.Call)
                    and isinstance(y.func, ast.Attribute)
                    and y.func.attr == "upper"
                    and ast.dump(y.func.value) == ast.dump(x)
                ):
                    self.say(node, function, "currency-grammar", "a code's case tested by hand")
        rounded = any(isinstance(s, ast.Call) and _callee(s) == "quantize" for s in sides)
        if rounded and any(self.zero_ref(s) for s in sides):
            self.say(node, function, "balance-check", "a value rounded and compared with zero")

    def bounds(self, node: ast.BoolOp, function: str) -> None:
        """`r < t and r > -t` (in any order): a value within plus or minus a bound."""
        sides = [
            side
            for v in node.values
            if isinstance(v, ast.Compare) and len(v.ops) == 1 and isinstance(v.ops[0], ORDERING)
            for side in (v.left, v.comparators[0])
        ]
        if any(_negation_of(a, b) for a in sides for b in sides):
            self.say(node, function, "balance-check", "a value compared within plus or minus a bound")

    def posting_sum(self, scope: ast.AST, name: str) -> None:
        """A function, lambda or module that sums posting amounts and tests a value for zero (compared with zero,
        `.is_zero()`, or the sum's truth in an `if`, a `not`, an `assert` or a comprehension's filter): a balance
        check."""
        sums: ast.AST | None = None
        totals: set[str] = set()
        nodes = list(_own_nodes(scope))
        for n in nodes:
            if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add | ast.Sub) and _has_amount_field(n.value):
                sums = sums or n
                target = n.target.value if isinstance(n.target, ast.Subscript) else n.target
                if isinstance(target, ast.Name):
                    totals.add(target.id)
            elif _sum_call(n):
                sums = sums or n
        if sums is None:
            return
        totals |= {t for t, v in _bound(nodes) if _sum_call(v)}
        for n in nodes:
            if isinstance(n, ast.comprehension):
                if _iterates(n.iter, totals):
                    totals |= {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}
        zero: ast.AST | None = None
        for n in nodes:
            if isinstance(n, ast.Compare) and any(self.zero_ref(s) for s in (n.left, *n.comparators)):
                zero = zero or n
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "is_zero":
                zero = zero or n
            tested: list[ast.expr] = []
            if isinstance(n, ast.If | ast.While | ast.IfExp | ast.Assert):
                tested.append(n.test)
            elif isinstance(n, ast.comprehension):
                tested.extend(n.ifs)
            elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
                tested.append(n.operand)
            for t in tested:
                t = t.operand if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not) else t
                if _sum_call(t) or (isinstance(t, ast.Name) and t.id in totals):
                    zero = zero or n
        if zero is not None:
            self.say(zero, name, "balance-check", "posting amounts summed and compared with zero")


ROOT_OPTION_PREFIX = "name_"


def _amount_attr(node: ast.AST) -> bool:
    """`x.amount`, `x.units`, `x.weight`, or the `.number` of one of them (or of `x.price`, `x.cost`)."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr in ("amount", "units", "weight"):
        return True
    return (
        node.attr == "number"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr
        in (
            "amount",
            "units",
            "price",
            "cost",
        )
    )


def _root_text(text: str) -> bool:
    return text in ROOT_NAMES or (text.endswith(":") and text[:-1] in ROOT_NAMES)


def _text_is(node: ast.expr, text: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == text


def _holds_magnitude(node: ast.expr) -> bool:
    return any(isinstance(n, ast.Call) and _callee(n) in ("abs", "copy_abs") for n in ast.walk(node))


def _negation_of(a: ast.expr, b: ast.expr) -> bool:
    return isinstance(a, ast.UnaryOp) and isinstance(a.op, ast.USub) and ast.dump(a.operand) == ast.dump(b)


def _sum_call(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "sum")
            or (isinstance(n.func, ast.Attribute) and n.func.attr in SUM_METHODS)
        )
        and any(_has_amount_field(a) for a in n.args)
    )


def _iterates(it: ast.expr, names: set[str]) -> bool:
    """`x`, `x.items()`, `x.values()` for a name in `names`."""
    if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and it.func.attr in ("items", "values"):
        it = it.func.value
    return isinstance(it, ast.Name) and it.id in names


def _bound(nodes: Iterable[ast.AST]) -> Iterator[tuple[str, ast.expr]]:
    """(name, value) of each plain assignment among `nodes`."""
    for node in nodes:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            yield node.target.id, node.value


def module_texts(tree: ast.Module) -> dict[str, str]:
    """The names a module's own body binds to text (a constant, a name bound before, a `+` of them): what another
    module of ours may import."""
    out: dict[str, str] = {}
    for name, value in _bound(tree.body):
        got = _plain_text(value, out)
        if got is not None:
            out[name] = got
    return out


def _plain_text(node: ast.expr, names: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
        return node.value if isinstance(node.value, str) else node.value.decode("latin-1")
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _plain_text(node.left, names), _plain_text(node.right, names)
        return None if left is None or right is None else left + right
    return None


def _own_nodes(fn: ast.AST) -> Iterator[ast.AST]:
    """The nodes of a scope, not those of the functions, classes and lambdas nested in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(n))


def findings(
    source: str,
    rel: str,
    concepts: Mapping[str, Concept] = CONCEPTS,
    index: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Finding] | str:
    """The shapes of one file outside their home (a file that does not parse: the message). `index`: the text
    constants of each module of ours, for names imported from them."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"{rel}:{e.lineno or 0}: does not parse, so it cannot be checked"
    where = module_of(rel)
    out: list[Finding] = []
    seen: set[tuple[int, str, str]] = set()
    for f in sorted(_Scanner(tree, where[1] if where else None, index).run()):
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


def problems_in(
    source: str,
    rel: str,
    concepts: Mapping[str, Concept] = CONCEPTS,
    index: Mapping[str, Mapping[str, str]] | None = None,
) -> list[str]:
    """Every shape of one file outside its home, as `rel:line: message` (no reviewed site applied)."""
    got = findings(source, rel, concepts, index)
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

    sources: dict[str, str] = {}
    index: dict[str, dict[str, str]] = {}
    for path in python_files(root):
        rel = path.relative_to(root).as_posix()
        where = module_of(rel)
        if where is None:
            continue
        sources[rel] = path.read_text(encoding="utf-8")
        try:
            index[where[1]] = module_texts(ast.parse(sources[rel]))
        except SyntaxError:
            pass  # reported below
    for rel, source in sources.items():
        where = module_of(rel)
        assert where is not None
        got = findings(source, rel, concepts, index)
        if isinstance(got, str):
            out.append(got)
            continue
        for f in got:
            site = Site(where[1], f.function, f.concept, f.what)
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
        f"EP-23: reviewed site {site.module}:{site.function} ({site.concept}: {site.what}) no longer holds its "
        "shape: stale; "
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
