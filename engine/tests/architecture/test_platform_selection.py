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

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
ADAPTERS = frozenset({"ledger_engine.fs.darwin"})
SELECTION = "fs/this_os.py"


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


def test_the_selection_point_is_the_one_that_imports_the_macos_adapter() -> None:
    """`@regression-guard`: the scan sees the selection point's own import, so it would see any other."""
    assert adapter_imports(SELECTION, (SRC / SELECTION).read_text(encoding="utf-8")) == ["ledger_engine.fs.darwin"]


def test_every_spelling_of_an_adapter_import_is_seen() -> None:
    """Must not pass: each spelling below is an adapter import; a plain port import is not."""
    cases = {
        "import ledger_engine.fs.darwin\n": ["ledger_engine.fs.darwin"],
        "import ledger_engine.fs.darwin as d\n": ["ledger_engine.fs.darwin"],
        "from ledger_engine.fs.darwin import DarwinFS\n": ["ledger_engine.fs.darwin"],
        "from ledger_engine.fs import darwin\n": ["ledger_engine.fs.darwin"],
        "def f():\n    from ledger_engine.fs.darwin import DarwinFS\n": ["ledger_engine.fs.darwin"],
        "from ..fs.darwin import DarwinFS\n": ["ledger_engine.fs.darwin"],
        "from ..fs import darwin\n": ["ledger_engine.fs.darwin"],
        "from ledger_engine.fs.port import FileSystem\n": [],
        "from ledger_engine.fs.this_os import for_this_os\n": [],
        "from ..fs import port\n": [],
    }
    for source, want in cases.items():
        assert adapter_imports("protocol/x.py", source) == want, source
    assert adapter_imports("fs/x.py", "from . import darwin\n") == ["ledger_engine.fs.darwin"]
    assert adapter_imports("fs/x.py", "from .darwin import DarwinFS\n") == ["ledger_engine.fs.darwin"]
