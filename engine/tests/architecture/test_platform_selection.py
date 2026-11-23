"""One selection point for the file-system adapters (S0113; `ledger_engine.fs.this_os`).

An adapter module states its platform once, at its top, and refuses to be imported elsewhere; so the engine starts on
any OS only if nothing but the selection point imports an adapter. Checked from the source (AST) over every module of
the engine's `src/`: an import of `ledger_engine.fs.darwin` in any spelling (`import ledger_engine.fs.darwin`,
`from ledger_engine.fs.darwin import ...`, `from ledger_engine.fs import darwin`, a relative import) is allowed only
in `fs/this_os.py`. Stated limits: a dynamic import by name (`importlib`) is out of an AST scan; the engine boundary
check already allows `import_module` only in `ledger/beancount_port.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
# Every module of `fs/` except the port, the selection point, the package itself, the code the POSIX adapters share
# (`posix`, S0125: imported only by the adapters, below) and the pure path model of every OS (`pathmodel`, S0127) is an
# adapter (S0113 verifier V1: `fs/linux.py` or `fs/windows.py` is covered without editing this test).
NOT_ADAPTERS = frozenset({"__init__", "port", "this_os", "posix", "pathmodel"})
SHARED = "ledger_engine.fs.posix"
SELECTION = "fs/this_os.py"
ADAPTERS = frozenset(f"ledger_engine.fs.{p.stem}" for p in (SRC / "fs").glob("*.py") if p.stem not in NOT_ADAPTERS)


def test_the_adapter_set_is_read_from_the_fs_package() -> None:
    assert {"ledger_engine.fs.darwin", "ledger_engine.fs.linux"} <= ADAPTERS
    assert not ADAPTERS & {"ledger_engine.fs.port", "ledger_engine.fs.this_os", "ledger_engine.fs.__init__", SHARED}
    assert "ledger_engine.fs.pathmodel" not in ADAPTERS


def adapter_imports(rel: str, source: str) -> list[str]:
    """The adapter modules `source` (the module at `rel` under `ledger_engine/`) imports, in any spelling."""
    package = ("ledger_engine", *Path(rel).parent.parts)
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if any(a.name == m or a.name.startswith(m + ".") for m in ADAPTERS))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join((*base, *([node.module] if node.module else [])))
            else:
                module = node.module or ""
            if module in ADAPTERS:
                found.append(module)
            else:
                found.extend(f"{module}.{a.name}" for a in node.names if f"{module}.{a.name}" in ADAPTERS)
    return found


def test_only_the_selection_point_imports_an_adapter() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == SELECTION:
            continue
        offenders.extend(f"{rel}: {m}" for m in adapter_imports(rel, path.read_text(encoding="utf-8")))
    assert not offenders, (
        "import the real port from ledger_engine.fs.this_os, never from an adapter module:\n" + "\n".join(offenders)
    )


def test_the_selection_point_is_the_one_that_imports_the_adapters() -> None:
    """`@regression-guard`: the scan sees the selection point's own imports, so it would see any other."""
    found = adapter_imports(SELECTION, (SRC / SELECTION).read_text(encoding="utf-8"))
    assert sorted(set(found)) == ["ledger_engine.fs.darwin", "ledger_engine.fs.linux", "ledger_engine.fs.windows"]


ENGINE_LOCK_CALLERS = frozenset({"protocol/server.py", SELECTION})
"""Where `this_os.engine_lock_folder` and `this_os.take_engine_lock` may be named (S0128 round 1, verifier F1)."""
TESTS = SRC.parents[1] / "tests"


def test_the_windows_test_switch_is_gone() -> None:
    """S0150 (BL-0286; owner, 2026-11-21: "option 1"): turning Windows writes on removes the test switch that selected
    the Windows adapter for a test (`fs/under_test.py`), its `windows_port` fixture and every module's opt-in, so the
    product and its tests select the adapter one way, by the OS and the pinned placeholder mode. Must not pass: any of
    them left."""
    assert not (SRC / "fs" / "under_test.py").exists()
    assert "under_test" not in (SRC / SELECTION).read_text(encoding="utf-8")
    left = [
        p.relative_to(TESTS).as_posix()
        for p in sorted(TESTS.rglob("*.py"))
        if p.name != "test_platform_selection.py"
        and any(n in p.read_text(encoding="utf-8") for n in ("windows_port", "under_test.", "fs import under_test"))
    ]
    assert not left, left


def test_only_the_server_takes_the_engine_lock_through_the_selection_point() -> None:
    """Must not pass (@regression-guard: the server was the only caller at the base): the Windows engine lock is taken
    in any placeholder mode (a guard, written only in the app folder's `locks/`; S0128, S0149), so only
    `protocol/server.py` names it."""
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).as_posix() not in ENGINE_LOCK_CALLERS
        and any(
            isinstance(n, ast.Attribute | ast.Name | ast.alias)
            and getattr(n, "attr", getattr(n, "id", getattr(n, "name", None)))
            in ("engine_lock_folder", "take_engine_lock")
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert not offenders, offenders


PATHMODEL = "ledger_engine.fs.pathmodel"
PATHMODEL_FILE = "fs/pathmodel.py"
PATHMODEL_ALLOWED = frozenset({"host", "HOST", "POSIX", "FoldRule", "MalformedPathError", "fold", "PathModel"})
"""What the product may take from the path model (S0127): the model in force (`host()`, `HOST`), POSIX's (the protocol's
pattern), the fold rule and its error. Never `WINDOWS`, `for_platform`, `simulating` or `_current`: each would put a
model other than this OS's in force, or apply one."""
SWITCHES = frozenset({"simulating", "_current", "for_platform"})


def model_switches(rel: str, source: str) -> list[str]:
    """Every way `source` (the module at `rel` under `ledger_engine/`) could reach past `PATHMODEL_ALLOWED`: a name of
    `SWITCHES` in any position (a name, an attribute, an import), an import of another name from the path model, and
    any use of the path model module itself other than reading an allowed attribute from it (`getattr(p, ...)`,
    passing it on). Stated limits: a dynamic import by name is out of an AST scan (`check_engine_boundary.py` allows
    `import_module` only in `ledger/beancount_port.py`); and a name scan cannot see every reflection path to a module
    object. These reach `simulating` and pass this scan (checked 2026-11-21): `sys.modules[...]`, `getattr` on the `fs`
    package with a joined name, `object.__getattribute__`, `operator.attrgetter`, `inspect.getmembers`, a walk of
    `gc.get_objects()`, an allowed function's `__globals__`. Unlike the Windows test switch (BL-0286, removed in S0150),
    this one stays: the product needs the Windows model in its source, so moving the switch out would not make the
    model unreachable (owner, 2026-11-21: "option 2", a stated limit)."""
    found: list[str] = []
    tree = ast.parse(source)
    package = ("ledger_engine", *Path(rel).parent.parts)
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join((*package[: len(package) - (node.level - 1)], *([node.module] if node.module else [])))
            for a in node.names:
                if base == PATHMODEL and a.name not in PATHMODEL_ALLOWED:
                    found.append(f"{rel}:{node.lineno}: imports {a.name} from the path model")
                elif f"{base}.{a.name}" == PATHMODEL:
                    module_names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == PATHMODEL:
                    if a.asname is None:
                        found.append(f"{rel}:{node.lineno}: imports the path model by its dotted name")
                    else:
                        module_names.add(a.asname)
    allowed_reads = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id in module_names
        and n.attr in PATHMODEL_ALLOWED
    }
    for node in ast.walk(tree):
        name = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else node.name
            if isinstance(node, ast.alias)
            else None
        )
        if name in SWITCHES:
            found.append(f"{rel}:{getattr(node, 'lineno', '?')}: names {name}")
        if isinstance(node, ast.Name) and node.id in module_names and id(node) not in allowed_reads:
            found.append(f"{rel}:{node.lineno}: uses the path model module otherwise than by an allowed name")
    return found


def test_the_product_never_puts_another_path_model_in_force() -> None:
    """Must not pass: only tests may put another OS's path model in force (`pathmodel.simulating`, S0127), so the
    product always splits, records and compares paths as the OS it runs on does. Every product module outside the path
    model takes only `PATHMODEL_ALLOWED` from it (`model_switches`)."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel != PATHMODEL_FILE:
            offenders.extend(model_switches(rel, path.read_text(encoding="utf-8")))
    assert not offenders, offenders


@pytest.mark.parametrize(
    "source",
    [
        "from ledger_engine.fs import pathmodel\npathmodel._current.set(pathmodel.WINDOWS)\n",
        "from ledger_engine.fs.pathmodel import for_platform\nM = for_platform('win32')\nM.split('C:\\\\x')\n",
        "from ledger_engine.fs import pathmodel as p\ngetattr(p, 'simul' + 'ating')(p.WINDOWS)\n",
        "from ledger_engine.fs.pathmodel import WINDOWS\nWINDOWS.split('C:\\\\x')\n",
        "from ledger_engine.fs.pathmodel import simulating\n",
        "from ..fs import pathmodel\nx = pathmodel\n",
        "import ledger_engine.fs.pathmodel\n",
        "import ledger_engine.fs.pathmodel as pm\npm.WINDOWS\n",
    ],
)
def test_each_way_past_the_path_models_allowed_names_is_found(source: str) -> None:
    """Must not pass (S0127 verifier F4: the first three passed the name-only guard)."""
    assert model_switches("ledger/x.py", source)


def test_the_allowed_uses_pass() -> None:
    """@regression-guard: the product's own spellings."""
    source = (
        "from ledger_engine.fs.pathmodel import FoldRule, MalformedPathError, fold, host\n"
        "from ledger_engine.fs import pathmodel\nhost().split('/a')\npathmodel.host()\n"
    )
    assert model_switches("ledger/x.py", source) == []


def test_the_path_model_uses_its_switches_only_where_listed() -> None:
    """Inside the path model: `_current` only in `host` and `simulating`, `for_platform` only for `HOST`."""
    tree = ast.parse((SRC / PATHMODEL_FILE).read_text(encoding="utf-8"))
    current_in = {
        f.name
        for f in tree.body
        if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.Name) and n.id == "_current"
    }
    assert current_in == {"host", "simulating"}
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "for_platform"
    ]
    assert len(calls) == 1
    hosts = [
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "HOST"
    ]
    assert len(hosts) == 1
    assert hosts[0].value is calls[0]
