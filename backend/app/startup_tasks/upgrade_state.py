"""Persistent record of completed one-shot startup tasks.

Stored at `config/.upgrade-state.json`. Data-driven migration tasks don't need
this (they self-retire via detection); one-shot informational/action tasks use
it so they surface only once.

The write is **durable and atomic** (via `atomic_backup.atomic_write_text`): a
crash mid-write can't leave a truncated file that `_load` would discard whole,
re-firing every completed one-shot. Before each write the on-disk set is
re-read and merged in, so a concurrently-running instance's completions aren't
clobbered (best-effort — not a lock; see the class docstring in the upgrades
doc for the remaining race window).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.atomic_backup import atomic_write_text

logger = logging.getLogger(__name__)

STATE_FILENAME = ".upgrade-state.json"


class UpgradeState:
    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / STATE_FILENAME
        self._completed: set[str] = self._read_disk()

    def _read_disk(self) -> set[str]:
        """The completed-id set currently on disk, or empty if absent/unreadable.
        A bad file is never fatal — it degrades to 'nothing completed' (which can
        re-fire one-shots, but never blocks startup)."""
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return set(data.get("completed", []))
        except Exception as e:  # noqa: BLE001 — never block on a bad state file
            logger.warning("Could not read upgrade state %s: %s", self._path, e)
        return set()

    def is_completed(self, task_id: str) -> bool:
        return task_id in self._completed

    def mark_completed(self, task_id: str) -> None:
        self._completed.add(task_id)
        try:
            self._save()
            logger.info("Recorded one-shot startup task '%s' as completed", task_id)
        except Exception as e:  # noqa: BLE001 — persistence failure must not fail apply
            # Loud, and named to the specific task, so the audit trail shows the
            # task will re-surface next launch (nothing was persisted).
            logger.warning(
                "Could not persist completion of one-shot task '%s' to %s — it "
                "will re-surface on next launch: %s", task_id, self._path, e
            )

    def _save(self) -> None:
        """Durably write the completed set, merged with whatever is on disk.

        Raises on write failure (mark_completed handles it) so a silent failure
        can't masquerade as a persisted completion."""
        merged = self._completed | self._read_disk()
        self._completed = merged
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._path,
            json.dumps({"completed": sorted(merged)}, indent=2) + "\n",
        )
