"""Persistent housekeeping state for keeping an install current.

Stored at `config/.upgrade-state.json`. This one file holds everything the
detect→consent→apply framework needs to remember across launches:

  - **tasks** — one-shot/consent bookkeeping for startup tasks (see below).
  - **seed** — provenance for bundled seed content (recipes + demo ledgers): the
    hash of exactly what we last wrote to the user's disk, so a later release can
    tell a *pristine* bundled file (safe to refresh) from one the user edited
    (never touch). See dev-docs/seed-content-refresh.md §4 (D2: one state file,
    namespaced — no separate `.seed-state.json`).

The write is **durable and atomic** (via `atomic_backup.atomic_write_text`): a
crash mid-write can't leave a truncated file that `_read_disk` would discard
whole, re-firing every completed one-shot. Before each write the on-disk state
is re-read and merged in, so a concurrently-running instance's records aren't
clobbered (best-effort — not a lock; see the upgrades doc for the race window).

**Schema (v1, nested):**

    {
      "schemaVersion": 1,
      "tasks": {"completed": [...], "consented": [...]},
      "seed":  {"appliedContentDigest": "...",
                "installed": {"recipes/...": "sha", "ledgers/...": "sha"},
                "dismissed": {"contentDigest": "..."}}
    }

The **legacy** flat shape `{"completed": [...], "consented": [...]}` (pre-seed)
is migrated in place on first read — old tasks records are lifted into `tasks`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.atomic_backup import atomic_write_text

logger = logging.getLogger(__name__)

STATE_FILENAME = ".upgrade-state.json"
SCHEMA_VERSION = 1


class UpgradeState:
    """Durable, merge-safe record of task bookkeeping and seed provenance.

    Task id-sets:
      - `completed` — one-shot tasks that have run (so they surface only once).
      - `consented` — data-driven tasks the user has already applied at least
        once; a consented task that still detects pending work downgrades from a
        hard gate to a dismissible notice, so one broken file can't wedge the app.

    Seed provenance (`seed`):
      - `appliedContentDigest` — the bundle content-digest last delivered; the
        seed-content notice fires only while the current bundle digest differs.
      - `installed` — path → hash of exactly what we last wrote there (recipes
        AND ledgers; a ledger hash is the post-`{default_currency}`-substitution
        hash). Distinguishes pristine from user-modified (see refresh §4).
      - `dismissed.contentDigest` — the digest the user snoozed, so the notice
        doesn't re-nag every launch until a later release changes the digest.
    """

    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / STATE_FILENAME
        disk = self._read_disk()
        self._completed: set[str] = disk["completed"]
        self._consented: set[str] = disk["consented"]
        self._seed: dict[str, Any] = disk["seed"]

    # ── reading ────────────────────────────────────────────────────────────

    def _read_disk(self) -> dict[str, Any]:
        """The state currently on disk, normalized to the v1 shape, or empty if
        absent/unreadable. A bad file is never fatal — it degrades to 'nothing
        recorded' (which can re-fire a one-shot / re-show the seed notice, but
        never blocks startup). Understands both the nested v1 shape and the
        legacy flat `{completed, consented}` shape."""
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # v1 nests task sets under "tasks"; legacy kept them top-level.
                tasks = data.get("tasks", data)
                return {
                    "completed": set(tasks.get("completed", [])),
                    "consented": set(tasks.get("consented", [])),
                    "seed": _normalize_seed(data.get("seed", {})),
                }
        except Exception as e:  # noqa: BLE001 — never block on a bad state file
            logger.warning("Could not read upgrade state %s: %s", self._path, e)
        return {"completed": set(), "consented": set(), "seed": _empty_seed()}

    # ── task bookkeeping ───────────────────────────────────────────────────

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

    # ── seed provenance ────────────────────────────────────────────────────

    def get_seed(self) -> dict[str, Any]:
        """A read-only snapshot of the seed provenance state (copied so callers
        can't mutate the in-memory record without going through the setters)."""
        return {
            "appliedContentDigest": self._seed.get("appliedContentDigest"),
            "installed": dict(self._seed.get("installed", {})),
            "dismissed": dict(self._seed.get("dismissed", {})),
        }

    def applied_content_digest(self) -> str | None:
        return self._seed.get("appliedContentDigest")

    def installed_hashes(self) -> dict[str, str]:
        return dict(self._seed.get("installed", {}))

    def dismissed_content_digest(self) -> str | None:
        return self._seed.get("dismissed", {}).get("contentDigest")

    def record_seed_apply(
        self, installed_updates: dict[str, str], applied_content_digest: str
    ) -> None:
        """Record the result of a first-run seed or a refresh apply in one write:
        merge the newly-written files' hashes into `installed` and stamp the
        delivered `appliedContentDigest`. One persist, no per-file churn."""
        self._seed.setdefault("installed", {}).update(installed_updates)
        self._seed["appliedContentDigest"] = applied_content_digest
        self._persist("seed content applied")

    def record_seed_installed(self, installed_updates: dict[str, str]) -> None:
        """Merge freshly-written file hashes into `installed` **without** advancing
        `appliedContentDigest`. Used by the registry's post-migration re-baseline
        (dev-docs/seed-content-refresh.md §10.1): a format migration rewrote a
        *pristine* seeded file, so we update our record of what's on disk — but we
        must NOT mark bundle content as applied, or the seed-content notice would
        stop offering the (now-pristine) files their colour/format refresh. A no-op
        when there's nothing to record."""
        if not installed_updates:
            return
        self._seed.setdefault("installed", {}).update(installed_updates)
        self._persist("seed provenance re-baselined")

    def snooze_seed(self, content_digest: str) -> None:
        """Record that the user dismissed the seed-content notice for this bundle
        digest, so it doesn't re-nag until a later release changes the digest."""
        self._seed.setdefault("dismissed", {})["contentDigest"] = content_digest
        self._persist("seed content notice snoozed")

    def clear_seed_snooze(self) -> None:
        """Forget any seed-content snooze, so a dismissed notice re-surfaces on the
        next detect. Drives Settings → "Show dismissed notices" (undo an accidental
        dismiss). A no-op if nothing was snoozed."""
        self._seed.setdefault("dismissed", {})["contentDigest"] = None
        self._persist("seed content snooze cleared")

    # ── persistence ────────────────────────────────────────────────────────

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
        """Durably write the full v1 state, merged with whatever is on disk.

        Raises on write failure (the caller handles it) so a silent failure
        can't masquerade as a persisted record. Merge policy: task id-sets union
        with disk; seed `installed` unions with disk (this instance wins on a key
        clash); `appliedContentDigest`/`dismissed` take this instance's value
        when set, else disk's — so a concurrent writer's records aren't lost."""
        disk = self._read_disk()
        self._completed |= disk["completed"]
        self._consented |= disk["consented"]

        disk_seed = disk["seed"]
        merged_installed = {**disk_seed.get("installed", {}), **self._seed.get("installed", {})}
        merged_digest = self._seed.get("appliedContentDigest") or disk_seed.get("appliedContentDigest")
        # This instance's snooze value wins (it may be an explicit *clear* to None,
        # which must stick — a `... or disk` fallback would resurrect the old
        # snooze and defeat "Show dismissed notices").
        merged_dismissed = self._seed.get("dismissed", {}).get("contentDigest")
        self._seed = {
            "appliedContentDigest": merged_digest,
            "installed": merged_installed,
            "dismissed": {"contentDigest": merged_dismissed},
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._path,
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "tasks": {
                        "completed": sorted(self._completed),
                        "consented": sorted(self._consented),
                    },
                    "seed": {
                        "appliedContentDigest": self._seed["appliedContentDigest"],
                        "installed": dict(sorted(self._seed["installed"].items())),
                        "dismissed": self._seed["dismissed"],
                    },
                },
                indent=2,
            ) + "\n",
        )


def _empty_seed() -> dict[str, Any]:
    return {"appliedContentDigest": None, "installed": {}, "dismissed": {"contentDigest": None}}


def _normalize_seed(seed: dict[str, Any]) -> dict[str, Any]:
    """Coerce a possibly-partial on-disk seed section to the full in-memory shape
    (tolerates a hand-edited or older file missing keys)."""
    if not isinstance(seed, dict):
        return _empty_seed()
    installed = seed.get("installed", {})
    dismissed = seed.get("dismissed", {})
    return {
        "appliedContentDigest": seed.get("appliedContentDigest"),
        "installed": dict(installed) if isinstance(installed, dict) else {},
        "dismissed": {
            "contentDigest": dismissed.get("contentDigest") if isinstance(dismissed, dict) else None
        },
    }
