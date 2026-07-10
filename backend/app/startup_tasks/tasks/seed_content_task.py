"""Startup task: deliver new/updated bundled demo content to existing installs.

Non-blocking `info` notice (never gates the app). Data-driven and self-retiring:
`detect()` surfaces the notice only while a refresh would actually add or update
files for *this* user, and stops once everything is delivered (or the user
snoozes it). `apply()` runs the provenance-safe refresh (add-new + refresh-pristine
+ preserve-edited, with backups). See dev-docs/seed-content-refresh.md §9.

Refinement over the pure-digest sketch in the design: detection gates on
"would this refresh actually change a file for this user?" rather than on the
bundle digest alone, so a release whose only bundle changes are files the user
has already edited never raises an empty "content updated" notice. The snooze is
still keyed by content-digest, so a dismissed notice reappears only when a later
release ships different content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.startup_schemas import StartupTaskInfo, SEVERITY_INFO
from app.seed_refresh import apply_seed_refresh, preview_refresh
from app.startup_tasks.base import StartupTask
from app.startup_tasks.upgrade_state import UpgradeState

TASK_ID = "seed-content"
DOCS_PATH = "upgrade-notes/seed-content"


class SeedContentTask(StartupTask):
    id = TASK_ID
    one_shot = False  # data-driven: retires when nothing more is deliverable

    def __init__(
        self,
        state: UpgradeState,
        config_dir: Path,
        data_dir: Path,
        currency: str,
        setup_complete: bool = True,
    ) -> None:
        self._state = state
        self._config_dir = config_dir
        self._data_dir = data_dir
        self._currency = currency
        self._setup_complete = setup_complete

    def detect(self, consented: bool = False) -> StartupTaskInfo | None:
        # Demos are irrelevant until the user has finished setup — and firing
        # mid-wizard (before the first-run baseline is recorded) would misclassify
        # everything. Stay silent until setup completes.
        if not self._setup_complete:
            return None

        report = preview_refresh(
            self._config_dir, self._data_dir, self._currency, self._state.installed_hashes()
        )
        # Nothing to deliver to this user → retire (covers "already up to date"
        # and "the only bundle changes are files you've edited").
        if not report.would_change():
            return None
        # Snoozed for this exact bundle → stay quiet until a later release changes
        # the content-digest.
        if self._state.dismissed_content_digest() == report.content_digest:
            return None

        n_add = len(report.added)
        n_refresh = len(report.refreshed)
        parts = []
        if n_add:
            parts.append(f"{n_add} new demo {'file' if n_add == 1 else 'files'}")
        if n_refresh:
            parts.append(f"{n_refresh} updated demo {'file' if n_refresh == 1 else 'files'}")
        what = " and ".join(parts)

        summary = (
            f"This version of Finzytrack includes {what} (demo dashboards and/or demo "
            "ledger data). You can add them now.\n\n"
            "Nothing is changed until you choose to apply. When you do, a timestamped "
            "backup of every replaced file is saved first, and anything you've edited "
            "yourself is left untouched."
        )

        return StartupTaskInfo(
            id=self.id,
            title="New demo content available",
            summary=summary,
            severity=SEVERITY_INFO,
            requires_consent=False,
            docs_path=DOCS_PATH,
            details=report.to_details(),
        )

    def apply(self) -> dict[str, Any]:
        report = apply_seed_refresh(
            self._state, self._config_dir, self._data_dir, self._currency
        )
        return report.to_result()

    def snooze(self) -> str:
        """Record that the user dismissed the current bundle's notice, so it won't
        re-nag until a later release changes the content-digest. Returns the
        snoozed digest. (The Dismiss action calls this instead of apply().)"""
        digest = preview_refresh(
            self._config_dir, self._data_dir, self._currency, self._state.installed_hashes()
        ).content_digest
        self._state.snooze_seed(digest)
        return digest
