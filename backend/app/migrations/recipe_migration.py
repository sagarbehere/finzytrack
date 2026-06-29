"""Recipe v1 → v2 conversion core (the steps/DAG refactor).

The ONLY code that understands the legacy recipe format (standalone widgets +
dashboards whose widgets carry a single `query` + optional `transform`). The
running app rejects that format; this converts it to v2 (dashboards only, each
widget an inline `steps`/`output` DAG, stamped `schemaVersion: 2`).

Shared by both call sites (§4.12 "one function, two call sites"):
  - the CLI at scripts/migrate_recipes.py (seed-config path), and
  - the startup runner (runner.py) that upgrades the user's active config.

See dev-docs/refactored-dashboard-recipes.md §4.11a / §5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 2
VALID_DB_TYPES = {"sqlite", "beanquery"}

# A write function: (path, text) -> None. Defaults to a plain write; the startup
# runner injects an atomic+backup writer for the active config.
WriteFn = Callable[[Path, str], None]
# A removal function: (path) -> None. Defaults to a plain unlink; the startup
# runner injects a backup-then-unlink remover so nothing is deleted without a copy.
RemoveFn = Callable[[Path], None]


def _plain_write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _plain_remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _one_widget_dashboard(dash_id: str, widget: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single migrated widget in a v2 one-widget dashboard, so an orphan
    standalone widget survives migration as a viewable dashboard rather than
    being deleted."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": dash_id,
        "title": widget.get("title") or dash_id,
        "description": "Migrated from a standalone widget recipe.",
        "layout": {
            "columns": 6, "gap": "1.5rem", "rowHeight": "200px",
            "widgets": [{"widgetId": widget["id"], "gridArea": "1 / 1 / 5 / 7"}],
        },
        "widgets": [widget],
    }


# ── Core conversion (pure) ───────────────────────────────────────────────────


def migrate_widget(widget: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy widget (query + optional transform) into a v2 inline
    widget (steps + output). Every non-pipeline field is preserved verbatim.
    Idempotent: a widget that already has `steps` is returned unchanged."""
    if "steps" in widget:
        return widget

    out: dict[str, Any] = {}
    for key, value in widget.items():
        if key in ("query", "transform", "dbType"):
            continue
        out[key] = value

    query = widget.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"widget '{widget.get('id', '?')}' has no usable query to migrate")

    # Legacy widgets carry the engine choice in a widget-level `dbType` field;
    # v2 moves it onto the query step as `engine` (same value set).
    query_step: dict[str, Any] = {"id": "main", "kind": "query", "query": query}
    db_type = widget.get("dbType")
    if db_type is not None:
        if db_type not in VALID_DB_TYPES:
            raise ValueError(
                f"widget '{widget.get('id', '?')}' has dbType '{db_type}' "
                f"outside the v2 engine enum {sorted(VALID_DB_TYPES)}"
            )
        query_step["engine"] = db_type

    transform = widget.get("transform")
    if transform is None:
        fn, config = "none", None
    elif isinstance(transform, str):
        fn, config = transform, None
    elif isinstance(transform, dict):
        fn = transform.get("type")
        if not isinstance(fn, str):
            raise ValueError(f"widget '{widget.get('id', '?')}' transform has no 'type'")
        config = transform  # the new catalog ignores the residual `type` key
    else:
        raise ValueError(f"widget '{widget.get('id', '?')}' transform must be a string or object")

    transform_step: dict[str, Any] = {
        "id": "out",
        "kind": "transform",
        "fn": fn,
        "inputs": ["{{steps.main}}"],
    }
    if config is not None:
        transform_step["config"] = config

    out["steps"] = [query_step, transform_step]
    out["output"] = "out"
    return out


def migrate_dashboard(
    dashboard: dict[str, Any],
    standalone_widgets: dict[str, dict[str, Any]],
    inlined: set[str],
) -> dict[str, Any]:
    """Convert a legacy dashboard into v2: migrate each inline widget, inline any
    standalone widgets referenced by the layout, stamp schemaVersion. Idempotent
    on already-v2 dashboards."""
    if dashboard.get("schemaVersion") == SCHEMA_VERSION:
        return dashboard

    out = dict(dashboard)
    inline_widgets: list[dict[str, Any]] = list(dashboard.get("widgets") or [])
    inline_by_id = {w.get("id"): w for w in inline_widgets}

    layout_refs = [lw.get("widgetId") for lw in dashboard.get("layout", {}).get("widgets", [])]
    for ref in layout_refs:
        if ref in inline_by_id:
            continue
        standalone = standalone_widgets.get(ref)
        if standalone is None:
            raise ValueError(f"dashboard '{dashboard.get('id', '?')}' references unknown widget '{ref}'")
        inline_widgets.append(standalone)
        inline_by_id[ref] = standalone
        inlined.add(ref)

    out["widgets"] = [migrate_widget(w) for w in inline_widgets]
    return {"schemaVersion": SCHEMA_VERSION, **{k: v for k, v in out.items() if k != "schemaVersion"}}


# ── Filesystem driver ────────────────────────────────────────────────────────


@dataclass
class MigrationReport:
    migrated_dashboards: list[str] = field(default_factory=list)
    skipped_already_v2: list[str] = field(default_factory=list)
    inlined_widgets: list[str] = field(default_factory=list)
    #: orphan widget id → new wrapper-dashboard id it was rehomed into.
    rehomed_orphans: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.migrated_dashboards or self.rehomed_orphans or self.inlined_widgets)

    def summary(self) -> str:
        return (
            f"migrated {len(self.migrated_dashboards)} dashboard(s), "
            f"skipped {len(self.skipped_already_v2)} (already v2), "
            f"inlined {len(self.inlined_widgets)} standalone widget(s), "
            f"rehomed {len(self.rehomed_orphans)} orphan widget(s), "
            f"{len(self.errors)} error(s)"
        )


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_pending(recipes_dir: Path) -> dict[str, int]:
    """Read-only: how many recipes are below the target format. NEVER writes.

    Returns {legacy_dashboards, standalone_widgets, total}. Used by the startup
    task to decide whether to surface an upgrade prompt — detection must not
    mutate anything, so the user can consent first."""
    dashboards_dir = recipes_dir / "dashboards"
    widgets_dir = recipes_dir / "widgets"

    legacy_dashboards = 0
    if dashboards_dir.is_dir():
        for df in dashboards_dir.glob("*.json"):
            data = _load_json(df)
            if data is not None and data.get("schemaVersion") != SCHEMA_VERSION:
                legacy_dashboards += 1

    standalone_widgets = (
        sum(1 for _ in widgets_dir.glob("*.json")) if widgets_dir.is_dir() else 0
    )
    return {
        "legacy_dashboards": legacy_dashboards,
        "standalone_widgets": standalone_widgets,
        "total": legacy_dashboards + standalone_widgets,
    }


def migrate_recipes_dir(
    recipes_dir: Path,
    *,
    write: bool = True,
    write_fn: WriteFn | None = None,
    remove_fn: RemoveFn | None = None,
) -> MigrationReport:
    """Migrate a single `recipes/` directory (dashboards/ + optional widgets/).
    Best-effort per file — a malformed file is reported, never fatal. `write_fn`
    lets the active-config path inject an atomic+backup writer; `remove_fn` lets
    it back up files before deletion (nothing is deleted without a copy)."""
    writer = write_fn or _plain_write
    remover = remove_fn or _plain_remove
    report = MigrationReport()
    dashboards_dir = recipes_dir / "dashboards"
    widgets_dir = recipes_dir / "widgets"

    standalone: dict[str, dict[str, Any]] = {}
    standalone_files: dict[str, Path] = {}
    if widgets_dir.is_dir():
        for wf in sorted(widgets_dir.glob("*.json")):
            data = _load_json(wf)
            if data is None or "id" not in data:
                report.errors.append(f"skip unparseable widget {wf.name}")
                continue
            standalone[data["id"]] = data
            standalone_files[data["id"]] = wf

    inlined: set[str] = set()

    if dashboards_dir.is_dir():
        for df in sorted(dashboards_dir.glob("*.json")):
            data = _load_json(df)
            if data is None:
                report.errors.append(f"skip unparseable dashboard {df.name}")
                continue
            if data.get("schemaVersion") == SCHEMA_VERSION:
                report.skipped_already_v2.append(df.name)
                continue
            try:
                migrated = migrate_dashboard(data, standalone, inlined)
            except Exception as e:  # noqa: BLE001 — best-effort per file
                report.errors.append(f"error migrating {df.name}: {e}")
                continue
            if write:
                writer(df, json.dumps(migrated, indent=2, ensure_ascii=False) + "\n")
            report.migrated_dashboards.append(df.name)

    report.inlined_widgets.extend(sorted(inlined))

    # Orphans (referenced by no dashboard) are REHOMED into a new one-widget
    # dashboard so nothing is lost, rather than deleted.
    existing_dash_ids = {
        d["id"] for df in dashboards_dir.glob("*.json")
        if (d := _load_json(df)) and isinstance(d.get("id"), str)
    } if dashboards_dir.is_dir() else set()

    orphans = sorted(set(standalone) - inlined)
    for oid in orphans:
        try:
            migrated_widget = migrate_widget(standalone[oid])
        except Exception as e:  # noqa: BLE001 — best-effort per file
            report.errors.append(f"could not rehome orphan widget '{oid}': {e}")
            continue
        # Derive a non-colliding dashboard id/filename for the wrapper.
        dash_id = oid if oid not in existing_dash_ids else f"{oid}-widget"
        while (dashboards_dir / f"{dash_id}.json").exists() or dash_id in existing_dash_ids:
            dash_id = f"{dash_id}-widget"
        existing_dash_ids.add(dash_id)
        dashboard = _one_widget_dashboard(dash_id, migrated_widget)
        if write:
            dashboards_dir.mkdir(parents=True, exist_ok=True)
            writer(dashboards_dir / f"{dash_id}.json", json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
        report.rehomed_orphans.append((oid, dash_id))

    # Remove the now-redundant standalone widget files (inlined + rehomed),
    # backing each up first via remove_fn, then drop the empty widgets/ dir.
    if write and widgets_dir.is_dir():
        for wid in list(inlined) + [oid for oid, _ in report.rehomed_orphans]:
            f = standalone_files.get(wid)
            if f is not None:
                remover(f)
        if not list(widgets_dir.glob("*")):
            widgets_dir.rmdir()

    return report
