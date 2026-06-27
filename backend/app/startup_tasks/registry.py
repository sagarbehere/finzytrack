"""Startup-task registry — detect pending tasks and apply them on consent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.startup_schemas import StartupTaskInfo
from .base import StartupTask
from .upgrade_state import UpgradeState
from .tasks.recipe_migration_task import RecipeMigrationTask


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
            info = task.detect()
            if info is not None:
                pending.append(info)
        return pending

    def apply(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        result = task.apply()
        if task.one_shot:
            self._state.mark_completed(task_id)
        return result


def build_startup_registry(config_dir: Path, recipes_dir: Path) -> StartupTaskRegistry:
    """Construct the registry with all tasks bound to their dependencies.

    New asset migrations / notices register here as additional tasks."""
    registry = StartupTaskRegistry(UpgradeState(config_dir))
    registry.register(RecipeMigrationTask(recipes_dir))
    return registry
