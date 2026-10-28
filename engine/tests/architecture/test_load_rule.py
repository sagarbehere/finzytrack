"""The load rule: the port's load stages are reached only from the overlay load (ARCHITECTURE.md §2.4 rule 4; ADR-0010
"The load rule"; INV-02; BL-0002).

A full load is the overlay replica: `ledger/overlay.py` alone may use the port's load stages (`LOAD_STAGES`: sort,
book, transformations, validation, options aggregation). The reconciliation job joins `ALLOWED` when it is built
(BL-0109). Every public name of the port is classed as a load stage or as an ordinary wrapper, so a new wrapper
cannot escape the rule unclassed. The port exposes neither `load_file` nor `load_string`: Beancount's `load_file`
reads and writes a pickle cache beside the ledger (S0049 journal, Decisions).

The scan is static and fails closed: inside `engine/src/`, any reference to a load stage outside `ALLOWED` is a
violation, whether called or not, imported by name, through a module alias or a relative import; a star import of the
port, a `getattr` on it, or a use of the port module as a value (which could carry it elsewhere) is refused too.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ledger_engine.ledger import beancount_port as port

pytestmark = pytest.mark.inv("INV-02")

ENGINE_SRC = Path(__file__).resolve().parents[2] / "src"
PORT = "ledger_engine.ledger.beancount_port"
ALLOWED = frozenset({"ledger_engine/ledger/overlay.py"})


def module_of(rel: str) -> str:
    return rel.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def absolute(rel: str, node: ast.ImportFrom) -> str:
    """The absolute module an `ImportFrom` names, relative imports resolved against the file's package."""
    if node.level == 0:
        return node.module or ""
    package = module_of(rel).split(".")
    if not rel.endswith("__init__.py"):
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def violations(rel: str, source: str, stages: frozenset[str]) -> list[str]:
    """Every reference to a load stage in one engine source file (`rel` is relative to `engine/src/`)."""
    if rel in ALLOWED or module_of(rel) == PORT:
        return []
    tree = ast.parse(source)
    out: list[str] = []
    aliases: set[str] = set()  # names bound to the port module
    dotted_port: set[str] = set()  # `import ledger_engine.ledger.beancount_port` binds the package root
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = absolute(rel, node)
            for a in node.names:
                if mod == PORT:
                    if a.name == "*":
                        out.append(f"{rel}:{node.lineno}: star import of the port")
                    elif a.name in stages:
                        out.append(f"{rel}:{node.lineno}: imports the load stage {a.name}")
                elif f"{mod}.{a.name}" == PORT:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == PORT and a.asname:
                    aliases.add(a.asname)
                elif a.name == PORT:
                    dotted_port.add(PORT)
    attribute_values: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attribute_values.add(id(node.value))
            target = node.value
            is_port = (isinstance(target, ast.Name) and target.id in aliases) or (
                dotted_port and ast.unparse(target) == PORT
            )
            if is_port and node.attr in stages:
                out.append(f"{rel}:{node.lineno}: uses the load stage {node.attr}")
            if is_port:
                attribute_values.add(id(target))
                for sub in ast.walk(target):
                    attribute_values.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Name) and first.id in aliases:
                out.append(f"{rel}:{node.lineno}: getattr on the port")
                attribute_values.add(id(first))
        elif isinstance(node, ast.Name) and node.id in aliases and id(node) not in attribute_values:
            if isinstance(node.ctx, ast.Load):
                out.append(f"{rel}:{node.lineno}: the port module used as a value")
    return sorted(set(out))


def source_files() -> list[tuple[str, str]]:
    return [
        (p.relative_to(ENGINE_SRC).as_posix(), p.read_text(encoding="utf-8")) for p in sorted(ENGINE_SRC.rglob("*.py"))
    ]


def public_callables() -> set[str]:
    return {
        name
        for name, value in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == port.__name__
    }


# ---- the rule on the tree ----------------------------------------------------------------------------------------


def test_no_engine_module_but_the_overlay_reaches_a_load_stage() -> None:
    found = [v for rel, src in source_files() for v in violations(rel, src, port.LOAD_STAGES)]
    assert found == []


def unused_stages(source: str, stages: frozenset[str]) -> set[str]:
    """The load stages a module never names as an attribute (of the port)."""
    used = {n.attr for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Attribute)}
    return set(stages - used)


def test_the_overlay_uses_the_load_stages() -> None:
    # The rule's one allowed home really is where the load stages are used (a renamed overlay would leave the rule
    # guarding nothing).
    source = (ENGINE_SRC / "ledger_engine" / "ledger" / "overlay.py").read_text(encoding="utf-8")
    assert unused_stages(source, port.LOAD_STAGES) == set()


def test_a_home_that_misses_a_load_stage_is_found() -> None:
    assert unused_stages("port.book(e, o)\n", frozenset({"book", "validate"})) == {"validate"}


def classing_errors(public: set[str], stages: frozenset[str], wrappers: frozenset[str]) -> list[str]:
    """Every public function must be in exactly one class, and each class names only public functions."""
    out = [f"{n}: both a load stage and a wrapper" for n in sorted(stages & wrappers)]
    out += [f"{n}: not classed" for n in sorted(public - stages - wrappers)]
    out += [f"{n}: classed but not a public function of the port" for n in sorted((stages | wrappers) - public)]
    return out


def test_every_public_function_of_the_port_is_classed() -> None:
    assert classing_errors(public_callables(), port.LOAD_STAGES, port.WRAPPERS) == []


def test_an_unclassed_or_doubly_classed_function_is_found() -> None:
    stages, wrappers = frozenset({"book"}), frozenset({"parse_string"})
    assert classing_errors({"book", "parse_string"}, stages, wrappers) == []
    assert classing_errors({"book", "parse_string", "load_file"}, stages, wrappers) == ["load_file: not classed"]
    assert classing_errors({"book", "parse_string"}, stages, wrappers | {"book"}) == [
        "book: both a load stage and a wrapper"
    ]
    assert classing_errors({"book"}, stages, wrappers) == [
        "parse_string: classed but not a public function of the port"
    ]


def test_the_port_offers_no_beancount_load_entry_point() -> None:
    assert not {"load_file", "load_string", "load_encrypted_file", "read_encrypted_file"} & set(vars(port))


# ---- planted violations (must not pass) --------------------------------------------------------------------------

STAGES = frozenset({"book", "validate"})
REL = "ledger_engine/mirror/build.py"


@pytest.mark.parametrize(
    "source",
    [
        "from ledger_engine.ledger.beancount_port import book\n",
        "from ledger_engine.ledger.beancount_port import book as b\n",
        "from ledger_engine.ledger.beancount_port import *\n",
        "from ledger_engine.ledger import beancount_port\nbeancount_port.book([], {})\n",
        "from ledger_engine.ledger import beancount_port as p\nf = p.validate\n",
        "import ledger_engine.ledger.beancount_port as p\np.book([], {})\n",
        "import ledger_engine.ledger.beancount_port\nledger_engine.ledger.beancount_port.book([], {})\n",
        "from ..ledger import beancount_port as p\np.book([], {})\n",
        "from ..ledger.beancount_port import validate\n",
        "from ledger_engine.ledger import beancount_port as p\ngetattr(p, 'bo' + 'ok')([], {})\n",
        "from ledger_engine.ledger import beancount_port as p\nq = p\nq.book([], {})\n",
        "from ledger_engine.ledger import beancount_port as p\nrun(p)\n",
    ],
)
def test_a_planted_reference_to_a_load_stage_is_found(source: str) -> None:
    assert violations(REL, source, STAGES) != []


def test_a_relative_import_inside_the_ledger_package_is_resolved() -> None:
    assert violations("ledger_engine/ledger/grammar.py", "from .beancount_port import book\n", STAGES) != []
    assert violations("ledger_engine/ledger/grammar.py", "from . import beancount_port as p\np.book()\n", STAGES) != []


@pytest.mark.parametrize(
    "source",
    [
        "from ledger_engine.ledger.beancount_port import parse_string\n",
        "from ledger_engine.ledger import beancount_port as p\np.parse_string(b'')\n",
        "from ledger_engine.ledger import beancount_port as p\nx: p.Transaction\n",
        "book = 1\nvalidate = book\n",
    ],
)
def test_ordinary_wrappers_and_unrelated_names_pass(source: str) -> None:
    assert violations(REL, source, STAGES) == []


def test_the_allowed_home_and_the_port_itself_are_exempt() -> None:
    src = "from ledger_engine.ledger import beancount_port as p\np.book([], {})\n"
    assert violations("ledger_engine/ledger/overlay.py", src, STAGES) == []
    assert violations("ledger_engine/ledger/beancount_port.py", "def book(): ...\nbook()\n", STAGES) == []
