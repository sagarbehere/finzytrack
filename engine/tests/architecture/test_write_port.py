"""The write path reaches the disk only through the file-system port (`ledger_engine.fs.port`; ARCHITECTURE.md §2.3
"Ports and their adapters", EP-07, EP-09; S0060).

The durable order's guarantees (the path guard through one descriptor, the swap, `F_FULLFSYNC`) hold only if no
module of the write path opens, renames, syncs or removes a file by itself, and the crash tests over the in-memory
fake test the real write path only if it has no other way to the disk. Checked from the source (AST): the modules of
the write path import none of the modules that touch files, and only `ledger_engine.fs` imports `ctypes` or `fcntl`
(the protocol server's engine lock excepted).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
WRITE_PATH = (
    "ledger/writer.py",
    "ledger/restore.py",
    "ledger/handles.py",
    "ledger/paths.py",
    "ledger/core/journal.py",
    "ledger/core/paths.py",
    "ledger/core/recovery.py",
)
DISK = frozenset({"os", "io", "shutil", "pathlib", "tempfile", "fcntl", "ctypes", "posix", "mmap", "glob"})
OS_LEVEL = frozenset({"ctypes", "fcntl"})
OS_LEVEL_ALSO = {"protocol/server.py": {"fcntl"}}  # the engine lock's `flock` (engine-jobs §5.1), not a file write


def imported(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            names.add("open()")
    return names


def test_the_write_path_imports_nothing_that_touches_files() -> None:
    for rel in WRITE_PATH:
        bad = imported((SRC / rel).read_text(encoding="utf-8")) & (DISK | {"open()"})
        assert not bad, f"{rel} reaches the disk without the port: {sorted(bad)}"


def test_only_the_fs_package_imports_ctypes_or_fcntl() -> None:
    """@regression-guard (S0060): it held before this slice; it keeps OS-level file work in `ledger_engine.fs`."""
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("fs/"):
            continue
        bad = imported(path.read_text(encoding="utf-8")) & OS_LEVEL - OS_LEVEL_ALSO.get(rel, set())
        assert not bad, f"{rel} imports {sorted(bad)}: only ledger_engine.fs does OS-level file work"


def test_the_check_sees_a_planted_import() -> None:
    """@regression-guard (S0060): the must-not-pass case of the checks above (EP-29)."""
    assert imported("import os.path\nfrom ctypes import CDLL\nx = open('f')\n") >= {"os", "ctypes", "open()"}
