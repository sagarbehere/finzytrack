"""INV-11 and EP-04: no module-level mutable state outside `app.platform.state_registry` (BL-0011).

The scan reads every module of `app` as source (AST), so it sees what a module would hold without importing
it. A module-level or class-level name may hold only a value that cannot change: a constant, a tuple or
frozenset of them, a compiled pattern, a type, or the result of a call on the short allowlist below. Anything
else (a list, dict or set, any other call, a comprehension), any `global`/`nonlocal` statement and any
memoizing decorator (`lru_cache`, `cache`) is state, and must be registered with its owner, bound and eviction.
Each rule sits beside the planted case that must not pass (EP-29).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

import pytest

from app.platform.observability.events import Event, Field
from app.platform.state_registry import REGISTRY, StateEntry

SRC = Path(__file__).resolve().parents[2] / "src"
# Calls whose result cannot change. Adding one is a reviewed change to this list (EP-04).
IMMUTABLE_CALLS = frozenset({"re.compile", "NewType", "TypeVar", "ParamSpec", "TypeVarTuple"})
# Immutable only when every element is: `tuple([[]])` holds a list.
CONTAINER_CALLS = frozenset({"frozenset", "tuple"})
# Frozen, slotted records of this tree, immutable only when every argument is (S0035: the registry's own entries,
# `StateEntry`, a frozen dataclass of strings; before its first entry the registry was `()`. S0039: the event
# registry's `Event` and `Field`, frozen dataclasses of strings, enums, frozensets and tuples of `Field`).
RECORD_CALLS = frozenset({"StateEntry", "Event", "Field"})
# In a class body, a per-instance default is not class state.
CLASS_CALLS = frozenset({"field", "dataclasses.field"})
# Memoizing decorators by their last name part, wherever imported from (functools, cachetools, ...).
MEMOIZER_NAMES = frozenset({"lru_cache", "cache", "cached", "memoize", "memoized"})
MEMOIZER_MODULES = frozenset({"cachetools"})


@dataclass(frozen=True)
class Found:
    module: str
    name: str
    why: str


def dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def immutable(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, ast.Tuple):
        return all(immutable(e) for e in value.elts)
    if isinstance(value, ast.Call):
        name = dotted(value.func)
        if name in IMMUTABLE_CALLS:
            return True
        if name in CONTAINER_CALLS:
            return all(elements_immutable(a) for a in value.args) and not value.keywords
        if name in RECORD_CALLS:
            return all(immutable(a) for a in value.args) and all(immutable(k.value) for k in value.keywords)
        return False
    if isinstance(value, ast.BinOp | ast.UnaryOp):
        return all(immutable(v) for v in ast.iter_child_nodes(value) if isinstance(v, ast.expr))
    if isinstance(value, ast.Subscript):
        return isinstance(value.value, ast.Name | ast.Attribute)  # a type expression such as `Literal[...]`
    # A name or attribute is an alias; the definition it points to is scanned where it lives. Stated limit:
    # an alias of the stdlib's own mutable state (`os.environ`) passes.
    return isinstance(value, ast.Name | ast.Attribute)


def elements_immutable(arg: ast.expr) -> bool:
    if isinstance(arg, ast.List | ast.Set | ast.Tuple):
        return all(immutable(e) for e in arg.elts)
    return immutable(arg)


def targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[str]:
    nodes = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [n.id for t in nodes for n in ast.walk(t) if isinstance(n, ast.Name)]


def import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted name it was imported as (`memo` -> `functools.lru_cache`, `ft` -> `functools`)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
    return out


def scan(source: str, module: str) -> list[Found]:
    found: list[Found] = []
    tree = ast.parse(source)
    aliases = import_aliases(tree)

    def resolved(node: ast.expr) -> str:
        name = dotted(node)
        head, _, rest = name.partition(".")
        base = aliases.get(head, head)
        return f"{base}.{rest}" if rest else base

    def body(stmts: list[ast.stmt], in_class: bool) -> None:
        for s in stmts:
            if isinstance(s, ast.Assign | ast.AnnAssign | ast.AugAssign):
                names = targets(s)
                if (names == ["__all__"] and not in_class) or s.value is None:
                    continue
                if isinstance(s, ast.AugAssign):
                    found.extend(Found(module, n, "augmented assignment at module or class level") for n in names)
                elif not (
                    immutable(s.value)
                    or (in_class and isinstance(s.value, ast.Call) and dotted(s.value.func) in CLASS_CALLS)
                ):
                    where = "class attribute" if in_class else "module-level value"
                    found.extend(Found(module, n, f"mutable or unknown {where}") for n in names)
            elif isinstance(s, ast.Expr):
                if not isinstance(s.value, ast.Constant):  # a docstring or `...` is fine; anything else acts
                    found.append(Found(module, "<expression>", f"module-level expression at line {s.lineno}"))
            elif isinstance(s, ast.ClassDef):
                body(s.body, in_class=True)
                for item in s.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        decorated(item)
            elif isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef):
                decorated(s)
            elif isinstance(s, ast.Match):
                for case in s.cases:
                    body(case.body, in_class)
            else:
                # if, for, while, try, try/except*, with (sync and async): every nested block of statements
                for part in ("body", "orelse", "finalbody"):
                    body(getattr(s, part, []), in_class)
                for handler in getattr(s, "handlers", []):
                    body(handler.body, in_class)
                if isinstance(s, ast.For | ast.AsyncFor):
                    found.extend(
                        Found(module, n, "module-level loop variable")
                        for n in [x.id for x in ast.walk(s.target) if isinstance(x, ast.Name)]
                    )

    def decorated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for d in fn.decorator_list:
            name = resolved(d.func if isinstance(d, ast.Call) else d)
            parts = name.split(".")
            if parts[-1] in MEMOIZER_NAMES or parts[0] in MEMOIZER_MODULES:
                found.append(Found(module, fn.name, f"memoizing decorator {name}"))

    body(tree.body, in_class=False)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global | ast.Nonlocal):
            found.extend(Found(module, n, f"{type(node).__name__.lower()} statement") for n in node.names)
    return found


def modules() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        out[".".join(parts)] = path.read_text(encoding="utf-8")
    return out


def unregistered(found: list[Found], registry: tuple[StateEntry, ...]) -> list[Found]:
    """Every module-level or class-level state the scan found: container-owned parts live on container attributes,
    and no entry may be process-wide (ADR-0064, S0047), so a registry entry never makes a module-level name state."""
    del registry  # kept in the signature: the registry decides nothing here since no entry is process-wide
    return list(found)


@pytest.mark.inv("INV-11")
def test_no_unregistered_module_state_in_the_tree() -> None:
    found = [f for module, text in modules().items() for f in scan(text, module)]
    assert unregistered(found, REGISTRY) == []


def defined_names(source: str) -> set[str]:
    return {n.name for n in ast.parse(source).body if isinstance(n, ast.ClassDef | ast.FunctionDef)} | {
        t for n in ast.parse(source).body if isinstance(n, ast.Assign | ast.AnnAssign) for t in targets(n)
    }


def registry_problems(registry: tuple[StateEntry, ...], sources: dict[str, str]) -> list[str]:
    """ARCHITECTURE.md §2.5: every entry has a container as its owner, a bound and an eviction, and names the class
    or factory that holds the state; no entry is listed twice. No entry is process-wide: ADR-0064 retired the
    heavy-load semaphore, the one process-wide entry ADR-0041 allowed (INV-11; S0047)."""
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for e in registry:
        key = f"{e.module}.{e.name}"
        if (e.module, e.name) in seen:
            out.append(f"{key}: listed twice")
        seen.add((e.module, e.name))
        owner = str(e.owner)  # widened: the registry is data, and a bad entry must be reported, not assumed away
        if owner == "process":
            out.append(f"{key}: a process-wide entry (ADR-0064: no process-wide state; INV-11)")
        elif owner not in ("UserContainer", "WorkspaceContainer"):
            out.append(f"{key}: unknown owner {owner!r}")
        if not e.bound.strip() or not e.eviction.strip():
            out.append(f"{key}: bound and eviction are required")
        source = sources.get(e.module)
        if source is None:
            out.append(f"{key}: stale entry (no such module)")
        elif e.name not in defined_names(source):
            out.append(f"{key}: stale entry (no such class or name)")
    return out


@pytest.mark.inv("INV-11")
def test_every_registry_entry_exists_and_declares_its_bounds() -> None:
    assert registry_problems(REGISTRY, modules()) == []


SOURCES = {
    "app.platform.plans": "class PlanStore:\n    pass\n",
    "app.platform.jobs": "class JobQueue:\n    pass\n",
    "app.recipes.memo": "class RecipeMemo:\n    pass\n",
}


@pytest.mark.inv("INV-11")
def test_good_registry_entries_pass() -> None:
    entries = (
        StateEntry("app.platform.plans", "PlanStore", "UserContainer", "200 plans", "TTL 1 h, then LRU"),
        StateEntry("app.platform.jobs", "JobQueue", "UserContainer", "max_queued jobs", "a job leaves when it ends"),
        StateEntry("app.recipes.memo", "RecipeMemo", "WorkspaceContainer", "configurable bytes", "LRU"),
    )
    assert registry_problems(entries, SOURCES) == []


@pytest.mark.inv("INV-11")
@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ((StateEntry("app.platform.plans", "PlanStore", "UserContainer", "", "LRU"),), "bound and eviction"),
        ((StateEntry("app.platform.plans", "PlanStore", "UserContainer", "200 plans", " "),), "bound and eviction"),
        ((StateEntry("app.platform.gone", "PlanStore", "UserContainer", "200 plans", "LRU"),), "no such module"),
        ((StateEntry("app.platform.plans", "Gone", "UserContainer", "1", "LRU"),), "no such class"),
        ((StateEntry("app.platform.plans", "PlanStore", "Global", "1", "LRU"),), "unknown owner"),  # type: ignore[arg-type]
        ((StateEntry("app.platform.plans", "PlanStore", "UserContainer", "1", "LRU"),) * 2, "listed twice"),
        ((StateEntry("app.platform.jobs", "JobQueue", "process", "1", "none"),), "process-wide"),  # type: ignore[arg-type]
    ],
)
def test_bad_registry_entries_fail(entries: tuple[StateEntry, ...], problem: str) -> None:
    assert any(problem in p for p in registry_problems(entries, SOURCES)), registry_problems(entries, SOURCES)


# S0047 (convergence audit C1): ADR-0064 retired the heavy-load semaphore, the one process-wide entry ADR-0041 allowed;
# the engine job queue hangs off `UserContainer` (INV-11), so no registry entry may be process-wide, and naming a
# module-level name in one does not make that name allowed state.
@pytest.mark.inv("INV-11")
def test_a_process_wide_entry_is_refused() -> None:
    entry = StateEntry("app.platform.jobs", "_queue", "process", "100 jobs", "none")  # type: ignore[arg-type]
    problems = registry_problems((entry,), {"app.platform.jobs": "_queue = make_queue()\n"})
    assert any("process-wide" in p for p in problems), problems


@pytest.mark.inv("INV-11")
def test_a_module_level_name_is_state_even_when_an_entry_names_it() -> None:
    found = scan("_queue = make_queue()\n", "app.platform.jobs")
    entry = StateEntry("app.platform.jobs", "_queue", "process", "100 jobs", "none")  # type: ignore[arg-type]
    assert [f.name for f in unregistered(found, (entry,))] == ["_queue"]


@pytest.mark.inv("INV-11")
@pytest.mark.parametrize(
    "source",
    [
        "_cache = {}\n",
        "_seen: set[str] = set()\n",
        "_items = []\n",
        "_by_ws = {k: 1 for k in ()}\n",
        "from collections import defaultdict\n_index = defaultdict(list)\n",
        "import collections\n_q = collections.deque()\n",
        "_client = make_client()\n",  # an unknown call may build state
        "_count = 0\n_count += 1\n",
        "def bump():\n    global _count\n    _count = 1\n",
        "def outer():\n    n = 0\n    def inner():\n        nonlocal n\n        n += 1\n",
        "from functools import lru_cache\n@lru_cache\ndef f(x):\n    return x\n",
        "import functools\n@functools.lru_cache(maxsize=None)\ndef f(x):\n    return x\n",
        "import functools\n@functools.cache\ndef f(x):\n    return x\n",  # privacy-scan: allow email
        "class Store:\n    rows = {}\n",
        "class Store:\n    rows: list[int] = []\n",
        "class Store:\n    @staticmethod\n    @functools.cache\n    def f():\n        return 1\n",
        "import sys\nif sys.platform == 'win32':\n    _handles = {}\n",
        "try:\n    _x = []\nexcept Exception:\n    pass\n",
        "_pair = ([], 1)\n",
        "_d = dict(a=1)\n",
        # S0017 verifier: class attributes from unknown calls, other blocks, expressions, aliases, tuple contents
        "import threading\nclass Store:\n    _local = threading.local()\n",
        "import asyncio\nclass Jobs:\n    queue = asyncio.Queue()\n",
        "import weakref\nclass Reg:\n    _all = weakref.WeakValueDictionary()\n",
        "(_cache := {})\n",
        "import sys\nsetattr(sys.modules[__name__], '_cache', {})\n",
        "import other\nother.CACHE.update(a=1)\n",
        "for _i in range(3):\n    pass\n",
        "while True:\n    _x = []\n    break\n",
        "match 1:\n    case 1:\n        _x = []\n",
        "try:\n    pass\nexcept* ValueError:\n    _x = []\n",
        "with open('f') as fh:\n    _x = []\n",
        "try:\n    pass\nexcept ValueError:\n    _x = []\n",
        "from functools import lru_cache as memo\n@memo\ndef f(x):\n    return x\n",
        "import functools as ft\n@ft.cache\ndef f(x):\n    return x\n",  # privacy-scan: allow email
        "import cachetools\n@cachetools.cached(cache={})\ndef f(x):\n    return x\n",  # privacy-scan: allow email
        "_t = tuple([[]])\n",
        "_s = frozenset([frozenset(), []])\n",
        # S0035: a registry entry is a record of constants; one holding a mutable value or an unknown call is state.
        "_r = (StateEntry('m', 'n', 'UserContainer', [], 'x'),)\n",
        "_r = (StateEntry('m', 'n', 'UserContainer', '1', eviction={}),)\n",
        "_r = (StateEntry('m', make(), 'UserContainer', '1', 'x'),)\n",
        "_r = Entry('m', 'n', 'UserContainer', '1', 'x')\n",
        "_x = ({},)[0]\n",
        # S0039: an event or field of the registry holding a mutable value or an unknown call is state.
        "E = Event('a.b', 'info', 'platform', [Field('x', Kind.ID)])\n",
        "E = Event('a.b', 'info', 'platform', (Field('x', Kind.ENUM, {'a'}),))\n",
        "E = Event('a.b', 'info', 'platform', (Field('x', Kind.ENUM, values=set()),))\n",
        "E = Event('a.b', 'info', make(), ())\n",
    ],
)
def test_planted_state_is_found(source: str) -> None:
    assert unregistered(scan(source, "app.planted"), REGISTRY) != [], source


@pytest.mark.inv("INV-11")
@pytest.mark.parametrize(
    "source",
    [
        '"""Docstring."""\n',
        "__all__ = ['f']\n",
        "LIMIT = 3\nNAME = 'x'\nRATIO = 1.5\nNOTHING = None\nFLAG = True\n",
        "PAIR = (1, 'a')\nKINDS = frozenset({'a', 'b'})\n",
        "import re\nPATTERN = re.compile(r'x')\n",
        "from typing import NewType, TypeVar\nKey = NewType('Key', str)\nT = TypeVar('T')\n",
        "type Owner = str\n",
        "from typing import Literal\nKind = Literal['a', 'b']\n",
        "from dataclasses import dataclass, field\n@dataclass\nclass A:\n"
        "    items: list[int] = field(default_factory=list)\n",
        "class A:\n    limit = 3\n",
        "def f():\n    local = {}\n    return local\n",
        "SECONDS = 60 * 60\n",
        "REGISTRY = (StateEntry('m', 'n', 'UserContainer', 'one ' 'process', eviction='close()'),)\n",
        "E = Event('a.b', 'info', 'platform', (Field('k', Kind.ENUM, frozenset({'a', 'b'})),), source='engine')\n",
    ],
)
def test_immutable_values_pass(source: str) -> None:
    assert scan(source, "app.planted") == [], source


def test_the_record_calls_are_frozen_slotted_dataclasses() -> None:
    """RECORD_CALLS trusts a call by its name; each name must be a frozen, slotted record of this tree."""
    records = {"StateEntry": StateEntry, "Event": Event, "Field": Field}
    assert set(records) == RECORD_CALLS
    for name, cls in records.items():
        assert is_dataclass(cls), name
        assert getattr(cls, "__dataclass_params__").frozen, name  # noqa: B009 (not in the class's static type)
        assert hasattr(cls, "__slots__"), name
        assert fields(cls), name
