"""Startup-task registry — detect pending tasks and apply them on consent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.schemas.startup_schemas import StartupTaskInfo
from .base import StartupTask
from .upgrade_state import UpgradeState
from .tasks.recipe_migration_task import RecipeMigrationTask
from .tasks.seed_content_task import SeedContentTask

logger = logging.getLogger(__name__)


class StartupTaskRegistry:
    def __init__(self, state: UpgradeState) -> None:
        self._tasks: dict[str, StartupTask] = {}
        self._state = state

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
        result = task.apply()
        if result.get("errors"):
            # Partial failure (best-effort per file): record that the user
            # consented so a data-driven task downgrades to a dismissible notice
            # on the next detect instead of re-gating the app forever.
            self._state.mark_consented(task_id)
        elif task.one_shot:
            # Clean apply: retire a one-shot so it doesn't surface again.
            self._state.mark_completed(task_id)
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
    task and the registry read/write one `.upgrade-state.json`."""
    state = UpgradeState(config_dir)
    registry = StartupTaskRegistry(state)
    registry.register(RecipeMigrationTask(recipes_dir))
    if data_dir is not None:
        registry.register(
            SeedContentTask(state, config_dir, data_dir, currency, setup_complete)
        )
    return registry
