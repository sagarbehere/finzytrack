"""Startup-task registry — detect pending tasks and apply them on consent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.schemas.startup_schemas import StartupTaskInfo
from app.seed_refresh.refresh import rebaseline_after_rewrite, snapshot_on_disk
from .base import StartupTask
from .upgrade_state import UpgradeState
from .tasks.recipe_migration_task import RecipeMigrationTask
from .tasks.seed_content_task import SeedContentTask

logger = logging.getLogger(__name__)


class StartupTaskRegistry:
    def __init__(
        self,
        state: UpgradeState,
        config_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._tasks: dict[str, StartupTask] = {}
        self._state = state
        # config_dir/data_dir enable the post-apply seed re-baseline (§10.1). When
        # absent (a bare registry with no seed context), the re-baseline no-ops.
        self._config_dir = config_dir
        self._data_dir = data_dir

    def register(self, task: StartupTask) -> None:
        self._tasks[task.id] = task

    def get(self, task_id: str) -> StartupTask | None:
        return self._tasks.get(task_id)

    def detect(self) -> list[StartupTaskInfo]:
        """Read-only: the pending tasks to surface, skipping completed one-shots."""
        pending: list[StartupTaskInfo] = []
        for task in self._tasks.values():
            if task.one_shot and self._state.is_completed(task.id):
                continue
            info = task.detect(consented=self._state.is_consented(task.id))
            if info is not None:
                pending.append(info)
        # Audit trail of what was surfaced to the user. INFO only when something
        # is pending — detection runs on every app load, so a "nothing pending"
        # line would be pure noise (kept at DEBUG for deep troubleshooting).
        if pending:
            logger.info(
                "Startup tasks pending: %s",
                ", ".join(f"{i.id} ({i.severity})" for i in pending),
            )
        else:
            logger.debug("No startup tasks pending")
        return pending

    def apply(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        # Snapshot what's on disk *before* the task runs, so we can tell which
        # pristine seeded files the task rewrote in place (re-baseline below).
        # Skipped when the registry has no seed context (a bare test registry).
        before = (
            snapshot_on_disk(self._config_dir, self._data_dir)
            if self._config_dir is not None
            else None
        )
        result = task.apply()
        if result.get("errors"):
            # Partial failure (best-effort per file): record that the user
            # consented so a data-driven task downgrades to a dismissible notice
            # on the next detect instead of re-gating the app forever.
            self._state.mark_consented(task_id)
        elif task.one_shot:
            # Clean apply: retire a one-shot so it doesn't surface again.
            self._state.mark_completed(task_id)
        # If the task rewrote a file the user hadn't touched (e.g. a format
        # migration rewriting a pristine seeded dashboard), re-baseline the seed
        # provenance so the seed-content refresh still recognises it as pristine
        # and keeps delivering bundle improvements (colours, new defaults). General
        # across tasks/asset classes; a no-op for tasks that touch no seed files.
        # See dev-docs/seed-content-refresh.md §10.1.
        if before is not None:
            rebaselined = rebaseline_after_rewrite(
                self._config_dir, self._data_dir, before, self._state
            )
            if rebaselined:
                logger.info(
                    "Re-baselined seed provenance for %d untouched file(s) after %s: %s",
                    len(rebaselined), task_id, ", ".join(rebaselined),
                )
        return result

    def dismiss(self, task_id: str) -> dict[str, Any]:
        """Dismiss a non-blocking notice without applying it. A task that offers a
        `snooze()` (the seed-content notice) records a per-content-digest snooze so
        it reappears only when a later release changes the bundle; a plain one-shot
        notice is marked completed so it shows exactly once."""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        snooze = getattr(task, "snooze", None)
        if callable(snooze):
            snooze()
        elif task.one_shot:
            self._state.mark_completed(task_id)
        return {"dismissed": True}

    def reopen_dismissed(self) -> None:
        """Undo dismissals on notices that support it (the seed-content notice),
        so an accidentally-dismissed notice re-surfaces on the next detect. Drives
        Settings → "Show dismissed notices". Gating migrations are deliberately
        untouched — they self-manage and must never be forced to re-gate."""
        for task in self._tasks.values():
            reopen = getattr(task, "reopen", None)
            if callable(reopen):
                reopen()


def build_startup_registry(
    config_dir: Path,
    recipes_dir: Path,
    data_dir: Path | None = None,
    currency: str = "USD",
    setup_complete: bool = True,
) -> StartupTaskRegistry:
    """Construct the registry with all tasks bound to their dependencies.

    New asset migrations / notices register here as additional tasks. The
    seed-content notice needs the data dir + currency (for the demo ledgers) and
    is only registered once those are known — the state object is shared so the
    task and the registry read/write one `.upgrade-state.json`.

    `setup_complete` goes to every task that shouldn't surface during first-run
    setup: the seed-content notice (demos are irrelevant pre-setup) and the recipe
    migration (a gating task must not pre-empt the wizard)."""
    state = UpgradeState(config_dir)
    registry = StartupTaskRegistry(state, config_dir, data_dir)
    registry.register(RecipeMigrationTask(recipes_dir, setup_complete))
    if data_dir is not None:
        registry.register(
            SeedContentTask(state, config_dir, data_dir, currency, setup_complete)
        )
    return registry
