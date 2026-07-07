"""Base class for startup tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.startup_schemas import StartupTaskInfo


class StartupTask(ABC):
    """One detectable, user-facing startup action.

    `id` must be stable. `one_shot` tasks are recorded in the upgrade-state once
    applied/acknowledged so they don't re-surface; data-driven tasks (the
    default) self-retire when `detect()` returns None.
    """

    id: str
    one_shot: bool = False

    @abstractmethod
    def detect(self, consented: bool = False) -> StartupTaskInfo | None:
        """Read-only. Return a pending task if action is needed, else None.
        MUST NOT mutate anything.

        `consented` is True when the user has already applied this task at least
        once (persisted). A task that still has pending work should then return a
        **non-blocking** notice (`requires_consent=False`) rather than re-gating —
        so an un-resolvable item can't wedge the app on every launch."""
        raise NotImplementedError

    @abstractmethod
    def apply(self) -> dict[str, Any]:
        """Perform the task (with backups where it mutates user files). Returns
        a small result dict for the confirmation message."""
        raise NotImplementedError
