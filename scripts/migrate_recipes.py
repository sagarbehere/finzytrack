#!/usr/bin/env python3
"""Recipe v1 → v2 migration (the steps/DAG refactor).

Converts the legacy recipe format — standalone widgets + dashboards whose
widgets carry a single `query` + optional `transform` — into the v2 format:
dashboards only, each widget an inline `steps`/`output` DAG, stamped with
`schemaVersion: 2`.

This is the ONLY code that understands the legacy shape; the running app
rejects it. The conversion core (`migrate_widget`, `migrate_dashboard`) is
imported by the startup migration runner (backend/app/migrations) so the CLI
(seed-config path) and the auto-on-startup path (active config) share one
implementation. See dev-docs/refactored-dashboard-recipes.md §4.11a, §5.

Two transforms:
  1. query+transform → steps[ sql(main), transform(out) ] + output:"out".
     Widget-level dbType moves onto the `main` sql step.
  2. Standalone widgets referenced by a dashboard layout are inlined into that
     dashboard's widgets[]; the widgets/ directory is removed; orphan
     standalone widgets (referenced by no dashboard) are reported and deleted.

Idempotent: a dashboard already at schemaVersion 2 is left untouched.
Robust: a malformed/unparseable file is skipped and reported, never fatal.

Usage:
    python scripts/migrate_recipes.py <recipes_dir> [<recipes_dir> ...]
    python scripts/migrate_recipes.py --check <recipes_dir>   # report only, no writes
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
VALID_DB_TYPES = {"sqlite", "beanquery"}


# ── Core conversion (pure; shared with the startup runner) ───────────────────


def migrate_widget(widget: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy widget (query + optional transform) into a v2 inline
    widget (steps + output). Every non-pipeline field is preserved verbatim.

    Idempotent: a widget that already has `steps` is returned unchanged.
    """
    if "steps" in widget:
        return widget

    out: dict[str, Any] = {}
    # Preserve field order roughly: id/title/description/helpText/parameters first.
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
    """Convert a legacy dashboard into v2: migrate each inline widget, inline
    any standalone widgets referenced by the layout, stamp schemaVersion.

    `standalone_widgets` maps widget id → raw legacy widget; ids that get
    inlined are added to `inlined`.

    Idempotent: a dashboard already at schemaVersion 2 is returned unchanged.
    """
    if dashboard.get("schemaVersion") == SCHEMA_VERSION:
        return dashboard

    out = dict(dashboard)
    inline_widgets: list[dict[str, Any]] = list(dashboard.get("widgets") or [])
    inline_by_id = {w.get("id"): w for w in inline_widgets}

    # Inline any layout reference not already present as an inline widget.
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
    # Stamp version first in the dict for readability when re-serialised.
    return {"schemaVersion": SCHEMA_VERSION, **{k: v for k, v in out.items() if k != "schemaVersion"}}


# ── CLI / filesystem driver ──────────────────────────────────────────────────


@dataclass
class MigrationReport:
    migrated_dashboards: list[str] = field(default_factory=list)
    skipped_already_v2: list[str] = field(default_factory=list)
    inlined_widgets: list[str] = field(default_factory=list)
    deleted_orphans: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

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


def migrate_recipes_dir(recipes_dir: Path, *, write: bool = True) -> MigrationReport:
    """Migrate a single `recipes/` directory (with dashboards/ and optional
    widgets/). Best-effort per file; malformed files are reported, not fatal."""
    report = MigrationReport()
    dashboards_dir = recipes_dir / "dashboards"
    widgets_dir = recipes_dir / "widgets"

    # Load standalone widgets (legacy).
    standalone: dict[str, dict[str, Any]] = {}
    standalone_files: dict[str, Path] = {}
    if widgets_dir.is_dir():
        for wf in sorted(widgets_dir.glob("*.json")):
            data = _load_json(wf)
            if data is None or "id" not in data:
                report.errors.append(f"skip unparseable widget {wf}")
                continue
            standalone[data["id"]] = data
            standalone_files[data["id"]] = wf

    inlined: set[str] = set()

    if dashboards_dir.is_dir():
        for df in sorted(dashboards_dir.glob("*.json")):
            data = _load_json(df)
            if data is None:
                report.errors.append(f"skip unparseable dashboard {df}")
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
                df.write_text(json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report.migrated_dashboards.append(df.name)

    report.inlined_widgets.extend(sorted(inlined))

    # Orphans: standalone widgets referenced by no dashboard layout.
    orphans = sorted(set(standalone) - inlined)
    for oid in orphans:
        report.deleted_orphans.append(oid)
        if write:
            standalone_files[oid].unlink(missing_ok=True)

    # Remove inlined standalone files and the (now-empty) widgets/ dir.
    if write and widgets_dir.is_dir():
        for wid in inlined:
            standalone_files.get(wid, Path("/nonexistent")).unlink(missing_ok=True)
        # Drop the directory if nothing remains.
        remaining = list(widgets_dir.glob("*"))
        if not remaining:
            widgets_dir.rmdir()

    return report


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    write = "--check" not in argv
    if not args:
        print(__doc__)
        return 2
    overall_errors = 0
    for d in args:
        path = Path(d)
        if not path.is_dir():
            print(f"not a directory: {path}", file=sys.stderr)
            overall_errors += 1
            continue
        report = migrate_recipes_dir(path, write=write)
        print(f"[{path}] {report.summary()}")
        for line in report.deleted_orphans:
            print(f"  orphan removed: {line}")
        for line in report.errors:
            print(f"  ! {line}")
        overall_errors += len(report.errors)
    return 1 if overall_errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
