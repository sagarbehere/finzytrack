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


def _plain_write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


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

    sql_step: dict[str, Any] = {"id": "main", "kind": "sql", "query": query}
    db_type = widget.get("dbType")
    if db_type is not None:
        if db_type not in VALID_DB_TYPES:
            raise ValueError(
                f"widget '{widget.get('id', '?')}' has dbType '{db_type}' "
                f"outside the v2 enum {sorted(VALID_DB_TYPES)}"
            )
        sql_step["dbType"] = db_type

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

    out["steps"] = [sql_step, transform_step]
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
    deleted_orphans: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.migrated_dashboards or self.deleted_orphans or self.inlined_widgets)

    def summary(self) -> str:
        return (
            f"migrated {len(self.migrated_dashboards)} dashboard(s), "
            f"skipped {len(self.skipped_already_v2)} (already v2), "
            f"inlined {len(self.inlined_widgets)} standalone widget(s), "
            f"deleted {len(self.deleted_orphans)} orphan(s), "
            f"{len(self.errors)} error(s)"
        )


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def migrate_recipes_dir(
    recipes_dir: Path,
    *,
    write: bool = True,
    write_fn: WriteFn | None = None,
) -> MigrationReport:
    """Migrate a single `recipes/` directory (dashboards/ + optional widgets/).
    Best-effort per file — a malformed file is reported, never fatal. `write_fn`
    lets the active-config path inject an atomic+backup writer."""
    writer = write_fn or _plain_write
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

    orphans = sorted(set(standalone) - inlined)
    for oid in orphans:
        report.deleted_orphans.append(oid)
        if write:
            standalone_files[oid].unlink(missing_ok=True)

    if write and widgets_dir.is_dir():
        for wid in inlined:
            standalone_files.get(wid, Path("/nonexistent")).unlink(missing_ok=True)
        if not list(widgets_dir.glob("*")):
            widgets_dir.rmdir()

    return report
