#!/usr/bin/env python3
"""Money is never a float and there is no UTC "today" in the product's Python code (INV-06, INV-07; ADR-0023; EP-10).

Scope: the main sidecar's and the engine's product code (`backend/src`, `engine/src`); tests and tooling are not
product code. Ruff already refuses the direct forms (`TID251`: `date.today`, `datetime.now`, `utcnow`, the `time`
clock; `DTZ`: naive datetimes; `RUF032`: `Decimal(1.5)`), and pyright strict refuses arithmetic that mixes a
`Decimal` and a float (typeshed's `Decimal` operators take no float; `/` on Decimals is a Decimal). This check adds
what neither sees:

Money (INV-06)
1. `float` used as a value: called (`float(x)`), or passed, assigned or returned as a function (`map(float, xs)`,
   `parse_float=float`). Allowed where it is a type: annotations, type aliases, the classes of `isinstance` and
   `issubclass`, a `case float():` pattern. A line that converts something that is not money says so with a comment
   `# not money: <reason>` (a duration, a log field).
2. A `Decimal` made from a float: `Decimal.from_float` and `Context.create_decimal_from_float` on anything, and a
   `Decimal(...)` or `.create_decimal(...)` call whose argument is not visibly text or a whole number: a string or
   integer literal, an f-string, or `str(...)`. Pyright and ruff accept `Decimal(x)` for `x: float` and
   `Decimal(cents / 100)`; this rule cannot see a type, so any other argument needs the comment `# exact: <reason>`
   on its line (an int, a Decimal).
3. `json.load`, `json.loads` and `JSONDecoder` without a `parse_float=` argument: a JSON number with a fraction
   would come back a float (also through `import json as j`, `from json import loads`, `json.decoder`); and a class
   that subclasses `JSONDecoder` (its own `decode` would need the same argument).
4. `math` beyond `isfinite`, `isinf`, `isnan`, `inf` and `nan`: its functions turn a `Decimal` into a float.

Civil dates (INV-07)
5. `.date()` and `.astimezone()` without an argument, outside the one home of the civil date
   (`app.domain.dates`): the first gives the date in the instant's own zone (UTC for the clock), the second reads the
   machine's zone in passing.
6. A call of `.today(...)`, `.utcnow(...)` or `.utcfromtimestamp(...)` on anything, so an alias of the class
   (`d = date; d.today()`) is refused as well as the direct call. A field named `today` is not a call.
7. The date of an instant taken through its text or its fields, outside the home: `.strftime(...)`, `.timetuple()`,
   `.utctimetuple()`, a slice or `split`/`partition` of an `.isoformat()` result, and a call given an instant's
   `.year`, `.month` and `.day` (`date(n.year, n.month, n.day)`): each is a UTC "today" when the instant is the
   clock's.

Stated limits (review covers them): an alias of a bound method (`f = d.today; f()`), `getattr` with a computed name,
`datetime.now` reached through an alias of the class (`D = datetime; D.now(UTC)`: a clock read the port rule and
review catch, never a UTC "today" by itself), `Decimal` passed as a function (`map(Decimal, xs)`), a JSON
`object_hook` or `object_pairs_hook` that makes floats itself, a slice of `str(instant)`, and the fields taken one
by one into separate calls. A file that does not parse fails closed.
Usage: python3 scripts/check_money_dates.py [DIR ...]   (default: backend/src engine/src; exit 0 = clean, 1 = not)
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("backend/src", "engine/src")
DATES_HOME = "backend/src/app/domain/dates.py"
NOT_MONEY = re.compile(r"#.*\bnot money: \S")
EXACT = re.compile(r"#.*\bexact: \S")
FROM_FLOAT = frozenset({"from_float", "create_decimal_from_float"})
JSON_DECODERS = frozenset({"load", "loads", "JSONDecoder"})
MATH_ALLOWED = frozenset({"isfinite", "isinf", "isnan", "inf", "nan"})
ZONE_CALLS = frozenset({"date", "astimezone"})
TEXT_OF_INSTANT = frozenset({"strftime", "timetuple", "utctimetuple"})
SPLITS = frozenset({"split", "rsplit", "partition", "rpartition"})
FIELDS = frozenset({"year", "month", "day"})
SYSTEM_DAYS = frozenset({"today", "utcnow", "utcfromtimestamp"})
FLOAT_MESSAGE = "INV-06: float used as a value; money stays Decimal (a line that is not money says `# not money: <reason>`)"


def _type_positions(tree: ast.AST) -> set[int]:
    """The ids of every node where `float` is a type, not a value."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, ast.TypeAlias):
            roots.append(node.value)
        elif isinstance(node, ast.MatchClass):
            roots.append(node.cls)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("isinstance", "issubclass")
            and len(node.args) == 2
        ):
            roots.append(node.args[1])
    return {id(n) for root in roots for n in ast.walk(root)}


def _module_aliases(tree: ast.AST, module: str) -> set[str]:
    """Names bound to `module` itself (`import json`, `import json as j`)."""
    return {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for a in node.names
        if a.name == module
    }


def _imported_from(tree: ast.AST, module: str) -> dict[str, str]:
    """`from module import name as alias`: alias -> name."""
    return {
        a.asname or a.name: a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module and node.level == 0
        for a in node.names
    }


def _visibly_exact(arg: ast.expr) -> bool:
    """A `Decimal` argument that is text or a whole number on its face."""
    if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub | ast.UAdd):
        arg = arg.operand
    if isinstance(arg, ast.Constant):
        return isinstance(arg.value, str) or (isinstance(arg.value, int) and not isinstance(arg.value, bool))
    if isinstance(arg, ast.JoinedStr):
        return True
    return isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "str"


def _isoformat_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "isoformat"


def problems_in(source: str, rel: str) -> list[str]:
    """Every violation in one file, as `rel:line: message`."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"{rel}:{e.lineno or 0}: does not parse, so it cannot be checked"]
    lines = source.splitlines()
    out: list[tuple[int, str]] = []

    def say(node: ast.AST, message: str) -> None:
        out.append((getattr(node, "lineno", 0), message))

    def marked(node: ast.AST, mark: re.Pattern[str] = NOT_MONEY) -> bool:
        first, last = getattr(node, "lineno", 0), getattr(node, "end_lineno", None) or getattr(node, "lineno", 0)
        return any(mark.search(lines[i - 1]) for i in range(first, last + 1) if 0 < i <= len(lines))

    types = _type_positions(tree)
    json_mods = _module_aliases(tree, "json")
    json_names = {
        alias
        for module in ("json", "json.decoder")
        for alias, name in _imported_from(tree, module).items()
        if name in JSON_DECODERS
    }
    decimal_mods = _module_aliases(tree, "decimal")
    decimal_names = {alias for alias, name in _imported_from(tree, "decimal").items() if name == "Decimal"}
    math_mods = _module_aliases(tree, "math")
    home = rel == DATES_HOME

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            (isinstance(b, ast.Attribute) and b.attr == "JSONDecoder") or (isinstance(b, ast.Name) and b.id in json_names)
            for b in node.bases
        ):
            say(node, "INV-06: a JSONDecoder subclass; decode with json.loads(..., parse_float=Decimal)")
        if isinstance(node, ast.Subscript) and _isoformat_call(node.value) and not home:
            say(node, "INV-07: a date cut from an instant's text; a civil date comes from app.domain.dates.civil_date")
        if isinstance(node, ast.ImportFrom) and node.module == "math" and node.level == 0:
            for a in node.names:
                if a.name not in MATH_ALLOWED:
                    say(node, f"INV-06: math.{a.name} turns a Decimal into a float; money stays Decimal")
        elif isinstance(node, ast.Name) and node.id == "float" and isinstance(node.ctx, ast.Load):
            if id(node) not in types and not marked(node):
                say(node, FLOAT_MESSAGE)
        elif isinstance(node, ast.Attribute):
            if node.attr in FROM_FLOAT:
                say(node, f"INV-06: {node.attr} makes a Decimal from a float; parse the written text instead")
            elif isinstance(node.value, ast.Name) and node.value.id in math_mods and node.attr not in MATH_ALLOWED:
                say(node, f"INV-06: math.{node.attr} turns a Decimal into a float; money stays Decimal")
        if isinstance(node, ast.Call):
            func = node.func
            json_call = (
                isinstance(func, ast.Attribute)
                and func.attr in JSON_DECODERS
                and (
                    (isinstance(func.value, ast.Name) and func.value.id in json_mods)
                    or (
                        isinstance(func.value, ast.Attribute)
                        and func.value.attr == "decoder"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id in json_mods
                    )
                )
            ) or (isinstance(func, ast.Name) and func.id in json_names)
            if json_call and not any(k.arg == "parse_float" for k in node.keywords):
                say(node, "INV-06: a JSON decode without parse_float= reads a number with a fraction as a float; "
                          "pass parse_float=Decimal")
            decimal_call = (
                (isinstance(func, ast.Name) and func.id in decimal_names)
                or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "Decimal"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in decimal_mods
                )
                or (isinstance(func, ast.Attribute) and func.attr == "create_decimal")
            )
            given = [*node.args, *(k.value for k in node.keywords if k.arg == "value")]
            if decimal_call and any(not _visibly_exact(a) for a in given) and not marked(node, EXACT):
                say(node, "INV-06: a Decimal from a value that may be a float; give it the written text, or say "
                          "`# exact: <reason>` when the value is an int or a Decimal")
            if not home and {a.attr for a in node.args if isinstance(a, ast.Attribute)} >= FIELDS:
                say(node, "INV-07: a date built from an instant's fields; a civil date comes from "
                          "app.domain.dates.civil_date")
            if isinstance(func, ast.Attribute):
                if not home and (
                    func.attr in TEXT_OF_INSTANT or (func.attr in SPLITS and _isoformat_call(func.value))
                ):
                    say(node, f"INV-07: .{func.attr}() takes a date from an instant's text; a civil date comes from "
                              "app.domain.dates.civil_date")
                if func.attr in ZONE_CALLS and not node.args and not node.keywords and not home:
                    say(node, f"INV-07: .{func.attr}() without a zone; a civil date comes from "
                              "app.domain.dates.civil_date (there is no UTC 'today')")
                elif func.attr in SYSTEM_DAYS:
                    say(node, f"INV-07: .{func.attr}() reads the system's day; a civil date comes from "
                              "app.domain.dates.civil_date and the injected clock")
    return [f"{rel}:{line}: {message}" for line, message in sorted(out)]


def files(trees: list[Path]) -> Iterator[Path]:
    for tree in trees:
        yield from sorted(p for p in tree.rglob("*.py") if "__pycache__" not in p.parts)


def check(trees: list[Path], root: Path = ROOT) -> list[str]:
    out: list[str] = []
    for path in files(trees):
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        out.extend(problems_in(path.read_text(encoding="utf-8"), rel))
    return out


def main(argv: list[str]) -> int:
    trees = [Path(a).resolve() for a in argv] or [ROOT / t for t in TREES]
    missing = [str(t) for t in trees if not t.is_dir()]
    if missing:
        print(f"check_money_dates: no such folder: {', '.join(missing)}", file=sys.stderr)
        return 1
    found = check(trees)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
