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
    """Two persisted id-sets:
      - `completed` — one-shot tasks that have run (so they surface only once).
      - `consented` — data-driven tasks the user has already applied at least
        once. A consented task that still detects pending work (e.g. a migration
        that left un-convertible files) downgrades from a hard gate to a
        dismissible notice, so one broken file can't wedge the app forever.
    """

    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / STATE_FILENAME
        disk = self._read_disk()
        self._completed: set[str] = disk["completed"]
        self._consented: set[str] = disk["consented"]

    def _read_disk(self) -> dict[str, set[str]]:
        """The id-sets currently on disk, or empty if absent/unreadable. A bad
        file is never fatal — it degrades to 'nothing recorded' (which can re-fire
        a one-shot / re-gate a migration, but never blocks startup)."""
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return {
                    "completed": set(data.get("completed", [])),
                    "consented": set(data.get("consented", [])),
                }
        except Exception as e:  # noqa: BLE001 — never block on a bad state file
            logger.warning("Could not read upgrade state %s: %s", self._path, e)
        return {"completed": set(), "consented": set()}

    def is_completed(self, task_id: str) -> bool:
        return task_id in self._completed

    def mark_completed(self, task_id: str) -> None:
        self._completed.add(task_id)
        self._persist(f"one-shot task '{task_id}' completed")

    def is_consented(self, task_id: str) -> bool:
        return task_id in self._consented

    def mark_consented(self, task_id: str) -> None:
        self._consented.add(task_id)
        self._persist(f"consent for task '{task_id}'")

    def _persist(self, what: str) -> None:
        try:
            self._save()
            logger.info("Recorded %s", what)
        except Exception as e:  # noqa: BLE001 — persistence failure must not fail apply
            # Loud, and named, so the audit trail shows it will re-surface next
            # launch (nothing was persisted).
            logger.warning(
                "Could not persist %s to %s — it will re-surface on next launch: %s",
                what, self._path, e,
            )

    def _save(self) -> None:
        """Durably write both id-sets, merged with whatever is on disk.

        Raises on write failure (the caller handles it) so a silent failure
        can't masquerade as a persisted record."""
        disk = self._read_disk()
        self._completed |= disk["completed"]
        self._consented |= disk["consented"]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._path,
            json.dumps(
                {"completed": sorted(self._completed), "consented": sorted(self._consented)},
                indent=2,
            ) + "\n",
        )
