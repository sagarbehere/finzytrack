"""The load rule: the port's load stages are reached only from the overlay load (ARCHITECTURE.md §2.4 rule 4; ADR-0010
"The load rule"; INV-02; BL-0002).

A full load is the overlay replica: `ledger/overlay.py` alone may use the port's load stages (`LOAD_STAGES`: sort,
book, transformations, validation, options aggregation). The reconciliation loads (`RECONCILE_LOADS`: Beancount's own
loader without its pickle cache, BL-0118) are R4's alone: only `ledger/r4.py` may use them (S0066). Every public name
of the port is classed as a load stage, a reconciliation load or an ordinary wrapper, so a new wrapper cannot escape
the rule unclassed. The port exposes neither `load_file` nor `load_string`: Beancount's `load_file`
reads and writes a pickle cache beside the ledger (S0049 journal, Decisions).

The scan is static and fails closed: inside `engine/src/`, any reference to a load stage outside `ALLOWED` is a
violation, whether called or not, imported by name, through a module alias or a relative import; a star import of the
port, a `getattr` on it, or a use of the port module as a value (which could carry it elsewhere) is refused too.
"""

from __future__ import annotations

import ast
import functools
import importlib
import sys
import typing
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import TypeAliasType

import pytest
from beancount.parser import booking as beancount_booking

from ledger_engine.ledger import beancount_port as port

pytestmark = pytest.mark.inv("INV-02")

ENGINE_SRC = Path(__file__).resolve().parents[2] / "src"
PORT = "ledger_engine.ledger.beancount_port"
ALLOWED = frozenset({"ledger_engine/ledger/overlay.py"})
RECONCILE_HOME = frozenset({"ledger_engine/ledger/r4.py"})
ENGINE_PACKAGE = "ledger_engine"
_MISSING = object()


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


def _import(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _in_engine(module: str) -> bool:
    return module == ENGINE_PACKAGE or module.startswith(ENGINE_PACKAGE + ".")


def violations(rel: str, source: str, stages: frozenset[str], allowed: frozenset[str] = ALLOWED) -> list[str]:
    """Every way one engine source file (`rel` is relative to `engine/src/`) can reach a load stage.

    Imports of engine modules are resolved at runtime, so the port is followed through every module that holds it
    (the overlay's `port`, the `ledger` package, a dotted import). Then: a load stage imported, or reached by any
    attribute path, is a violation; so is any name the port does not export (`__all__`: `__dict__`, Beancount's own
    names), the port module used as a value (passed, assigned, `getattr`, `vars`), a star import that brings the
    port or a stage, an engine import that does not resolve, and the port's module name in a string handed to a call
    or a subscript (`importlib.import_module`, `sys.modules`). A name computed at run time is beyond a static scan.
    """
    if rel in allowed or module_of(rel) == PORT:
        return []
    exported = frozenset(port.__all__)
    stage_ids = {id(getattr(port, name)) for name in stages if hasattr(port, name)}
    tree = ast.parse(source)
    out: list[str] = []
    bound: dict[str, object] = {}  # names bound by an engine import, to the object they hold

    def reaches_stage(where: str, what: str, value: object) -> None:
        if id(value) in stage_ids:
            out.append(f"{where}: {what} is a load stage")

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = absolute(rel, node)
            if not _in_engine(mod):
                continue
            module = _import(mod)
            if module is None:
                out.append(f"{rel}:{node.lineno}: cannot resolve the import of {mod}")
                continue
            for a in node.names:
                if a.name == "*":
                    public = getattr(module, "__all__", [n for n in vars(module) if not n.startswith("_")])
                    values = [getattr(module, n, None) for n in public]
                    if module is port or any(v is port or id(v) in stage_ids for v in values):
                        out.append(f"{rel}:{node.lineno}: a star import that brings the port or a load stage")
                    continue
                value = getattr(module, a.name, _MISSING)
                if value is _MISSING:
                    value = _import(f"{mod}.{a.name}")
                if value is None:
                    out.append(f"{rel}:{node.lineno}: cannot resolve {a.name} from {mod}")
                    continue
                if module is port and a.name not in exported:
                    out.append(f"{rel}:{node.lineno}: imports {a.name}, which the port does not export")
                reaches_stage(f"{rel}:{node.lineno}", f"the import of {a.name}", value)
                bound[a.asname or a.name] = value
        elif isinstance(node, ast.Import):
            for a in node.names:
                if not _in_engine(a.name):
                    continue
                target = a.name if a.asname else a.name.split(".")[0]
                if _import(a.name) is None or (module := _import(target)) is None:
                    out.append(f"{rel}:{node.lineno}: cannot resolve the import of {a.name}")
                    continue
                bound[a.asname or target] = module

    inner: set[int] = set()  # nodes that are the `.value` of an attribute: judged as part of the longer path
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
        if isinstance(node, ast.Attribute):
            inner.add(id(node.value))
    for node in ast.walk(tree):
        where = f"{rel}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Name) and node.id in bound and id(node) not in inner:
            if isinstance(node.ctx, ast.Load) and bound[node.id] is port:
                out.append(f"{where}: the port module used as a value")
        elif isinstance(node, ast.Attribute) and id(node) not in inner:
            chain: list[str] = []
            base: ast.expr = node
            while isinstance(base, ast.Attribute):
                chain.insert(0, base.attr)
                base = base.value
            if not isinstance(base, ast.Name) or base.id not in bound:
                continue
            value: object = bound[base.id]
            for attr in chain:
                if value is port and attr not in exported:
                    out.append(f"{where}: uses {attr}, which the port does not export")
                if not isinstance(value, ModuleType):
                    value = _MISSING
                    break
                following = getattr(value, attr, _MISSING)
                if following is _MISSING and _in_engine(value.__name__):
                    following = _import(f"{value.__name__}.{attr}") or _MISSING
                value = following
                reaches_stage(where, f"{ast.unparse(node)}", value)
                if value is _MISSING:
                    break
            if value is port:
                out.append(f"{where}: the port module used as a value")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "beancount_port" in node.value:
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call | ast.Subscript | ast.BinOp):
                out.append(f"{where}: names the port module in a string")
    return sorted(set(out))


def source_files() -> list[tuple[str, str]]:
    return [
        (p.relative_to(ENGINE_SRC).as_posix(), p.read_text(encoding="utf-8")) for p in sorted(ENGINE_SRC.rglob("*.py"))
    ]


def is_type(value: object) -> bool:
    """A class or a type alias: a name for a type, not a step that runs."""
    return isinstance(value, type | TypeAliasType) or typing.get_origin(value) is not None


def exported_callables(namespace: Mapping[str, object], exported: list[str]) -> set[str]:
    """The names another module may use that run something: every exported callable that is not a type."""
    return {n for n in exported if callable(namespace.get(n)) and not is_type(namespace.get(n))}


def leaked_names(namespace: Mapping[str, object], exported: list[str]) -> list[str]:
    """Public names of the port outside `__all__` that come from Beancount, or whose origin cannot be told (a
    constant): each must be imported under a private alias, so nothing of Beancount's is reachable unclassed."""
    out: list[str] = []
    for name, value in namespace.items():
        if name.startswith("_") or name in exported:
            continue
        origin = value.__name__ if isinstance(value, ModuleType) else getattr(value, "__module__", None)
        if not isinstance(origin, str) or origin.split(".")[0] == "beancount":
            out.append(name)
    return sorted(out)


# ---- the rule on the tree ----------------------------------------------------------------------------------------


def test_no_engine_module_but_the_overlay_reaches_a_load_stage() -> None:
    found = [v for rel, src in source_files() for v in violations(rel, src, port.LOAD_STAGES)]
    assert found == []


def unused_stages(source: str, stages: frozenset[str]) -> set[str]:
    """The load stages a module never names as an attribute (of the port)."""
    used = {n.attr for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Attribute)}
    return set(stages - used)


def test_no_engine_module_but_r4_reaches_a_reconciliation_load() -> None:
    found = [v for rel, src in source_files() for v in violations(rel, src, port.RECONCILE_LOADS, RECONCILE_HOME)]
    assert found == []


def test_the_overlay_does_not_reach_a_reconciliation_load() -> None:
    # The replica must never call Beancount's own loader: R4 compares the two.
    source = (ENGINE_SRC / "ledger_engine" / "ledger" / "overlay.py").read_text(encoding="utf-8")
    assert violations("ledger_engine/ledger/overlay.py", source, port.RECONCILE_LOADS, RECONCILE_HOME) == []


def test_r4_uses_the_reconciliation_loads() -> None:
    source = (ENGINE_SRC / "ledger_engine" / "ledger" / "r4.py").read_text(encoding="utf-8")
    assert unused_stages(source, port.RECONCILE_LOADS) == set()


def test_a_planted_reconciliation_load_outside_r4_is_found() -> None:
    src = "from ledger_engine.ledger import beancount_port as p\np.reconcile_load('/l/main.bean')\n"
    assert violations(REL, src, port.RECONCILE_LOADS, RECONCILE_HOME) != []
    assert violations("ledger_engine/ledger/overlay.py", src, port.RECONCILE_LOADS, RECONCILE_HOME) != []
    assert violations("ledger_engine/ledger/r4.py", src, port.RECONCILE_LOADS, RECONCILE_HOME) == []


def test_the_overlay_uses_the_load_stages() -> None:
    # The rule's one allowed home really is where the load stages are used (a renamed overlay would leave the rule
    # guarding nothing).
    source = (ENGINE_SRC / "ledger_engine" / "ledger" / "overlay.py").read_text(encoding="utf-8")
    assert unused_stages(source, port.LOAD_STAGES) == set()


def test_a_home_that_misses_a_load_stage_is_found() -> None:
    assert unused_stages("port.book(e, o)\n", frozenset({"book", "validate"})) == {"validate"}


def classing_errors(
    public: set[str], stages: frozenset[str], wrappers: frozenset[str], reconcile: frozenset[str] = frozenset()
) -> list[str]:
    """Every exported callable must be in exactly one class, and each class names only exported callables."""
    out = [f"{n}: both a load stage and a wrapper" for n in sorted(stages & wrappers)]
    out += [f"{n}: a reconciliation load and another class" for n in sorted(reconcile & (stages | wrappers))]
    out += [f"{n}: not classed" for n in sorted(public - stages - wrappers - reconcile)]
    out += [
        f"{n}: classed but not an exported callable of the port"
        for n in sorted((stages | wrappers | reconcile) - public)
    ]
    return out


def test_every_exported_callable_of_the_port_is_classed() -> None:
    public = exported_callables(vars(port), port.__all__)
    assert classing_errors(public, port.LOAD_STAGES, port.WRAPPERS, port.RECONCILE_LOADS) == []


def test_a_reconciliation_load_classed_twice_is_found() -> None:
    """@regression-guard: a must-not-pass test of the classing check itself (test code), so it passes on the base."""
    assert classing_errors({"a", "b"}, frozenset({"a"}), frozenset(), frozenset({"a", "b"})) == [
        "a: a reconciliation load and another class"
    ]


def test_the_port_holds_nothing_of_beancount_under_a_public_name_it_does_not_export() -> None:
    assert leaked_names(vars(port), port.__all__) == []


def test_an_unclassed_or_doubly_classed_function_is_found() -> None:
    stages, wrappers = frozenset({"book"}), frozenset({"parse_string"})
    assert classing_errors({"book", "parse_string"}, stages, wrappers) == []
    assert classing_errors({"book", "parse_string", "load_file"}, stages, wrappers) == ["load_file: not classed"]
    assert classing_errors({"book", "parse_string"}, stages, wrappers | {"book"}) == [
        "book: both a load stage and a wrapper"
    ]
    assert classing_errors({"book"}, stages, wrappers) == [
        "parse_string: classed but not an exported callable of the port"
    ]


def test_an_exported_callable_is_told_from_a_type() -> None:
    namespace: dict[str, object] = {
        "book": port.book,
        "Transaction": port.Transaction,
        "Directive": port.Directive,
        "OptionsMap": port.OptionsMap,
        "BEANCOUNT_VERSION": port.BEANCOUNT_VERSION,
        "run": functools.partial(port.book),
    }
    assert exported_callables(namespace, list(namespace)) == {"book", "run"}


def test_a_beancount_name_or_a_constant_left_public_is_found() -> None:
    namespace: dict[str, object] = {
        "beancount_book": beancount_booking.book,
        "booking": beancount_booking,
        "OPTIONS": {"a": 1},
        "Decimal": Decimal,
        "sys": sys,
        "book": port.book,
        "_book": beancount_booking.book,
    }
    assert leaked_names(namespace, ["book"]) == ["OPTIONS", "beancount_book", "booking"]


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
        # Beancount's own steps, held by the port under private names, and names the port does not export (S0049
        # verifier F1a).
        "from ledger_engine.ledger.beancount_port import _book\n",
        "from ledger_engine.ledger import beancount_port as p\np._validate([], {}, None, None)\n",
        "from ledger_engine.ledger.beancount_port import beancount_book\n",
        "from ledger_engine.ledger import beancount_port as p\np.__dict__['book']([], {})\n",
        "from ledger_engine.ledger import beancount_port as p\nvars(p)['book']([], {})\n",
        # The port reached through another module that holds it (F1b).
        "from ledger_engine.ledger.overlay import port\nport.book([], {})\n",
        "from ledger_engine.ledger.overlay import port\nrun(port)\n",
        "from ledger_engine.ledger import overlay\noverlay.port.validate([], {})\n",
        "from ledger_engine.ledger.overlay import *\n",
        "from ledger_engine import ledger\nledger.beancount_port.book([], {})\n",
        "import ledger_engine.ledger\nledger_engine.ledger.beancount_port.book([], {})\n",
        "import ledger_engine\nf = ledger_engine.ledger.beancount_port.validate\n",
        "import ledger_engine.ledger.beancount_port\nq = ledger_engine.ledger.beancount_port\nq.book([], {})\n",
        "import importlib\nq = importlib.import_module('ledger_engine.ledger.beancount_port')\n",
        "import sys\nq = sys.modules['ledger_engine.ledger.' + 'beancount_port']\n",
        "from ledger_engine.ledger import no_such_module\n",
        "from ledger_engine.no_such_package import port\nport.book([], {})\n",
        "import ledger_engine.no_such_module as m\n",
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
        "from ledger_engine.ledger.overlay import port\nport.parse_string(b'')\n",
        "from ledger_engine.ledger.overlay import load, Loaded\n",
        "from ledger_engine.ledger import grammar\ngrammar.parse_account\n",
        "import ledger_engine.ledger.beancount_port\nx = ledger_engine.ledger.beancount_port.parse_string(b'')\n",
        '"""The beancount_port module is the gateway."""\n',
    ],
)
def test_ordinary_wrappers_and_unrelated_names_pass(source: str) -> None:
    assert violations(REL, source, STAGES) == []


def test_the_allowed_home_and_the_port_itself_are_exempt() -> None:
    src = "from ledger_engine.ledger import beancount_port as p\np.book([], {})\n"
    assert violations("ledger_engine/ledger/overlay.py", src, STAGES) == []
    assert violations("ledger_engine/ledger/beancount_port.py", "def book(): ...\nbook()\n", STAGES) == []
