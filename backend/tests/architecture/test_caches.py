"""INV-10: every in-memory cache is per user, evictable and bounded, declared in the state registry (BL-0010, S0093).

`app.platform.state_registry` is the one registry of INV-10 and INV-11 (ARCHITECTURE.md §2.5): each entry names the
class a container holds, its bound and its eviction. The globals test (`test_globals.py`, INV-11) refuses state at
module and class level in `app`. This file adds what that scan cannot see:

1. **The long-lived graph, at run time.** Everything the composition root builds and keeps for the process's life
   (`app.main.wire` and the log) is walked through attributes, slots, dataclass fields, closures, a function's
   attributes, bound methods, partials and the class-level values of each object's classes. Default-deny: only an
   immutable value (a constant, a tuple, a frozen dataclass), a function, an object whose type has no storage of its
   own beyond `object`'s header (no `__dict__`, no slot, no C fields, read from the type's layout) and the C types
   allowed by name (`STATELESS_C`: locks) are entered; anything else (a dict, list, deque, array, queue, an
   `lru_cache` wrapper, a subclass of a container, an object with an attribute or slot that can be rebound) must sit
   inside an instance of a registered class, whose entry declares how big it may grow and how it shrinks; the walk
   does not enter a registered holder. So a cache added to any long-lived object, at construction or later (a slot
   or attribute filled on first use), is refused until it is registered. Every entry must be reached from the
   graph: an entry nothing holds is stale.
2. **The engine's source.** The engine serves job after job (engine-jobs §5.2), so a module-level or class-level
   store there outlives every job. The globals scan covers only `app`; here the engine's tree is scanned for the
   cache forms: a memoizing decorator or call, `global`/`nonlocal`, `globals()`/`vars()`/`locals()` and
   `sys.modules`, and a write inside a function into a long-lived store. A long-lived store is a module-level name,
   a class-level name (written through the class, `cls` or `self`; a dataclass `field(...)` default is per
   instance), a default argument (bound to a store or a container built in place), or a local bound to one of these
   by a plain assignment. A write is an item or attribute assigned or deleted, a class attribute rebound, a mutating
   method (in-place dunders included; on the store itself only when it is bound to a container, so
   `decimal.Context.add` is arithmetic), `heapq`/`bisect` on it, or `setattr`/`delattr` on it. The engine's
   module-level tables are built at import and never written afterwards, which this holds. The one long-lived
   object of the engine, `Engine`, is checked at run time by `engine/tests/architecture/test_no_resident_ledger.py`
   (it holds after the jobs exactly what it held before).
3. **A bound that says there is none**, or an eviction that says it never happens, is refused (INV-10: "evictable
   and has a bound"), the words anywhere in the text.

Stated limits. The walk: a registered holder's insides are its entry's word (the review checks the entry against
the code); a frozen field rebound with `object.__setattr__` is not seen; library objects built by the web framework
(FastAPI's routes and middleware, fixed by code) are not walked. The engine scan: a write into another module's
object through its import (`other.TABLE[k] = v`, `setattr(other, ...)`); a method that mutates a store bound to a
call the scan does not know as a container (`T = make()`, then `T.push(...)`; its items and attributes are still
seen); an alias made other than by a plain assignment or a default (tuple unpacking, `getattr`, a store passed as an
argument to a function that writes it, a nested function writing through an enclosing method's `self`). Scope:
INV-10 speaks of in-memory caches, so the caches on disk (the mirror's generations, the engine's bytecode, the log
files) are outside it (S0093 journal); that each bound is a configuration value sized per deployment is BL-0229.
Each rule sits beside the planted case that must not pass (EP-29).
"""

from __future__ import annotations

import array
import ast
import collections
import dataclasses
import functools
import itertools
import queue
import re
import threading
import types
import weakref
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


# C types that hold no data of their own, allowed by name (default-deny: any other object of a C type is refused).
# A lock holds one flag and its owner, never a store.
STATELESS_C: tuple[type, ...] = (type(threading.Lock()), type(threading.RLock()))
# Container bases: an instance of one (or of a subclass, slotted or frozen) is a store whatever else it is.
STORES: tuple[type, ...] = (dict, list, set, bytearray, collections.deque, array.array, queue.SimpleQueue)
# Class-level values that are code, not data; a staticmethod or classmethod is walked through its function.
CODE: tuple[type, ...] = (property, types.MemberDescriptorType, types.GetSetDescriptorType)


def holds_nothing(obj: object) -> bool:
    """An instance with no storage of its own beyond `object`'s header: no `__dict__`, no slot, no C fields. Read from
    the type's layout (`__basicsize__`), so a C type (a deque, an array, a queue) is never taken for empty."""
    t = type(obj)
    return t.__basicsize__ == object.__basicsize__ and t.__dictoffset__ == 0 and not slot_names(obj)


def class_values(obj: object) -> list[tuple[str, object]]:
    """The class-level values of `obj`'s classes (state reached through `type(obj)`, not through the instance)."""
    out: list[tuple[str, object]] = []
    for cls in type(obj).__mro__:
        if cls is object or not hasattr(cls, "__dict__"):
            continue
        for name, value in vars(cls).items():
            if name.startswith("__") and name.endswith("__"):
                continue  # the class machinery (its dict, slots, annotations, dataclass fields)
            if isinstance(value, staticmethod | classmethod):
                value = value.__func__  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(value, CODE):
                continue
            out.append((f"<class {cls.__qualname__}.{name}>", value))  # pyright: ignore[reportUnknownArgumentType]
    return out


def children(obj: object) -> Iterator[tuple[str, object]] | None:
    """What `obj` holds, if it can hold nothing that grows by itself; None when `obj` itself can change.
    Default-deny: only the shapes below are entered; everything else is refused."""
    if isinstance(obj, STORES):
        return None
    if isinstance(obj, tuple | frozenset):
        return ((f"[{i}]", v) for i, v in enumerate(obj))  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if isinstance(obj, types.FunctionType):
        cells = obj.__closure__ or ()
        held = [(f"<closure {n}>", c.cell_contents) for n, c in zip(obj.__code__.co_freevars, cells, strict=True)]
        held += [(f"<default {i}>", v) for i, v in enumerate(obj.__defaults__ or ())]
        held += [(f"<default {k}>", v) for k, v in (obj.__kwdefaults__ or {}).items()]
        held += [(f"<attribute {k}>", v) for k, v in vars(obj).items()]  # `f.memo = {}` on a kept function
        return iter(held)  # never `__globals__`: module state is the globals test's
    if isinstance(obj, types.MethodType):
        return iter([("__self__", obj.__self__), ("__func__", obj.__func__)])
    if isinstance(obj, types.BuiltinFunctionType | types.BuiltinMethodType):
        owner = obj.__self__
        return iter([] if owner is None or isinstance(owner, types.ModuleType) else [("__self__", owner)])
    if isinstance(obj, functools.partial):
        p: functools.partial[object] = obj  # pyright: ignore[reportUnknownVariableType]
        return iter([("func", p.func), *((f"args[{i}]", a) for i, a in enumerate(p.args)), *p.keywords.items()])
    if isinstance(obj, STATELESS_C):
        return iter([])
    if frozen_dataclass(obj):
        named = [(f.name, getattr(obj, f.name)) for f in dataclasses.fields(obj)]  # pyright: ignore[reportArgumentType]
        extra = [(k, v) for k, v in getattr(obj, "__dict__", {}).items() if k not in dict(named)]
        return iter([*named, *extra, *slots_of(obj), *class_values(obj)])
    if holds_nothing(obj):
        return iter(class_values(obj))  # it holds nothing, now or later; its class's values are walked
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


@dataclass(frozen=True)
class FrozenDict(dict[str, int]):
    """A frozen dataclass that is also a dict: its fields are fixed, its items are not."""

    part: int = 0


class SlottedDict(dict[str, int]):
    __slots__ = ()


class StatelessWithClassStore:
    __slots__ = ()
    memo: dict[str, int] = {}  # noqa: RUF012 (the planted class-level store)


class StatelessWithStaticMemo:
    __slots__ = ()

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def f(x: int) -> int:
        return x


@functools.lru_cache(maxsize=128)
def memoized(x: int) -> int:
    return x


def function_with_memo() -> object:
    def f() -> None: ...

    f.memo = {}  # type: ignore[attr-defined]
    return f


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
        # S0093 round 1, F1 and F3: C containers (no `__dict__`, no slot) and state reached through a class or a
        # function's attributes
        (Frozen(collections.deque([1])), "root.part: collections.deque"),
        (Frozen(collections.defaultdict[str, list[int]](list)), "root.part: collections.defaultdict"),
        (Frozen(collections.OrderedDict[str, int]()), "root.part: collections.OrderedDict"),
        (Frozen(array.array("i", [1])), "root.part: array.array"),
        (Frozen(queue.SimpleQueue[int]()), "root.part: _queue.SimpleQueue"),
        (Frozen(weakref.WeakValueDictionary[str, Held]()), "root.part: weakref.WeakValueDictionary"),
        (Frozen(SlottedDict()), f"root.part: {__name__}.SlottedDict"),
        (Frozen(FrozenDict()), f"root.part: {__name__}.FrozenDict"),
        (Frozen(itertools.count()), "root.part: itertools.count"),  # C state with no `__dict__` and no slot
        (Frozen(memoized), "root.part: functools._lru_cache_wrapper"),
        (Frozen(function_with_memo()), "root.part.<attribute memo>: builtins.dict"),
        (Frozen(StatelessWithClassStore()), "root.part.<class StatelessWithClassStore.memo>: builtins.dict"),
        (
            Frozen(StatelessWithStaticMemo()),
            "root.part.<class StatelessWithStaticMemo.f>: functools._lru_cache_wrapper",
        ),
    ],
)
def test_a_store_outside_a_registered_holder_is_refused(root: object, where: str) -> None:
    problems = graph_problems({"root": root, "held": Held()}, (HELD,))
    assert any(p.startswith(where) for p in problems), problems


def test_a_registered_holder_is_not_entered_and_immutable_parts_pass() -> None:
    root = Frozen((Held(), Frozen(Frozen(("a", 1, Decimal(2), Path("/x"), None))), Stateless(), threading.Lock()))
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
        "__ior__",
        "__iadd__",
        "__iand__",
        "__isub__",
        "__ixor__",
        "__imul__",
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


# Standard-library functions that write into their first argument (a heap or a sorted list kept as a module store).
WRITES_FIRST_ARG = frozenset(
    {
        "heapq.heappush",
        "heapq.heappop",
        "heapq.heapify",
        "heapq.heappushpop",
        "heapq.heapreplace",
        "bisect.insort",
        "bisect.insort_left",
        "bisect.insort_right",
        "setattr",
        "delattr",
    }
)
# Ways to reach a module's own namespace without naming it: refused anywhere in the engine (it uses none).
NAMESPACE_CALLS = frozenset({"globals", "vars", "locals"})
CLASS_FIELD_CALLS = frozenset({"field", "dataclasses.field"})  # a class-level default made per instance


@dataclass(frozen=True)
class Klass:
    """A class of the module: its name and its class-level names with their values (per-instance defaults left out)."""

    name: str
    names: dict[str, ast.expr]


def class_names(cls: ast.ClassDef) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for s in cls.body:
        if isinstance(s, ast.Assign | ast.AnnAssign) and s.value is not None:
            if isinstance(s.value, ast.Call) and dotted(s.value.func) in CLASS_FIELD_CALLS:
                continue
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            out |= {t.id: s.value for t in targets if isinstance(t, ast.Name)}
    return out


def is_display(value: ast.expr | None) -> bool:
    """A container built in place: a display, a comprehension or a container call (a mutable default argument)."""
    displays = ast.Dict | ast.List | ast.Set | ast.DictComp | ast.ListComp | ast.SetComp
    return isinstance(value, displays) or (isinstance(value, ast.Call) and container_like(value))


@dataclass
class Scope:
    """One function as the scan sees it: the module's stores, its class (for a method), the names that are its own,
    and its aliases of a store (a default argument or a local bound to one), each to the store's key."""

    module: str
    shared: dict[str, ast.expr | None]
    klass: Klass | None
    selves: frozenset[str]
    local: set[str]
    aliases: dict[str, str]
    values: dict[str, ast.expr | None]  # store key -> what it is bound to

    def store(self, node: ast.expr) -> str | None:
        """The key of the long-lived store `node` is in, or None for the call's own objects. `node` is the object
        written into (the receiver of a method, the container of an item, the object of an attribute)."""
        chain: list[ast.Attribute | ast.Subscript] = []
        while isinstance(node, ast.Attribute | ast.Subscript):
            chain.append(node)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        base = node.id
        if base in self.aliases:
            return self.aliases[base]
        if self.klass is not None and base in self.selves | {self.klass.name}:
            first = chain[-1] if chain else None
            if isinstance(first, ast.Attribute) and first.attr in self.klass.names:
                return self.klass_key(first.attr)
            return None
        if base in self.shared and base not in self.local:
            return base
        return None

    def klass_key(self, attr: str) -> str:
        assert self.klass is not None
        key = f"{self.klass.name}.{attr}"
        self.values[key] = self.klass.names[attr]
        return key

    def bare(self, node: ast.expr) -> bool:
        """`node` names a store itself (`_C`, `self.memo`), not an item or attribute inside one."""
        if isinstance(node, ast.Name):
            return True
        return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in self.selves


def scope_of(
    fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    module: str,
    shared: dict[str, ast.expr | None],
    klass: Klass | None,
) -> Scope:
    args = fn.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    selves: frozenset[str] = frozenset()
    if klass is not None and params and not isinstance(fn, ast.Lambda):
        static = any(dotted(d) == "staticmethod" for d in fn.decorator_list)
        selves = frozenset() if static else frozenset({params[0].arg, "cls"})
    sc = Scope(module, shared, klass, selves, set(), {}, dict(shared))
    sc.local = {a.arg for a in params} | {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    positional = [*args.posonlyargs, *args.args]
    defaults = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True))
    defaults += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is not None]
    body = fn.body if isinstance(fn.body, list) else [ast.Expr(fn.body)]
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                sc.local.add(sub.id)
    for param, default in defaults:  # a default is made once, at the definition: it outlives every call
        if is_display(default):
            key = f"{getattr(fn, 'name', '<lambda>')}.<default {param.arg}>"
            sc.aliases[param.arg], sc.values[key] = key, default
        elif (key := sc.store(default)) is not None:
            sc.aliases[param.arg] = key
    for stmt in body:  # a local bound to a store is the store (`c = _C`, `memo = self.memo`)
        for sub in ast.walk(stmt):
            if (
                isinstance(sub, ast.Assign)
                and len(sub.targets) == 1
                and isinstance(sub.targets[0], ast.Name)
                and (key := sc.store(sub.value)) is not None
            ):
                sc.aliases[sub.targets[0].id] = key
    return sc


def function_writes(
    fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, sc: Scope, aliases: dict[str, str]
) -> list[str]:
    """Every write this function makes into a long-lived store, as findings."""
    out: list[str] = []
    body = fn.body if isinstance(fn.body, list) else [ast.Expr(fn.body)]

    def hit(key: str | None, line: int, how: str) -> None:
        if key is not None:
            out.append(f"{sc.module}.{key}: {how} inside a function at line {line}")

    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.Delete):
                targets = sub.targets if isinstance(sub, ast.Assign | ast.Delete) else [sub.target]
                for t in targets:
                    if isinstance(t, ast.Subscript | ast.Attribute):
                        hit(sc.store(t.value), sub.lineno, "a long-lived store written")
                    if (  # `K.memo = v`, `cls.memo = v`: the class's own attribute rebound
                        isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and sc.klass is not None
                        and t.value.id in {sc.klass.name, "cls"}
                        and t.attr in sc.klass.names
                    ):
                        hit(sc.klass_key(t.attr), sub.lineno, "a class attribute rebound")
            elif isinstance(sub, ast.Call):
                func = dotted(sub.func)
                head, _, rest = func.partition(".")
                full = f"{aliases.get(head, head)}.{rest}" if rest else aliases.get(head, head)
                if func in NAMESPACE_CALLS:
                    out.append(f"{sc.module}: {func}() at line {sub.lineno} (a module's own namespace)")
                elif full in WRITES_FIRST_ARG and sub.args:
                    hit(sc.store(sub.args[0]), sub.lineno, f"a long-lived store written by {full}")
                elif isinstance(sub.func, ast.Attribute) and sub.func.attr in MUTATORS:
                    recv = sub.func.value
                    key = sc.store(recv)
                    if key is not None and (not sc.bare(recv) or container_like(sc.values.get(key))):
                        hit(key, sub.lineno, f"a long-lived store written by .{sub.func.attr}()")
    return out


def engine_cache_findings(source: str, module: str) -> list[str]:
    """The cache forms in one engine module: memoizers, `global`/`nonlocal`, `globals()`/`vars()`/`sys.modules`, and
    a write inside a function into a long-lived store: a module-level name, a class-level name (through the class,
    `cls` or `self`), a default argument, or a local alias of one of these. A function's own local of the same name
    is not the module's (a parameter or a local assignment)."""
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
        if isinstance(node, ast.Attribute) and dotted(node) == "sys.modules":
            found.append(f"{module}: sys.modules at line {node.lineno} (a module's namespace by name)")
    shared = module_names(tree)
    owner: dict[int, Klass] = {}
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            k = Klass(cls.name, class_names(cls))
            owner |= {id(f): k for f in cls.body if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            sc = scope_of(fn, module, shared, owner.get(id(fn)))
            found += function_writes(fn, sc, aliases)
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
        # S0093 round 1, F2: class-level stores, defaults, aliases, heapq/bisect, namespaces, in-place dunders
        "_C = {}\ndef f(k, v, c=_C):\n    c[k] = v\n",
        "def f(k, v, c={}):\n    c[k] = v\n",  # a mutable default lives as long as the function
        "def f(k, *, c=dict()):\n    c.update(k=1)\n",
        "_C = {}\ndef f(k, v):\n    c = _C\n    c[k] = v\n",
        "class K:\n    memo = {}\n    def f(self, k, v):\n        K.memo[k] = v\n",
        "class K:\n    memo = {}\n    def f(self, k, v):\n        self.memo[k] = v\n",
        "class K:\n    memo = []\n    @classmethod\n    def f(cls, v):\n        cls.memo.append(v)\n",
        "class K:\n    memo = {}\n    def f(self, k):\n        m = self.memo\n        m[k] = 1\n",
        "class K:\n    last = None\n    def f(self, v):\n        K.last = v\n",
        "import heapq\n_H = []\ndef f(v):\n    heapq.heappush(_H, v)\n",
        "from heapq import heappush\n_H = []\ndef f(v):\n    heappush(_H, v)\n",
        "import bisect\n_L = []\ndef f(v):\n    bisect.insort(_L, v)\n",
        "def f(v):\n    globals()['_LAST'] = v\n",
        "def f(v):\n    vars()['x'] = v\n",
        "import sys\ndef f(v):\n    setattr(sys.modules[__name__], '_LAST', v)\n",
        "import types\nNS = types.SimpleNamespace()\ndef f(v):\n    setattr(NS, 'last', v)\n",
        "_C = {}\ndef f(d):\n    _C.__ior__(d)\n",
        "def g(f):\n    from functools import cache\n    return cache(f)\n",
        "import functools\nclass K:\n    @functools.lru_cache(maxsize=None)\n    def f(self, x):\n        return x\n",
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
        "class K:\n    def __init__(self):\n        self.memo = {}\n    def f(self, k):\n        self.memo[k] = 1\n",
        "from dataclasses import dataclass, field\n@dataclass\nclass K:\n    rows: list = field(default_factory=list)\n"
        "    def f(self, v):\n        self.rows.append(v)\n",  # a per-instance default
        "class K:\n    LIMIT = 3\n    def f(self):\n        self.LIMIT = 4\n",  # an instance attribute shadows it
        "import decimal\nclass K:\n    CTX = decimal.Context()\n"
        "    def f(self, a, b):\n        return self.CTX.add(a, b)\n",
        "_C = {}\ndef f(k):\n    c = _C\n    return c[k]\n",  # an alias only read
        "def f(k, c=None):\n    c = {}\n    c[k] = 1\n",
        "import types\nmod = types.ModuleType('m')\ndef f(o, v):\n    setattr(o, 'x', v)\n",  # a parameter's object
    ],
)
def test_engine_tables_and_locals_pass(source: str) -> None:
    assert engine_cache_findings(source, "ledger_engine.planted") == [], source


# -- 3. A bound and an eviction that say something --------------------------------------------------------------------

NO_BOUND = re.compile(
    r"\b(unbounded|unlimited|infinite|without (?:a )?(?:bound|limit|cap|maximum)|no (?:bound|limit|cap|maximum))\b"
    r"|^\s*(none|n/?a|tbd|todo|-+|\?+)\s*\.?\s*$",
    re.I,
)
# Anywhere in the text (S0093 round 1, F4): an eviction that is "never" somewhere is not an eviction.
NO_EVICTION = re.compile(
    r"\b(never|forever|no eviction|not evicted|kept for ever)\b|^\s*(none|n/?a|tbd|todo|-+|\?+)\s*\.?\s*$", re.I
)


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
        ("infinite", "LRU"),
        ("rows without limit", "LRU"),
        ("no maximum", "LRU"),
        ("200 rows", "LRU, but the oldest is never dropped"),
        ("200 rows", "kept forever"),
        ("200 rows", "no eviction"),
        ("200 rows", "rows are not evicted"),
    ],
)
def test_a_boundless_or_unevictable_entry_is_refused(bound: str, eviction: str) -> None:
    entry = StateEntry("app.m", "C", "UserContainer", bound, eviction)
    assert bound_problems((entry,)) != [], (bound, eviction)


def test_a_stated_bound_passes() -> None:
    entry = StateEntry("app.m", "C", "UserContainer", "one file; no buffered lines; 200 rows", "LRU, then TTL 1 h")
    assert bound_problems((entry,)) == []
