"""Startup-task framework — inform → consent → apply for upgrades and notices.

At launch the frontend asks the backend what's pending (read-only detection).
Any `action_required` task (e.g. the recipe-format migration) gates the app
behind a dialog that explains the change and links to the Upgrade Notes; only
when the user consents does the backend apply it (with backups). Informational
tasks surface as dismissible notices.

The framework is generic: future breaking changes register a task with the same
detect/apply contract. Tasks retire automatically —
  - data-driven tasks (migrations) stop surfacing once `detect()` finds nothing,
  - one-shot tasks are recorded in the upgrade-state once acknowledged.

See dev-docs/upgrades.md.
"""

from .base import StartupTask
from .registry import build_startup_registry

__all__ = ["StartupTask", "build_startup_registry"]
