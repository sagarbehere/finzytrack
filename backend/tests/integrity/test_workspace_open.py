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


WRITE_METHODS = {"write_text", "write_bytes"}  # pathlib
WRITE_FUNCS = {("os", "replace"), ("os", "rename"), ("os", "renames"), ("os", "link"), ("os", "symlink")} | {
    ("shutil", f) for f in ("copy", "copy2", "copyfile", "copytree", "move")
}


def _writes_outside_adapters() -> list[str]:
    """Calls that write files, in any `app` module outside `platform/adapters/` (ARCHITECTURE.md §2.2)."""
    hits: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("platform/adapters/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            mode = (
                node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            )
            writes_open = (
                name == "open" and isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "wax+")
            )
            if name in WRITE_METHODS or (owner, name) in WRITE_FUNCS or writes_open:
                hits.append(f"{rel}:{node.lineno} {owner}.{name}")
    return hits


@pytest.mark.fixme(id="L1-04", due="M2", reason="capability not built")
def test_no_mirror_export_endpoints_and_no_ledger_write_outside_the_contract() -> None:
    """Seeding and seed refresh cannot bypass the write lock: in the main sidecar nothing outside the I/O adapters
    writes a file at all (ledger writes go to the engine's contract, ADR-0054), and the unlocked v0.3 mirror export
    endpoints do not exist (ADR-0015). Assumed: `create_app` accepts a container factory that does no I/O."""
    create_app = planned("app.api", "create_app")
    paths = {route.path for route in create_app(lambda: None).routes}

    assert not {p for p in paths if p.endswith(("/ledger/export", "/export/status")) or "/mirror/export" in p}
    assert _writes_outside_adapters() == []
