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
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.platform.state_registry import REGISTRY, StateEntry

SRC = Path(__file__).resolve().parents[2] / "src"
# Calls whose result cannot change. Adding one is a reviewed change to this list (EP-04).
IMMUTABLE_CALLS = frozenset(
    {"frozenset", "tuple", "re.compile", "NewType", "TypeVar", "ParamSpec", "TypeVarTuple", "MappingProxyType"}
)
MUTABLE_CALLS = frozenset(
    {
        "list",
        "dict",
        "set",
        "bytearray",
        "defaultdict",
        "OrderedDict",
        "Counter",
        "deque",
        "ChainMap",
        "WeakValueDictionary",
        "WeakKeyDictionary",
        "WeakSet",
        "collections.defaultdict",
        "collections.OrderedDict",
        "collections.Counter",
        "collections.deque",
    }
)
MEMOIZERS = frozenset({"lru_cache", "cache", "functools.lru_cache", "functools.cache"})


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
        return dotted(value.func) in IMMUTABLE_CALLS  # their results are immutable whatever the arguments
    if isinstance(value, ast.BinOp | ast.UnaryOp):
        return all(immutable(v) for v in ast.iter_child_nodes(value) if isinstance(v, ast.expr))
    # A name, attribute or subscript is an alias or a type expression (`Literal[...]`); the definition it points
    # to is scanned where it lives. Stated limit: an alias of the stdlib's own mutable state (os.environ) passes.
    return isinstance(value, ast.Name | ast.Attribute | ast.Subscript)


def targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[str]:
    nodes = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [n.id for t in nodes for n in ast.walk(t) if isinstance(n, ast.Name)]


def scan(source: str, module: str) -> list[Found]:
    found: list[Found] = []

    def body(stmts: list[ast.stmt], in_class: bool) -> None:
        for s in stmts:
            if isinstance(s, ast.Assign | ast.AnnAssign | ast.AugAssign):
                value = s.value
                names = targets(s)
                if names == ["__all__"] and not in_class:
                    continue
                if value is None:
                    continue
                if isinstance(s, ast.AugAssign):
                    found.extend(Found(module, n, "augmented assignment at module level") for n in names)
                    continue
                if in_class:
                    mutable = isinstance(
                        value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp
                    ) or (isinstance(value, ast.Call) and dotted(value.func) in MUTABLE_CALLS)
                    if mutable:
                        found.extend(Found(module, n, "mutable class attribute") for n in names)
                elif not immutable(value):
                    found.extend(Found(module, n, "mutable or unknown module-level value") for n in names)
            elif isinstance(s, ast.ClassDef):
                body(s.body, in_class=True)
                for item in s.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        decorated(item)
            elif isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef):
                decorated(s)
            elif isinstance(s, ast.If | ast.Try | ast.With):
                for part in ("body", "orelse", "finalbody"):
                    body(getattr(s, part, []), in_class)
                for handler in getattr(s, "handlers", []):
                    body(handler.body, in_class)

    def decorated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for d in fn.decorator_list:
            name = dotted(d.func if isinstance(d, ast.Call) else d)
            if name in MEMOIZERS:
                found.append(Found(module, fn.name, f"memoizing decorator {name}"))

    tree = ast.parse(source)
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
    allowed = {(e.module, e.name) for e in registry}
    return [f for f in found if (f.module, f.name) not in allowed]


@pytest.mark.inv("INV-11")
def test_no_unregistered_module_state_in_the_tree() -> None:
    found = [f for module, text in modules().items() for f in scan(text, module)]
    assert unregistered(found, REGISTRY) == []


@pytest.mark.inv("INV-11")
def registry_problems(registry: tuple[StateEntry, ...], sources: dict[str, str]) -> list[str]:
    """Entries without a bound or eviction, or naming a module or stateful name that does not exist."""
    out: list[str] = []
    for e in registry:
        if not e.bound.strip() or not e.eviction.strip():
            out.append(f"{e.module}.{e.name}: bound and eviction are required")
        if e.module not in sources or not any(f.name == e.name for f in scan(sources[e.module], e.module)):
            out.append(f"{e.module}.{e.name}: stale entry (no such stateful name)")
    return out


def test_every_registry_entry_exists_and_declares_its_bounds() -> None:
    assert registry_problems(REGISTRY, modules()) == []


@pytest.mark.parametrize(
    "entry",
    [
        StateEntry("app.platform.gates", "_sem", "process", "", "none"),
        StateEntry("app.platform.gates", "_sem", "process", "2 slots", " "),
        StateEntry("app.platform.gone", "_sem", "process", "2 slots", "none"),
        StateEntry("app.platform.gates", "_renamed", "process", "2 slots", "none"),
    ],
)
def test_bad_registry_entries_fail(entry: StateEntry) -> None:
    sources = {"app.platform.gates": "_sem = make_semaphore()\n"}
    assert registry_problems((entry,), sources) != [], entry


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
    ],
)
def test_immutable_values_pass(source: str) -> None:
    assert scan(source, "app.planted") == [], source


@pytest.mark.inv("INV-11")
def test_a_registered_name_passes_and_an_unregistered_one_does_not() -> None:
    found = scan("_sem = make_semaphore()\n_other = []\n", "app.platform.gates")
    entry = StateEntry("app.platform.gates", "_sem", "process", "2 slots", "none (fixed size)")
    assert [f.name for f in unregistered(found, (entry,))] == ["_other"]
