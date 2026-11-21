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
NOT_ADAPTERS = frozenset({"__init__", "port", "this_os", "posix", "pathmodel", "under_test"})
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


SWITCH = "ledger_engine.fs.under_test"
SWITCH_FILE = "fs/under_test.py"
THIS_OS = "ledger_engine.fs.this_os"
SWITCH_NAMES = frozenset({"windows_port_under_test", "_windows_under_test", "under_test", "ON", "selected"})
"""The names that reach the test-only switch selecting the Windows adapter (S0128; BL-0087 turns writes on)."""
REFLECTION = frozenset({"getattr", "setattr", "delattr", "vars", "__dict__", "globals", "__getattribute__"})
CONTEXTVARS_HOMES = frozenset({"fs/pathmodel.py", SWITCH_FILE})
"""Where the product may use `contextvars` (S0128 round 1, verifier F1): iterating a copied context reaches every
context variable, the path model's and the switch's among them, so nowhere else."""
ENGINE_LOCK_CALLERS = frozenset({"protocol/server.py", SELECTION})
"""Where `this_os.engine_lock_folder` and `this_os.take_engine_lock` may be named (S0128 round 1, verifier F1)."""


def switch_uses(source: str, rel: str = "ledger/x.py") -> list[str]:
    """Every way `source` (the product module at `rel` under `ledger_engine/`) could reach the switch: an import of
    `ledger_engine.fs.under_test` in any spelling; a name of `SWITCH_NAMES` in any position; reflection (`getattr`,
    `setattr`, `vars`, `__dict__`, ...) on a name bound to `this_os`, to the switch module or to the `fs` package; and
    `contextvars` outside `CONTEXTVARS_HOMES`. Stated limits: a dynamic import by name is out of an AST scan
    (`check_engine_boundary.py` allows `import_module`, `__import__` and `exec` only in `ledger/beancount_port.py`);
    and a name scan cannot see every reflection path to a module object. These reach the switch and pass this scan
    (BL-0286): `getattr` with an attribute chain as its first argument, `object.__getattribute__`,
    `operator.attrgetter`, `sys.modules[...]`, a walk of `gc.get_objects()`, `inspect.getmembers`, a function's
    `__globals__`. The behavioural check is the runner's fresh engine process
    (`tests/fs/test_windows.py::test_the_product_on_windows_still_refuses_file_operations_and_never_loads_the_adapter`).
    BL-0087 removes the switch, its fixture and this scan (owner, 2026-11-21: "option 1")."""
    found: list[str] = []
    tree = ast.parse(source)
    package = ("ledger_engine", *Path(rel).parent.parts)
    guarded: set[str] = set()  # local names bound to this_os, the switch module or the fs package
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join((*package[: len(package) - (node.level - 1)], *([node.module] if node.module else [])))
            if base == "contextvars" and rel not in CONTEXTVARS_HOMES:
                found.append(f"{node.lineno}: contextvars")
            for a in node.names:
                full = f"{base}.{a.name}"
                if base == SWITCH or full == SWITCH:
                    found.append(f"{node.lineno}: imports the switch")
                if full in (THIS_OS, SWITCH, "ledger_engine.fs"):
                    guarded.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == SWITCH or a.name.startswith(SWITCH + "."):
                    found.append(f"{node.lineno}: imports the switch")
                if a.name == "contextvars" and rel not in CONTEXTVARS_HOMES:
                    found.append(f"{node.lineno}: contextvars")
                if a.name in (THIS_OS, SWITCH, "ledger_engine.fs", "ledger_engine"):
                    guarded.add((a.asname or a.name).split(".")[0])
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
        line = getattr(node, "lineno", 0)
        if name in SWITCH_NAMES:
            found.append(f"{line}: names {name}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in REFLECTION
            and any(isinstance(a, ast.Name) and a.id in guarded for a in node.args)
        ):
            found.append(f"{line}: {node.func.id} on this_os or the switch")
        if isinstance(node, ast.Attribute) and node.attr in REFLECTION:
            base_name = node.value
            while isinstance(base_name, ast.Attribute):
                base_name = base_name.value
            if isinstance(base_name, ast.Name) and base_name.id in guarded:
                found.append(f"{line}: {node.attr} of this_os or the switch")
    return found


def test_the_product_never_turns_the_windows_port_on() -> None:
    """Must not pass (@regression-guard: on the base there was no switch to reach): the product refuses file operations
    on Windows until BL-0087 (S0128), so no product module but the selection point reaches the switch module, in any
    of the ways `switch_uses` sees."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel not in (SELECTION, SWITCH_FILE):
            offenders.extend(f"{rel}:{n}" for n in switch_uses(path.read_text(encoding="utf-8"), rel))
    assert not offenders, offenders


@pytest.mark.parametrize(
    "source",
    [
        "from ledger_engine.fs.this_os import windows_port_under_test\n",
        "from ledger_engine.fs import this_os\nwith this_os.windows_port_under_test():\n    pass\n",
        "from ledger_engine.fs import this_os\nthis_os._windows_under_test.set(True)\n",
        "from ledger_engine.fs import under_test\nunder_test.ON.set(True)\n",
        "from ledger_engine.fs.under_test import ON as o\no.set(True)\n",
        "import ledger_engine.fs.under_test as u\n",
        "from ..fs import under_test\n",
        # the verifier's four (S0128 round 1, F1): each passed the name-only guard
        'from ledger_engine.fs import this_os\nwith getattr(this_os, "windows_port" + "_under_test")():\n    pass\n',
        'from ledger_engine.fs import this_os\nvars(this_os)["_windows_under_test"].set(True)\n',
        "import contextvars\nfor v in contextvars.copy_context():\n    if v.name == 'windows_port_under_test':\n"
        "        v.set(True)\n",
        'from ledger_engine.fs import this_os as t\nsw = t.__dict__["windows_port_under_test"]\n',
        # and their neighbours
        "from contextvars import copy_context\nlist(copy_context())\n",
        'from ledger_engine.fs import this_os\nsetattr(this_os, "supported", lambda: True)\n',
        "import ledger_engine.fs.this_os\nx = ledger_engine.fs.this_os.__dict__\n",
        'from ledger_engine import fs\ngetattr(fs, "under" + "_test")\n',
    ],
)
def test_each_way_to_the_windows_switch_is_seen(source: str) -> None:
    """Must not pass: each spelling turns the Windows adapter on, or could."""
    assert switch_uses(source)


def test_the_products_own_uses_of_this_os_pass() -> None:
    """@regression-guard: the product's spellings."""
    source = (
        "from ledger_engine.fs import this_os\nfrom ledger_engine.fs.this_os import for_this_os\n"
        "this_os.supported()\nfor_this_os()\n"
    )
    assert switch_uses(source) == []


def test_only_the_server_takes_the_engine_lock_through_the_selection_point() -> None:
    """Must not pass (@regression-guard: the server was the only caller at the base): the Windows engine lock is the one
    file operation the product takes on Windows (S0128), so only `protocol/server.py` names it."""
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


def test_the_selection_point_reads_the_switch_only_beside_a_windows_check() -> None:
    """`supported`, `for_this_os` and `legacy_locks_for_this_os` read `under_test.selected()`, each in a condition that
    also asks for win32; nothing else in the selection point reads it."""
    tree = ast.parse((SRC / SELECTION).read_text(encoding="utf-8"))
    reads: set[str] = set()
    for f in tree.body:
        if isinstance(f, ast.FunctionDef):
            for n in ast.walk(f):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "under_test":
                    reads.add(f"{f.name}.{n.attr}")
            for n in ast.walk(f):
                if isinstance(n, ast.BoolOp) and any("under_test" in ast.unparse(v) for v in n.values):
                    assert any("win32" in ast.unparse(v) for v in n.values), ast.unparse(n)
    assert reads == {"supported.selected", "for_this_os.selected", "legacy_locks_for_this_os.selected"}


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
    passing it on). Stated limit: a dynamic import by name is out of an AST scan (`check_engine_boundary.py` allows
    `import_module` only in `ledger/beancount_port.py`)."""
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
