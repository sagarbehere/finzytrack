"""What the main sidecar's event loop runs: the call walk behind EP-25's check (S0103; L1-02).

The roots are every `async def` of the package and the reviewed sync callbacks the loop thread runs (`ROOTS`). From a
root the walk visits every call its body makes (not the bodies of nested functions, lambdas or classes, which run
when called; a nested `async def` is a root of its own):

- an awaited call (`await f(...)`) passes; when `f` is a sync function of ours its body runs on the loop before it
  returns the awaitable, so it is walked too;
- a call that resolves to a function of ours (a module-level or nested function, a method through `self`/`cls`, a
  name imported from the package, re-exports followed) is walked in turn; a class of ours is walked through its
  `__init__`, `__post_init__` and `__new__` when it defines them (a generated dataclass or pydantic `__init__` only
  builds a value in memory);
- a call of a pure builtin (`PURE_BUILTINS`) passes;
- anything else is a **site** `(function, callee text)` that must be reviewed (`test_async_edge.REVIEWED`), with
  its reason. Default-deny: an unreviewed site fails, and so does a reviewed site the walk no longer meets.

A `with` whose context is not a call (`with lock:`) is a site too (`with <text>`): entering it may wait.

Stated limits: a property or descriptor runs code without a call node; iterating an object (`for x in it`) calls
its `__next__`; operators call dunders; a callable passed in and called (`handler(request)`) is a site by its text,
so its body is the reason's word. Library code (FastAPI, Starlette, uvicorn, pydantic) is not walked: it is the
framework that runs the loop.
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "app"

PURE_BUILTINS = frozenset(
    {
        "abs", "all", "any", "bool", "bytes", "callable", "dict", "divmod", "enumerate", "filter", "float",
        "frozenset", "getattr", "hasattr", "hash", "id", "int", "isinstance", "issubclass", "iter", "len", "list",
        "map", "max", "min", "next", "object", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "super", "tuple", "type", "zip",
    }
)  # fmt: skip
"""Builtins that compute in memory. `open`, `input`, `print`, `exec`, `eval`, `compile`, `__import__`, `breakpoint`
and `help` are not here: each would be a site."""

INIT_METHODS = ("__init__", "__post_init__", "__new__")


@dataclass(frozen=True, slots=True)
class Fn:
    module: str
    qualname: str  # `f`, `C.m`, `outer.inner`, `C.m.inner`

    @property
    def key(self) -> str:
        return f"{self.module}:{self.qualname}"


@dataclass(slots=True)
class Module:
    name: str
    tree: ast.Module
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(
        default_factory=dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    )
    classes: dict[str, ast.ClassDef] = field(default_factory=dict[str, ast.ClassDef])
    imports: dict[str, str] = field(default_factory=dict[str, str])  # local name -> dotted target
    is_package: bool = False


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _index_defs(mod: Module, body: list[ast.stmt], prefix: str) -> None:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            qual = f"{prefix}{node.name}"
            mod.functions[qual] = node
            _index_defs(mod, node.body, qual + ".")
        elif isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}"
            mod.classes[qual] = node
            _index_defs(mod, node.body, qual + ".")
        elif isinstance(node, ast.If | ast.Try | ast.With):
            for part in (getattr(node, "body", []), getattr(node, "orelse", []), getattr(node, "finalbody", [])):
                _index_defs(mod, part, prefix)


def _imports(tree: ast.Module, name: str, is_package: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = name.split(".") if is_package else name.split(".")[:-1]
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            for a in node.names:
                out[a.asname or a.name] = f"{base}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    out[top] = top
    return out


def index(root: Path = SRC, package: str = PACKAGE) -> dict[str, Module]:
    mods: dict[str, Module] = {}
    for path in sorted((root / package).rglob("*.py")):
        name = module_name(path, root)
        tree = ast.parse(path.read_text(), filename=str(path))
        is_package = path.name == "__init__.py"
        mod = Module(name, tree, imports=_imports(tree, name, is_package), is_package=is_package)
        _index_defs(mod, tree.body, "")
        mods[name] = mod
    return mods


@dataclass(frozen=True, slots=True)
class Site:
    fn: str  # Fn.key
    callee: str  # the callee's source text, or `with <text>`
    line: int


@dataclass(slots=True)
class Walk:
    reached: set[Fn] = field(default_factory=set[Fn])
    sites: list[Site] = field(default_factory=list[Site])
    roots: set[Fn] = field(default_factory=set[Fn])
    awaited: int = 0
    builtin_calls: int = 0
    calls_of_ours: int = 0


class _Resolver:
    def __init__(self, mods: dict[str, Module]) -> None:
        self.mods = mods

    def target(self, dotted: str, depth: int = 0) -> Fn | tuple[str, str] | None:
        """A dotted import target -> a function of ours, a class of ours (module, qualname), or None."""
        if depth > 10 or not dotted.startswith(PACKAGE + "."):
            return None
        if dotted in self.mods:
            return None  # a module, not a callable
        mod_name, _, attr = dotted.rpartition(".")
        mod = self.mods.get(mod_name)
        if mod is None:
            return None
        if attr in mod.functions:
            return Fn(mod_name, attr)
        if attr in mod.classes:
            return (mod_name, attr)
        if attr in mod.imports:
            return self.target(mod.imports[attr], depth + 1)
        return None

    def call(self, mod: Module, scope: str, cls: str | None, func: ast.expr) -> Fn | tuple[str, str] | None:
        if isinstance(func, ast.Name):
            parts = scope.split(".") if scope else []
            for i in range(len(parts), -1, -1):  # nested functions of the enclosing scopes, then the module
                qual = ".".join([*parts[:i], func.id])
                if qual in mod.functions:
                    return Fn(mod.name, qual)
                if qual in mod.classes:
                    return (mod.name, qual)
            if func.id in mod.imports:
                return self.target(mod.imports[func.id])
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = func.value.id
            if base in ("self", "cls") and cls is not None:
                qual = f"{cls}.{func.attr}"
                if qual in mod.functions:
                    return Fn(mod.name, qual)
                return None
            if base in mod.imports:
                return self.target(f"{mod.imports[base]}.{func.attr}")
        return None


def _own_class(mod: Module, qualname: str) -> str | None:
    """The innermost class whose method (or a function nested in it) this is."""
    parts = qualname.split(".")
    for i in range(len(parts) - 1, 0, -1):
        cand = ".".join(parts[:i])
        if cand in mod.classes:
            return cand
    return None


def _calls(body: list[ast.stmt]) -> Iterator[tuple[ast.Call | ast.withitem, bool]]:
    """Every call in `body` (and `with` items that are not calls), with whether it is awaited; nested function,
    lambda and class bodies are skipped (their decorators and defaults run here and are kept)."""
    stack: list[ast.AST] = list(reversed(body))
    awaited: set[int] = set()
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.extend(node.decorator_list)
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.Lambda):
            stack.extend(node.args.defaults)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.decorator_list)
            stack.extend(node.bases)
            continue
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))
        if isinstance(node, ast.With):
            for item in node.items:
                if not isinstance(item.context_expr, ast.Call):
                    yield item, False
        if isinstance(node, ast.Call):
            yield node, id(node) in awaited
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _pure_builtin(mod: Module, func: ast.expr) -> bool:
    """A pure builtin, or a builtin exception class (building one computes in memory), not shadowed by an import."""
    if not isinstance(func, ast.Name) or func.id in mod.imports or func.id in mod.functions or func.id in mod.classes:
        return False
    value: object = getattr(builtins, func.id, None)
    return func.id in PURE_BUILTINS or (isinstance(value, type) and issubclass(value, BaseException))


def _str_method(func: ast.expr) -> bool:
    """A method of a text literal (`", ".join(...)`): computes in memory."""
    return (
        isinstance(func, ast.Attribute) and isinstance(func.value, ast.Constant) and isinstance(func.value.value, str)
    )


def walk(mods: dict[str, Module], extra_roots: tuple[str, ...] = ()) -> Walk:
    res = _Resolver(mods)
    out = Walk()
    todo: list[Fn] = []
    for mod in mods.values():
        for qual, node in mod.functions.items():
            if isinstance(node, ast.AsyncFunctionDef):
                todo.append(Fn(mod.name, qual))
    for key in extra_roots:
        m, _, q = key.partition(":")
        todo.append(Fn(m, q))
    out.roots = set(todo)
    while todo:
        fn = todo.pop()
        if fn in out.reached:
            continue
        out.reached.add(fn)
        mod = mods[fn.module]
        node = mod.functions[fn.qualname]
        cls = _own_class(mod, fn.qualname)
        for call, is_awaited in _calls(node.body):
            if isinstance(call, ast.withitem):
                out.sites.append(Site(fn.key, f"with {ast.unparse(call.context_expr)}", call.context_expr.lineno))
                continue
            target = res.call(mod, fn.qualname, cls, call.func)
            if isinstance(target, Fn):
                out.calls_of_ours += 1
                todo.append(target)
                continue
            if isinstance(target, tuple):
                out.calls_of_ours += 1
                cmod, cqual = target
                todo.extend(Fn(cmod, f"{cqual}.{m}") for m in INIT_METHODS if f"{cqual}.{m}" in mods[cmod].functions)
                continue
            if is_awaited:
                out.awaited += 1
                continue
            if _pure_builtin(mod, call.func) or _str_method(call.func):
                out.builtin_calls += 1
                continue
            out.sites.append(Site(fn.key, ast.unparse(call.func), call.lineno))
    return out
