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

    Normalized shapes the startup modal (StartupGate.vue) renders generically —
    fill these so any task gets the "See details / N succeeded, M failed" UI for
    free. They live inside the untyped `details`/`result` dicts, so no wire-schema
    change is needed:

      detect() → StartupTaskInfo.details["items"] = [{"path": str, "note"?: str}]
          the affected files, shown behind "See details" before the user consents.

      apply()  → result["outcome"] = {
          "succeeded": [{"path": str, "note"?: str}],
          "failed":    [{"path": str, "reason": str}],   # same list as result["errors"]
      }
          what happened, shown after applying. Keep also returning `result["errors"]`
          (the failed list) — the registry uses it to record consent on partial
          failure.
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
