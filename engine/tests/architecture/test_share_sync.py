"""The share's one tolerated refusal stays where the owner put it (S0136, BL-0219; owner, 2026-11-20: "Best effort +
open notice (Recommended)"; as S0132 round 1's red team F2 pinned the mirror's): a ledger folder's sync refused by a
network share's server (`SyncRefused`) counts as done, and nothing else does.

Checked from the source (AST), over every module of `ledger_engine`:
- no `except` names `SyncRefused` outside `ledger/handles.py`'s `ledger_sync` (the adapters and the port may define
  and raise it);
- every folder sync of the write path is classified: `ledger_sync(...)` only in the functions that sync a ledger folder
  (`LEDGER_SITES`), a plain `.sync()` only in those that sync the journal, backups, app or mirror folders
  (`APP_SITES`), each by its count. A new site, or one moved from one side to the other, fails here until it is
  classified on purpose: a journal or backups sync that went through `ledger_sync` would make "saved" rest on a sync
  that never happened (INV-27, INV-30).
The inventory (S0136's brief, "Sync sites"): 22 ledger-folder sites, 15 app-side sites.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"
HOME = ("ledger/handles.py", "ledger_sync")

LEDGER_SITES: Counter[tuple[str, str]] = Counter(
    {
        ("ledger/writer.py", "_stage"): 1,
        ("ledger/writer.py", "_best_effort"): 1,
        ("ledger/writer.py", "_replace"): 3,
        ("ledger/writer.py", "_remove_temps"): 1,
        ("ledger/handles.py", "keep"): 1,
        ("ledger/restore.py", "_put_back"): 3,
        ("ledger/restore.py", "_sync_folders"): 1,
        ("ledger/recover_all.py", "keep"): 1,
        ("ledger/recover_all.py", "_delete_one"): 1,
        ("ledger/create.py", "_cleanup"): 2,
        ("ledger/create.py", "_target"): 1,
        ("ledger/create.py", "_sync"): 2,
        ("ledger/create.py", "_manifest"): 1,
        ("ledger/create.py", "_write_temp"): 2,
        ("ledger/create.py", "outcome_of"): 1,
    }
)
"""`ledger_sync(...)` calls: the folders a save, an undo, Keep, a delete or a create writes ledger files in."""

APP_SITES: Counter[tuple[str, str]] = Counter(
    {
        ("ledger/writer.py", "_backups"): 2,
        ("ledger/writer.py", "durable_commit"): 2,
        ("ledger/restore.py", "restore"): 2,
        ("ledger/recover_all.py", "list_journals"): 2,
        ("ledger/recover_all.py", "keep"): 1,
        ("ledger/save.py", "_ensure_dir"): 1,
        ("ledger/save.py", "outcome"): 1,
        ("ledger/history.py", "trim"): 1,
        ("mirror/publish.py", "open_folder"): 1,
        ("mirror/publish.py", "_ensure"): 1,
        ("mirror/publish.py", "publish"): 1,
    }
)
"""Plain `.sync()` calls outside the adapters: the journal, backups, app and mirror folders, always needed."""


def _names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        out: set[str] = set()
        for e in node.elts:
            out |= _names(e)
        return out
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def scan(source: str, rel: str) -> tuple[list[str], Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    """The `except SyncRefused` clauses outside the home (with their lines), and the `ledger_sync(...)` and plain
    `.sync()` calls, each by its enclosing function. A `lambda h=h: ledger_sync(h)` counts in its function."""
    catches: list[str] = []
    ledger: Counter[tuple[str, str]] = Counter()
    plain: Counter[tuple[str, str]] = Counter()

    def walk(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else func
            if isinstance(child, ast.ExceptHandler) and "SyncRefused" in _names(child.type) and (rel, func) != HOME:
                catches.append(f"{rel}:{child.lineno} ({func})")
            if isinstance(child, ast.Call):
                f = child.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name == "ledger_sync":
                    ledger[(rel, func)] += 1
                elif isinstance(f, ast.Attribute) and f.attr == "sync" and not child.args and not child.keywords:
                    plain[(rel, func)] += 1
            walk(child, inner)

    walk(ast.parse(source), "<module>")
    return catches, ledger, plain


def _all() -> tuple[list[str], Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    catches: list[str] = []
    ledger: Counter[tuple[str, str]] = Counter()
    plain: Counter[tuple[str, str]] = Counter()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("fs/"):
            continue  # the adapters define and raise it; their own `sync` is the call itself
        c, lg, pl = scan(path.read_text(encoding="utf-8"), rel)
        catches += c
        ledger += lg
        plain += pl
    return catches, ledger, plain


def test_only_ledger_sync_takes_a_refused_share_sync_as_done() -> None:
    catches, _, _ = _all()
    assert catches == [], f"only {HOME[0]}'s {HOME[1]} may catch SyncRefused: {catches}"
    source = (SRC / HOME[0]).read_text(encoding="utf-8")
    home = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == HOME[1])
    assert any(isinstance(n, ast.ExceptHandler) and "SyncRefused" in _names(n.type) for n in ast.walk(home))


def test_every_folder_sync_of_the_engine_is_classified_ledger_or_app() -> None:
    _, ledger, plain = _all()
    plain[HOME] -= 1  # `ledger_sync`'s own `h.sync()`
    plain = +plain
    assert ledger == LEDGER_SITES, (
        f"ledger-folder syncs changed: {dict(ledger - LEDGER_SITES)} added, {dict(LEDGER_SITES - ledger)} gone"
    )
    assert plain == APP_SITES, (
        f"app-folder syncs changed: {dict(plain - APP_SITES)} added, {dict(APP_SITES - plain)} gone"
    )
    assert sum(LEDGER_SITES.values()) == 22
    assert sum(APP_SITES.values()) == 15


def test_the_check_sees_a_planted_catch_and_a_planted_site() -> None:
    """The must-not-pass cases (EP-29): a catch of `SyncRefused` in the writer, and a journal sync moved to
    `ledger_sync`."""
    planted = (
        "def durable_commit(jd):\n"
        "    try:\n"
        "        jd.sync()\n"
        "    except (OSError, port.SyncRefused):\n"
        "        pass\n"
        "    ledger_sync(jd)\n"
    )
    catches, ledger, plain = scan(planted, "ledger/writer.py")
    assert catches == ["ledger/writer.py:4 (durable_commit)"]
    assert ledger == Counter({("ledger/writer.py", "durable_commit"): 1})
    assert plain == Counter({("ledger/writer.py", "durable_commit"): 1})
    assert ledger + Counter(LEDGER_SITES) != LEDGER_SITES
