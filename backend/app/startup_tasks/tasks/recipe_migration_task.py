"""Startup task: upgrade saved dashboards to the step-based format (v1 → v2).

Data-driven (self-retiring): once every recipe is at the target version,
`detect()` returns None and the task stops surfacing. `apply()` runs the
migration with backups (and rehomes orphan widgets) only after the user consents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.migrations.recipe_migration import detect_pending
from app.migrations.runner import apply_recipe_migration
from app.schemas.startup_schemas import (
    StartupTaskInfo, SEVERITY_ACTION_REQUIRED, SEVERITY_INFO,
)
from app.startup_tasks.base import StartupTask

# Format-neutral, stable id: this one task migrates recipes to the CURRENT
# format across every version hop (v1→v2 today, later steps appended to the same
# chain), so the id names the invariant ("upgrade recipes") rather than any one
# version's format. See dev-docs/upgrades.md §5.
TASK_ID = "recipes-upgrade"
DOCS_PATH = "upgrade-notes/dashboards-step-format"


class RecipeMigrationTask(StartupTask):
    id = TASK_ID
    one_shot = False  # detection is data-driven; no completion marker needed

    def __init__(self, recipes_dir: Path) -> None:
        self._recipes_dir = recipes_dir

    def detect(self, consented: bool = False) -> StartupTaskInfo | None:
        pending = detect_pending(self._recipes_dir)
        if pending["total"] == 0:
            return None

        n = pending["legacy_dashboards"]
        w = pending["standalone_widgets"]
        parts = []
        if n:
            parts.append(f"{n} saved dashboard{'s' if n != 1 else ''}")
        if w:
            parts.append(f"{w} standalone widget{'s' if w != 1 else ''}")
        what = " and ".join(parts) if parts else "your recipes"

        # Already applied once, yet items remain → they couldn't be converted
        # (e.g. a dashboard referencing a widget that no longer exists). Surface a
        # dismissible notice instead of gating the app again; the un-converted
        # files simply won't load until fixed. This is the no-permanent-wedge path.
        if consented:
            return StartupTaskInfo(
                id=self.id,
                title="Some dashboards couldn't be upgraded",
                summary=(
                    f"{what} couldn't be upgraded to the new format and won't be shown "
                    "until fixed. This usually means a dashboard references a widget that "
                    "no longer exists. Edit or remove the affected file(s), or restore the "
                    "original from its timestamped .backup. See the Upgrade Notes."
                ),
                severity=SEVERITY_INFO,
                requires_consent=False,
                docs_path=DOCS_PATH,
                details=pending,
            )

        summary = (
            f"This version of Finzytrack uses a new dashboard format. {what} need to be "
            "upgraded before your dashboards can be shown.\n\n"
            "Nothing is changed until you choose to upgrade. When you do, a timestamped "
            "backup of every changed file is saved first (changed dashboards keep a .backup "
            "beside them; removed widget files are copied to config/recipes/.migration-backups/), "
            "and any standalone widget not used by a dashboard is preserved as its own new dashboard."
        )

        return StartupTaskInfo(
            id=self.id,
            title="Upgrade saved dashboards",
            summary=summary,
            severity=SEVERITY_ACTION_REQUIRED,
            requires_consent=True,
            docs_path=DOCS_PATH,
            details=pending,
        )

    def apply(self) -> dict[str, Any]:
        report = apply_recipe_migration(self._recipes_dir)
        # Normalized outcome the startup modal renders generically (base.py):
        # succeeded/failed lists of {path, note?} / {path, reason}.
        succeeded = (
            [{"path": f"dashboards/{n}", "note": "upgraded"} for n in report.migrated_dashboards]
            + [{"path": f"widgets/{n}", "note": "inlined into its dashboard"} for n in report.inlined_widgets]
            + [{"path": f"widgets/{o}", "note": f"rehomed into '{d}'"} for o, d in report.rehomed_orphans]
        )
        return {
            "migrated_dashboards": report.migrated_dashboards,
            "inlined_widgets": report.inlined_widgets,
            "rehomed_orphans": [{"widget": o, "dashboard": d} for o, d in report.rehomed_orphans],
            "errors": report.errors,  # [{path, reason}] — also the outcome.failed list
            "outcome": {"succeeded": succeeded, "failed": report.errors},
            "summary": report.summary(),
        }
