"""INV-10: every in-memory cache is per user, evictable and bounded, declared in the state registry (BL-0010, S0093).

`app.platform.state_registry` is the one registry of INV-10 and INV-11 (ARCHITECTURE.md §2.5): each entry names the
class a container holds, its bound and its eviction. The globals test (`test_globals.py`, INV-11) refuses state at
module and class level in `app`. This file adds what that scan cannot see:

1. **The long-lived graph, at run time.** Everything the composition root builds and keeps for the process's life
   (`app.main.wire` and the log) is walked through attributes, slots, dataclass fields, closures and bound methods.
   Anything that can change after it is built (a dict, list, set, deque, bytearray, or any object with an attribute
   or slot that can be rebound: everything but a frozen dataclass and a class with empty `__slots__`) must sit inside
   an instance of a registered class, whose entry declares how big it may grow and how it shrinks; the walk does not
   enter a registered holder. So a cache added to any long-lived object, at construction or later (a slot or
   attribute filled on first use), is refused until it is registered. Every entry must be
   reached from the graph: an entry nothing holds is stale.
2. **The engine's source.** The engine serves job after job (engine-jobs §5.2), so a module-level store there
   outlives every job. The globals scan covers only `app`; here the engine's tree is scanned for the cache forms:
   a memoizing decorator, `global`/`nonlocal`, and a module-level name written to from inside a function (an item
   or attribute assigned or deleted, or a mutating method called on a name bound to a container). The engine's
   module-level tables are built at import and never written afterwards, which this holds.
3. **A bound that says there is none**, or an eviction that says it never happens, is refused (INV-10: "evictable
   and has a bound").

Stated limits: a registered holder's insides are its entry's word (the review checks the entry against the code);
a frozen field rebound with `object.__setattr__` is not seen; in the engine, a write to another module's
object through its import (`other.TABLE[k] = v`) and a store built by a call the scan does not know as a container
(`T = make()`, then `T.append(...)`) are not seen; library objects built by the web framework (FastAPI's
routes and middleware, fixed by code) are not walked; INV-10 speaks of in-memory caches, so the
caches on disk (the mirror's generations, the engine's bytecode, the log files) are outside it (S0093 journal).
Each rule sits beside the planted case that must not pass (EP-29).
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import re
import types
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath

import pytest

from app import main
from app.platform.observability.events import Event
from app.platform.state_registry import REGISTRY, StateEntry
from tests.architecture.test_globals import MEMOIZER_MODULES, MEMOIZER_NAMES, dotted, import_aliases, scan

pytestmark = pytest.mark.inv("INV-10")

ENGINE_SRC = Path(__file__).resolve().parents[3] / "engine" / "src"

# Values that cannot hold anything that grows.
ATOMS: tuple[type, ...] = (
    type(None),
    bool,
    int,
    float,  # not money: a value kind the walk accepts as unchangeable
    complex,
    str,
    bytes,
    Decimal,
    range,
    PurePath,
    date,
    datetime,
    time,
    timedelta,
    tzinfo,
    Enum,
    type,
    re.Pattern,
    types.ModuleType,  # module state is the globals test's (INV-11); a module is never entered here
)


# -- 1. The long-lived graph -----------------------------------------------------------------------------------------


def class_key(obj: object) -> str:
    t = type(obj)
    return f"{t.__module__}.{t.__qualname__}"


def frozen_dataclass(obj: object) -> bool:
    if isinstance(obj, type) or not dataclasses.is_dataclass(obj):
        return False
    params: object = getattr(obj, "__dataclass_params__", None)
    return bool(getattr(params, "frozen", False))


def slot_names(obj: object) -> list[str]:
    out: list[str] = []
    for cls in type(obj).__mro__:
        names = cls.__dict__.get("__slots__", ())
        out += [n for n in ((names,) if isinstance(names, str) else names) if n not in ("__weakref__", "__dict__")]
    return out


def slots_of(obj: object) -> Iterator[tuple[str, object]]:
    for name in slot_names(obj):
        try:
            yield name, getattr(obj, name)
        except AttributeError:
            continue  # an unset slot holds nothing (yet)


def children(obj: object) -> Iterator[tuple[str, object]] | None:
    """What `obj` holds, if it can hold nothing that grows by itself; None when `obj` itself can change."""
    if isinstance(obj, tuple | frozenset):
        return ((f"[{i}]", v) for i, v in enumerate(obj))  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if isinstance(obj, types.FunctionType):
        cells = obj.__closure__ or ()
        held = [(f"<closure {n}>", c.cell_contents) for n, c in zip(obj.__code__.co_freevars, cells, strict=True)]
        held += [(f"<default {i}>", v) for i, v in enumerate(obj.__defaults__ or ())]
        held += [(f"<default {k}>", v) for k, v in (obj.__kwdefaults__ or {}).items()]
        return iter(held)  # never `__globals__`: module state is the globals test's
    if isinstance(obj, types.MethodType):
        return iter([("__self__", obj.__self__), ("__func__", obj.__func__)])
    if isinstance(obj, types.BuiltinFunctionType | types.BuiltinMethodType):
        owner = obj.__self__
        return iter([] if owner is None or isinstance(owner, types.ModuleType) else [("__self__", owner)])
    if isinstance(obj, functools.partial):
        p: functools.partial[object] = obj  # pyright: ignore[reportUnknownVariableType]
        return iter([("func", p.func), *((f"args[{i}]", a) for i, a in enumerate(p.args)), *p.keywords.items()])
    if frozen_dataclass(obj):
        named = [(f.name, getattr(obj, f.name)) for f in dataclasses.fields(obj)]  # pyright: ignore[reportArgumentType]
        extra = [(k, v) for k, v in getattr(obj, "__dict__", {}).items() if k not in dict(named)]
        return iter([*named, *extra, *slots_of(obj)])
    if not hasattr(obj, "__dict__") and type(obj).__module__ != "builtins" and not slot_names(obj):
        return iter([])  # a slotted object with no slot: it holds nothing, now or later
    return None  # anything else can change: a slot or attribute can be rebound to a store, lazily


@dataclass(frozen=True)
class Walk:
    reached: frozenset[str]  # the registry keys of the holders reached
    unregistered: tuple[str, ...]  # "path: type" of each value that can change outside a registered holder


def walk(roots: dict[str, object], registry: tuple[StateEntry, ...]) -> Walk:
    registered = {f"{e.module}.{e.name}" for e in registry}
    reached: set[str] = set()
    found: list[str] = []
    seen: set[int] = set()
    stack: list[tuple[str, object]] = list(roots.items())
    while stack:
        path, obj = stack.pop()
        if id(obj) in seen or isinstance(obj, ATOMS):
            continue
        seen.add(id(obj))
        key = class_key(obj)
        if key in registered:
            reached.add(key)
            continue
        held = children(obj)
        if held is None:
            found.append(f"{path}: {key}")
            continue
        stack.extend((f"{path}.{name}", value) for name, value in held)
    return Walk(frozenset(reached), tuple(sorted(found)))


def graph_problems(roots: dict[str, object], registry: tuple[StateEntry, ...]) -> list[str]:
    w = walk(roots, registry)
    out = [f"{u}: can change and is not inside a registered holder" for u in w.unregistered]
    out += [
        f"{e.module}.{e.name}: registered but not reached from the composition root (stale)"
        for e in registry
        if f"{e.module}.{e.name}" not in w.reached
    ]
    return out


def no_log(event: Event, /, **fields: object) -> None:
    pass


@pytest.fixture(params=[(), ("--frozen-clock", "2026-11-09T10:00:00+05:30")], ids=["system-clock", "frozen-clock"])
def product_roots(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[dict[str, object]]:
    """What `app.main.main` builds and keeps for the process's life, before the serve: the log and the wiring, with
    each clock the composition root can choose."""
    data, cache = tmp_path / "data", tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    extra: tuple[str, ...] = request.param
    args = main.parse_args(["--app-local-data", str(data), "--app-cache", str(cache), *extra])
    log = main.open_log(args, "9.9.9-test")
    assert isinstance(log, main.EventLog), log
    wiring = main.wire(args, log, "9.9.9-test")
    try:
        yield {"log": log, "wiring": wiring}
    finally:
        wiring.engine.close()
        log.close()


def test_every_long_lived_store_of_the_product_is_registered(product_roots: dict[str, object]) -> None:
    """It failed on 321be18d: `Uuid7Ids` and `NoWorkspaces` had a `__dict__` (now `__slots__ = ()`)."""
    assert graph_problems(product_roots, REGISTRY) == []


class Held:
    """A registered holder of the planted cases: its insides are its entry's word."""

    def __init__(self) -> None:
        self.rows: dict[str, int] = {}


@dataclass(frozen=True, slots=True)
class Frozen:
    part: object


@dataclass(frozen=True)
class FrozenWithDict:
    part: object


@dataclass(slots=True)
class Unfrozen:
    part: object


class Slotted:
    __slots__ = ("part",)

    def __init__(self, part: object) -> None:
        self.part = part


class Stateless:
    __slots__ = ()


class Plain:
    def lookup(self) -> int:
        return 1


HELD = StateEntry(__name__, "Held", "UserContainer", "200 rows", "LRU")


def closure_over(value: object) -> object:
    def read() -> object:
        return value

    return read


@pytest.mark.parametrize(
    ("root", "where"),
    [
        (Frozen({}), "root.part: builtins.dict"),
        (Frozen([1]), "root.part: builtins.list"),
        (Frozen(set[int]()), "root.part: builtins.set"),
        (Frozen(bytearray()), "root.part: builtins.bytearray"),
        (Frozen((1, (dict[str, int](),))), "root.part.[1].[0]: builtins.dict"),
        (Slotted({}), f"root: {__name__}.Slotted"),
        (Slotted(None), f"root: {__name__}.Slotted"),  # a slot set to None now can hold a dict on first use
        (Frozen(Slotted.__new__(Slotted)), f"root.part: {__name__}.Slotted"),  # an unset slot too
        (FrozenWithDict({}), "root.part: builtins.dict"),
        (Plain(), f"root: {__name__}.Plain"),
        (
            Unfrozen(1),
            f"root: {__name__}.Unfrozen",
        ),  # a field can be rebound to a growing store  # a `__dict__` can grow a cache later, lazily
        (Frozen(closure_over({})), "root.part.<closure value>: builtins.dict"),
        (Frozen(Plain().lookup), f"root.part.__self__: {__name__}.Plain"),
        (Frozen(functools.partial(print, {})), "root.part.args[0]: builtins.dict"),
        (Frozen(list[int]().append), "root.part.__self__: builtins.list"),
    ],
)
def test_a_store_outside_a_registered_holder_is_refused(root: object, where: str) -> None:
    problems = graph_problems({"root": root, "held": Held()}, (HELD,))
    assert any(p.startswith(where) for p in problems), problems


def test_a_registered_holder_is_not_entered_and_immutable_parts_pass() -> None:
    root = Frozen((Held(), Frozen(Frozen(("a", 1, Decimal(2), Path("/x"), None))), Stateless()))
    assert graph_problems({"root": root}, (HELD,)) == []


def test_an_entry_nothing_holds_is_stale() -> None:
    problems = graph_problems({"root": Frozen(1)}, (HELD,))
    assert problems == [f"{__name__}.Held: registered but not reached from the composition root (stale)"]


def test_an_entry_naming_a_factory_is_never_reached() -> None:
    """Entries name the class a container holds: a factory's name matches no instance, so it reads as stale."""
    entry = StateEntry(__name__, "closure_over", "UserContainer", "1", "LRU")
    assert any("stale" in p for p in graph_problems({"root": Frozen(closure_over(1))}, (entry,)))


# -- 2. The engine's source ------------------------------------------------------------------------------------------

MUTATORS = frozenset(
    {
        "append",
        "appendleft",
        "extend",
        "extendleft",
        "insert",
        "add",
        "update",
        "setdefault",
        "pop",
        "popitem",
        "popleft",
        "clear",
        "remove",
        "discard",
        "sort",
        "reverse",
        "__setitem__",
        "__delitem__",
    }
)


# Calls that build a container: a mutating method called on a module-level name bound to one of these (or to a
# display, a comprehension or another name) writes to a store. Any other call's result (`decimal.Context(...)`, whose
# `add` is arithmetic) is not taken for a store by its method names; its items and attributes still are.
CONTAINER_CALLS = frozenset(
    {
        "dict",
        "list",
        "set",
        "bytearray",
        "deque",
        "defaultdict",
        "OrderedDict",
        "Counter",
        "ChainMap",
        "SimpleNamespace",
    }
)


def module_names(tree: ast.Module) -> dict[str, ast.expr | None]:
    """Every name the module body binds by assignment, in any block at module level (never inside a function or a
    class), with the value it is bound to (None when it has none)."""
    out: dict[str, ast.expr | None] = {}

    def block(stmts: list[ast.stmt]) -> None:
        for s in stmts:
            if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if isinstance(s, ast.Assign | ast.AnnAssign):
                targets = s.targets if isinstance(s, ast.Assign) else [s.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                            out[n.id] = s.value
            for part in ("body", "orelse", "finalbody"):
                block(getattr(s, part, []))
            for handler in getattr(s, "handlers", []):
                block(handler.body)
            for case in getattr(s, "cases", []):
                block(case.body)

    block(tree.body)
    return out


def container_like(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Call):
        return dotted(value.func).split(".")[-1] in CONTAINER_CALLS
    return value is not None


def root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def engine_cache_findings(source: str, module: str) -> list[str]:
    """The cache forms in one engine module: memoizers, `global`/`nonlocal`, module-level names written to inside
    a function. A function's own local of the same name is not the module's (a parameter or a local assignment)."""
    tree = ast.parse(source)
    found = [
        f"{module}.{f.name}: {f.why}"
        for f in scan(source, module)
        if f.why.startswith(("memoizing decorator", "global statement", "nonlocal statement"))
    ]
    aliases = import_aliases(tree)
    for node in ast.walk(tree):  # a memoizer the globals scan reads by its import: also a bare call `cache(f)`
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            head, _, rest = name.partition(".")
            full = f"{aliases.get(head, head)}.{rest}" if rest else aliases.get(head, head)
            parts = full.split(".")
            if parts[-1] in MEMOIZER_NAMES and (parts[0] in MEMOIZER_MODULES or parts[0] == "functools"):
                found.append(f"{module}: memoizing call {full} at line {node.lineno}")
    shared = module_names(tree)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        args = fn.args
        local = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
        local |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
        body = fn.body if isinstance(fn.body, list) else [ast.Expr(fn.body)]
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    local.add(sub.id)
        writes: list[tuple[str, int]] = []  # (name, line)
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.Delete):
                    targets = sub.targets if isinstance(sub, ast.Assign | ast.Delete) else [sub.target]
                    for t in targets:
                        if isinstance(t, ast.Attribute | ast.Subscript) and (name := root_name(t)) is not None:
                            writes.append((name, sub.lineno))
                elif (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in MUTATORS
                    and (name := root_name(sub.func.value)) is not None
                    and (not isinstance(sub.func.value, ast.Name) or container_like(shared.get(name)))
                ):
                    writes.append((name, sub.lineno))
        found += [
            f"{module}.{name}: module-level name written inside a function at line {line}"
            for name, line in writes
            if name in shared and name not in local
        ]
    return sorted(set(found))


def engine_modules() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(ENGINE_SRC.rglob("*.py")):
        rel = path.relative_to(ENGINE_SRC).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        out[".".join(parts)] = path.read_text(encoding="utf-8")
    return out


def test_the_engine_holds_no_cache_across_jobs() -> None:
    """@regression-guard (it holds on 321be18d; the planted cases below are its must-not-pass tests)."""
    sources = engine_modules()
    assert len(sources) > 40  # the tree was found (52 modules at S0093)
    assert [f for module, text in sources.items() for f in engine_cache_findings(text, module)] == []


@pytest.mark.parametrize(
    "source",
    [
        "from functools import lru_cache\n@lru_cache(maxsize=None)\ndef f(x):\n    return x\n",
        "import functools\n@functools.cache\ndef f(x):\n    return x\n",  # privacy-scan: allow email
        "import functools\ndef f(x):\n    return x\ng = functools.lru_cache(f)\n",
        "from functools import cache as memo\ndef f(x):\n    return x\ng = memo(f)\n",
        "import cachetools\n@cachetools.cached(cache={})\ndef f(x):\n    return x\n",  # privacy-scan: allow email
        "_SEEN = {}\ndef f(k, v):\n    _SEEN[k] = v\n",
        "_SEEN = {}\ndef f(k):\n    del _SEEN[k]\n",
        "_LOADS = []\ndef f(r):\n    _LOADS.append(r)\n",
        "_BY = {}\ndef f(k, v):\n    _BY.setdefault(k, []).append(v)\n",
        "_BY = {}\nclass C:\n    def m(self, k):\n        _BY.update({k: self})\n",
        "import types\nSTATE = types.SimpleNamespace()\ndef f(v):\n    STATE.last = v\n",
        "_T = {'a': []}\ndef f(v):\n    _T['a'].append(v)\n",
        "_N = 0\ndef f():\n    global _N\n    _N = 1\n",
        "def f():\n    n = 0\n    def g():\n        nonlocal n\n        n = 1\n    return g\n",
        "_C = {}\nhook = lambda k: _C.pop(k)\n",
        "if True:\n    _C = {}\ndef f(k):\n    _C[k] = 1\n",
        "_C = dict()\ndef f(k):\n    _C.update(k=1)\n",
        "import decimal\nCTX = decimal.Context()\ndef f(v):\n    CTX.flags = {}\n",  # its attributes are written
        "def outer():\n    pass\n_C = []\ndef f():\n    def g(v):\n        _C.append(v)\n    return g\n",
    ],
)
def test_planted_engine_caches_are_found(source: str) -> None:
    assert engine_cache_findings(source, "ledger_engine.planted") != [], source


@pytest.mark.parametrize(
    "source",
    [
        "TABLE = {'a': 1}\ndef f(k):\n    return TABLE[k]\n",
        "def f():\n    rows = []\n    rows.append(1)\n    return rows\n",
        "ROWS = []\ndef f(ROWS):\n    ROWS.append(1)\n",  # a parameter of the same name is the call's own
        "ROWS = []\ndef f():\n    ROWS = []\n    ROWS.append(1)\n",  # a local of the same name
        "class C:\n    def __init__(self):\n        self.rows = {}\n    def add(self, k):\n        self.rows[k] = 1\n",
        "BY = {}\nBY['a'] = 1\n",  # built at import, then never written
        "import decimal\nEXACT = decimal.Context(prec=10)\ndef f(a, b):\n    return EXACT.add(a, b)\n",  # arithmetic
        "def f():\n    seen = []\n    def g(v):\n        seen.append(v)\n    return g\n",  # a call's own closure
        "import sys\ndef f(p):\n    sys.path[0:0] = p\n",  # an imported module's state, not this module's store
    ],
)
def test_engine_tables_and_locals_pass(source: str) -> None:
    assert engine_cache_findings(source, "ledger_engine.planted") == [], source


# -- 3. A bound and an eviction that say something --------------------------------------------------------------------

NO_BOUND = re.compile(
    r"\b(unbounded|unlimited|no (?:bound|limit|cap))\b|^\s*(none|n/?a|tbd|todo|-+|\?+)\s*\.?\s*$", re.I
)
NO_EVICTION = re.compile(r"^\s*(none|never|n/?a|tbd|todo|-+|\?+)\s*\.?\s*$", re.I)


def bound_problems(registry: tuple[StateEntry, ...]) -> list[str]:
    out: list[str] = []
    for e in registry:
        if NO_BOUND.search(e.bound):
            out.append(f"{e.module}.{e.name}: its bound says there is none ({e.bound!r})")
        if NO_EVICTION.search(e.eviction):
            out.append(f"{e.module}.{e.name}: its eviction says it never happens ({e.eviction!r})")
    return out


def test_every_entry_declares_a_real_bound_and_eviction() -> None:
    """@regression-guard (it holds on 321be18d; the planted cases below are its must-not-pass tests)."""
    assert bound_problems(REGISTRY) == []


@pytest.mark.parametrize(
    ("bound", "eviction"),
    [
        ("unbounded", "LRU"),
        ("grows unbounded with the ledger", "LRU"),
        ("no limit", "LRU"),
        ("None", "LRU"),
        ("n/a", "LRU"),
        ("TBD", "LRU"),
        ("-", "LRU"),
        ("200 rows", "never"),
        ("200 rows", "none"),
        ("200 rows", "TODO"),
    ],
)
def test_a_boundless_or_unevictable_entry_is_refused(bound: str, eviction: str) -> None:
    entry = StateEntry("app.m", "C", "UserContainer", bound, eviction)
    assert bound_problems((entry,)) != [], (bound, eviction)


def test_a_stated_bound_passes() -> None:
    entry = StateEntry("app.m", "C", "UserContainer", "one file; no buffered lines; 200 rows", "LRU, then TTL 1 h")
    assert bound_problems((entry,)) == []
