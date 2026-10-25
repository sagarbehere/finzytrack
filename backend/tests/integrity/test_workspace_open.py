"""P1 integrity tests for opening a workspace (review:27 §1(d), Appendix B: green in M2): I6, L10.M-1, L1-04.

Spec: INV-29 (record only after `READY`/`WARMING`; a missing ledger is never created), INV-30 (opening never writes
the folder), ADR-0019 (a missing ledger is `LEDGER_UNAVAILABLE`, never auto-created), ADR-0015 (the v0.3 "export the
mirror" endpoints are not rebuilt), review:23 §3.3 step 3.4 and §3.9 (a failed open is never persisted; opening builds
a new container and nothing is swapped in place), ARCHITECTURE.md §2.2 (`platform/adapters` is the only place that
does real I/O) and ADR-0054 (ledger writes happen in the engine, through the write contract).

Interface: `workspaces.open_workspace(key) -> Opened | NotReady(phase)`, `workspaces.Registry`, `UserContainer`
(ARCHITECTURE.md §2.3); `api.create_app(container_factory)`. Assumed, not decided: `UserContainer(app_root)` with
`.registry.add(folder) -> key` and `.open_workspace(key)`, the registry file `workspaces.json` in the app root
(ARCHITECTURE.md §2.5 names the file), a `NotReady` result with a `.phase` string, and the workspace layout
`<folder>/main.beancount` + `<folder>/.finzytrack/workspace.json` written by a test helper.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from tests.integrity.planned import planned

APP = Path(__file__).resolve().parents[2] / "src" / "app"
LEDGER = 'option "operating_currency" "USD"\n2026-01-01 open Assets:Checking USD\n'


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def _user(tmp_path: Path) -> tuple[Any, Path]:
    container = planned("app.workspaces.containers", "UserContainer")(tmp_path / "app")
    return container, tmp_path / "app"


def _workspace(tmp_path: Path, name: str, *, ledger: bool = True, settings: str = "{}\n") -> Path:
    folder = tmp_path / name
    (folder / ".finzytrack").mkdir(parents=True)
    (folder / ".finzytrack" / "workspace.json").write_text('{"ledger": "main.beancount"}\n')
    (folder / ".finzytrack" / "settings.yaml").write_text(settings)
    if ledger:
        (folder / "main.beancount").write_text(LEDGER)
    return folder


@pytest.mark.fixme(id="I6", due="M2", reason="capability not built")
@pytest.mark.parametrize(
    ("broken", "phase"),
    [({"ledger": False}, "LEDGER_UNAVAILABLE"), ({"settings": "accounts: [unclosed\n"}, "WS_CONFIG_INVALID")],
)
def test_a_failed_open_records_nothing_and_swaps_nothing(tmp_path: Path, broken: dict[str, Any], phase: str) -> None:
    user, app_root = _user(tmp_path)
    good = user.registry.add(_workspace(tmp_path, "A"))
    bad = user.registry.add(_workspace(tmp_path, "B", **broken))
    opened_a = user.open_workspace(good)
    registry_before = (app_root / "workspaces.json").read_bytes()
    folder_before = _snapshot(tmp_path / "B")

    result = user.open_workspace(bad)

    assert result.phase == phase  # NotReady(phase), never Opened
    assert (app_root / "workspaces.json").read_bytes() == registry_before  # "last opened" still names A (INV-29)
    assert _snapshot(tmp_path / "B") == folder_before  # nothing written in B (INV-30)
    assert user.open_workspace(good).container is opened_a.container  # A's container was not replaced in place


@pytest.mark.fixme(id="L10.M-1", due="M2", reason="capability not built")
def test_a_missing_ledger_is_unavailable_and_never_created(tmp_path: Path) -> None:
    user, _ = _user(tmp_path)
    folder = _workspace(tmp_path, "W")
    key = user.registry.add(folder)
    (folder / "main.beancount").unlink()  # moved away, or a sync client mid-replace (review:L10-verif M-1)

    result = user.open_workspace(key)

    assert result.phase == "LEDGER_UNAVAILABLE"
    assert not (folder / "main.beancount").exists()
    assert sorted(p.name for p in folder.iterdir()) == [".finzytrack"]


# Calls that write or remove a file, by name wherever they appear (S0029 verifier F1: the adapters' own write
# functions count too, a Path method's mode is its first argument, and `Path.replace` takes one argument).
WRITE_NAMES = {
    "write_text",
    "write_bytes",
    "atomic_replace",
    "touch",
    "unlink",
    "rmdir",
    "symlink_to",
    "hardlink_to",
    "mkstemp",
    "NamedTemporaryFile",
    "rename",
    "renames",
}
WRITE_FUNCS = {("os", f) for f in ("replace", "link", "symlink", "remove", "truncate", "open")} | {
    ("shutil", f) for f in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree")
}
# Modules outside platform/adapters/ that may call an adapter's write function: the app-level stores, once built,
# each added here as a reviewed change. A ledger file is never among their targets: the engine writes ledgers
# through the write contract (ADR-0054, ADR-0008). Empty until the first such store lands.
ALLOWED_WRITERS: set[str] = set()


def _mode(node: ast.Call, index: int) -> object:
    if len(node.args) > index:
        arg = node.args[index]
        return arg.value if isinstance(arg, ast.Constant) else None
    kw = next((k.value for k in node.keywords if k.arg == "mode"), None)
    return kw.value if isinstance(kw, ast.Constant) else None


def _file_writes() -> list[str]:
    """Calls that write files in any `app` module outside `platform/adapters/` (ARCHITECTURE.md §2.2)."""
    hits: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("platform/adapters/") or rel in ALLOWED_WRITERS:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            # builtin open(path, mode) has the mode second; Path.open(mode) first
            mode = _mode(node, 1) if isinstance(func, ast.Name) else _mode(node, 0) if name == "open" else None
            writes = (
                name in WRITE_NAMES
                or (owner, name) in WRITE_FUNCS
                or (name == "open" and isinstance(mode, str) and any(c in mode for c in "wax+"))
                or (name == "replace" and isinstance(func, ast.Attribute) and owner != "os" and len(node.args) == 1)
            )
            if writes:
                hits.append(f"{rel}:{node.lineno} {owner}.{name}")
    return hits


@pytest.mark.fixme(id="L1-04", due="M2", reason="capability not built")
def test_seeding_goes_through_the_write_contract_and_no_mirror_export_endpoint_exists(tmp_path: Path) -> None:
    """Seeding and seed refresh cannot bypass the write lock (review:L1 L1-04): the main sidecar writes no file outside
    its I/O adapters, nothing outside them calls an adapter's write function (ledger writes are the engine's, through
    the contract that takes the locks, ADR-0054 and ADR-0008), demo seeding hands its file to the writer as a plan
    instead of writing it, and the unlocked v0.3 mirror export endpoints do not exist (ADR-0015).

    Interface: `seed_demo` (review:27 M3 (d): "writes the generated demo into the demo workspace … through the
    contract's file-creation path"). Assumed, not decided: `app.boot.demo.seed_demo(demo_root, writer=...)` calling
    `writer.commit(plan)` once, with `plan.creates` naming the files; `create_app` taking a no-I/O container factory.
    """
    assert _file_writes() == []

    create_app = planned("app.api", "create_app")
    seed_demo = planned("app.boot.demo", "seed_demo")
    paths = {route.path for route in create_app(lambda: None).routes}
    assert not {p for p in paths if p.endswith(("/ledger/export", "/export/status")) or "/mirror/export" in p}

    plans: list[Any] = []

    class RecordingWriter:  # stands in for the engine client's commit, which takes the locks (ADR-0008)
        def commit(self, plan: Any) -> Any:
            plans.append(plan)
            return plan

    demo_root = tmp_path / "demo"
    seed_demo(demo_root, writer=RecordingWriter())
    assert len(plans) == 1
    assert "main.beancount" in {Path(p).name for p in plans[0].creates}
    assert not (demo_root / "main.beancount").exists()  # seeding did not write the ledger itself
