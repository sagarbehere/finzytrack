"""Persistent record of completed one-shot startup tasks.

Stored at `config/.upgrade-state.json`. Data-driven migration tasks don't need
this (they self-retire via detection); one-shot informational/action tasks use
it so they surface only once.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILENAME = ".upgrade-state.json"


class UpgradeState:
    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / STATE_FILENAME
        self._completed: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._completed = set(data.get("completed", []))
        except Exception as e:  # noqa: BLE001 — never block on a bad state file
            logger.warning("Could not read upgrade state %s: %s", self._path, e)
            self._completed = set()

    def is_completed(self, task_id: str) -> bool:
        return task_id in self._completed

    def mark_completed(self, task_id: str) -> None:
        self._completed.add(task_id)
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"completed": sorted(self._completed)}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not write upgrade state %s: %s", self._path, e)
