"""The refresh operation — ownership-aware delivery of bundled seed content.

A single, idempotent, backup-aware pass (the analogue of
`app/migrations/runner.apply_recipe_migration`): walk the bundle and decide, per
file, whether to write it — by **ownership**, which splits into two rules.

**Recipes (dashboards) are user-editable → provenance-protected (§4).** We
overwrite only when we can prove the on-disk copy is exactly what *we* last wrote
(`pristine`); any local change, or no record at all, means hands off. Compares
the on-disk file to **the hash of what we last wrote there** (`installed`), not
to the current bundle — the dpkg-conffiles model:

    | on disk | in installed | on-disk vs installed | state         | action        |
    |---------|--------------|----------------------|---------------|---------------|
    | yes     | yes          | equal                | pristine      | back up→write |
    | yes     | yes          | differ               | user-modified | leave         |
    | yes     | no           | —                    | user-created  | leave         |
    | no      | yes          | —                    | user-deleted  | do not resurrect |
    | no      | no           | —                    | new           | write         |

There is **no force/overwrite mode for recipes**: a dashboard the user created,
edited, or deleted is never touched here, so we can never clobber a user's tweaks
(their loss would send them hunting for a backup). Upgrading a *tweaked* dashboard
to a new format is the migration task's job (it preserves the tweaks); this
refresh only delivers content to demos the user hasn't touched.

**Demo ledgers are app-owned → replaced, not protected.** They are disposable
scaffolding we regenerate with fresh dates each release, not user-authored data
(the user's real ledger is a *different* file we never touch). So we skip the
user-edit test on ledgers entirely and replace the on-disk copy with the current
bundle whenever it differs from what we last delivered — even if a demo-mode user
edited it. The replace is still **consent-gated** (the seed-content notice) and
**backed up** first; and we compare against *last-delivered* (not on-disk), so an
unchanged release never nags and a demo user's exploration isn't wiped every
launch. A ledger deleted after we delivered our current version is not resurrected.

See dev-docs/seed-content-refresh.md §4–§5.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.atomic_backup import atomic_write_text, timestamped_backup
from .bundle import SeedFile, content_digest, walk_bundle

if TYPE_CHECKING:  # avoids a package import cycle (startup_tasks ↔ seed_refresh)
    from app.startup_tasks.upgrade_state import UpgradeState

logger = logging.getLogger(__name__)

# Provenance states (§4).
PRISTINE = "pristine"
USER_MODIFIED = "user-modified"
USER_CREATED = "user-created"
USER_DELETED = "user-deleted"
NEW = "new"

# Per-file actions a refresh plan can decide.
_ADD = "add"                # write a file absent on disk
_REFRESH = "refresh"        # back up + overwrite a pristine file that changed
_BASELINE = "baseline"      # already current: record its hash, no write
_SKIP = "skip"              # user-modified/user-created: leave, report
_IGNORE = "ignore"          # user-deleted: leave, don't report


def classify(on_disk_hash: str | None, installed_hash: str | None) -> str:
    """The §4 state for one file, from (does it exist on disk?, was it in the
    installed record?, and do the hashes match?). `on_disk_hash` is None when the
    file is absent; `installed_hash` is None when we have no record of writing it."""
    if on_disk_hash is not None:
        if installed_hash is None:
            return USER_CREATED
        return PRISTINE if on_disk_hash == installed_hash else USER_MODIFIED
    return USER_DELETED if installed_hash is not None else NEW


@dataclass(frozen=True)
class _Decision:
    """A read-only per-file plan: what to do and everything the writer needs."""
    file: SeedFile
    action: str
    display: str
    target: Path
    target_hash: str


def _decide(
    f: SeedFile, config_dir: Path, data_dir: Path, currency: str,
    installed: dict[str, str],
) -> _Decision:
    target = f.target_path(config_dir, data_dir)
    target_hash = f.target_hash(currency)
    on_disk_hash = _hash_file(target)
    last_delivered = installed.get(f.relpath)

    if f.kind == "ledger":
        # App-owned demo ledger: replace with the current bundle whenever it
        # differs from what we last delivered, WITHOUT the user-edit test — the
        # demo ledger is ours, not user data. Compare target vs last-delivered so
        # an unchanged release is a no-op and a demo user's edits aren't wiped
        # every launch. Don't resurrect one the user deleted (unless we've never
        # delivered it). The replace itself is consent-gated + backed up (apply).
        if target_hash == last_delivered:
            action = _BASELINE
        elif on_disk_hash is None:
            action = _ADD if last_delivered is None else _IGNORE
        else:
            action = _REFRESH
    else:
        # User-editable dashboard: provenance-protected (§4). Overwrite only a
        # provably pristine copy; never a file the user made, changed, or deleted.
        state = classify(on_disk_hash, last_delivered)
        if state == NEW:
            action = _ADD
        elif state == PRISTINE:
            action = _BASELINE if on_disk_hash == target_hash else _REFRESH
        elif state in (USER_MODIFIED, USER_CREATED):
            action = _SKIP
        else:  # USER_DELETED
            action = _IGNORE

    return _Decision(
        file=f, action=action,
        display=f.display_path(config_dir, data_dir),
        target=target, target_hash=target_hash,
    )


@dataclass
class RefreshReport:
    """What a refresh/baseline pass did, in the normalized shape the startup UI
    renders (paths are install-root-relative display strings; `base_dir` anchors
    them). `errors` mirrors `failed` — the registry keys consent off it."""
    added: list[dict] = field(default_factory=list)       # [{path, note}]
    refreshed: list[dict] = field(default_factory=list)    # [{path, note}]
    skipped: list[dict] = field(default_factory=list)      # [{path, note}] left untouched
    failed: list[dict] = field(default_factory=list)       # [{path, reason}]
    base_dir: str = ""
    content_digest: str = ""

    def would_change(self) -> bool:
        """True if a refresh would add or overwrite at least one file (a pure
        re-baseline of already-current files is not a change)."""
        return bool(self.added or self.refreshed)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added {len(self.added)}")
        if self.refreshed:
            parts.append(f"refreshed {len(self.refreshed)}")
        if self.skipped:
            parts.append(f"kept {len(self.skipped)} you'd edited")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ("Demo content: " + ", ".join(parts) + ".") if parts else "Demo content already up to date."

    def to_result(self) -> dict:
        """The `apply()` return shape (base.py): succeeded/failed outcome plus the
        skipped list and `errors` (== failed) for the registry. Notes are relabeled
        to PAST tense here — this describes a completed apply, whereas the same
        item lists carry future-tense notes ("new"/"will refresh") in to_details()
        for the pre-consent preview."""
        succeeded = (
            [{**p, "note": "added"} for p in self.added]
            + [{**p, "note": "refreshed"} for p in self.refreshed]
        )
        return {
            "outcome": {"succeeded": succeeded, "failed": self.failed},
            "skipped": self.skipped,
            "errors": self.failed,
            "baseDir": self.base_dir,
            "summary": self.summary(),
        }

    def to_details(self) -> dict:
        """The `detect()` preview shape: the files that *would* change, classified,
        for the "See details" list shown before the user consents."""
        return {
            "items": self.added + self.refreshed,
            "baseDir": self.base_dir,
            "changed": len(self.added) + len(self.refreshed),
        }


def _base_dir(config_dir: Path) -> str:
    """The absolute install root that both config/ and data/ live under — shown
    once in the UI as the anchor for the display paths."""
    return str(config_dir.resolve().parent)


def _plan(
    config_dir: Path, data_dir: Path, currency: str, installed: dict[str, str],
) -> tuple[list[_Decision], RefreshReport]:
    """Classify every bundled file (read-only). Returns the write plan and a
    report describing it, both derived from the same single bundle walk."""
    files = walk_bundle()
    decisions = [_decide(f, config_dir, data_dir, currency, installed) for f in files]
    report = RefreshReport(base_dir=_base_dir(config_dir), content_digest=content_digest(files))
    for d in decisions:
        if d.action == _ADD:
            report.added.append({"path": d.display, "note": "new"})
        elif d.action == _REFRESH:
            report.refreshed.append({"path": d.display, "note": "will refresh"})
        elif d.action == _SKIP:
            report.skipped.append({"path": d.display, "note": "kept your edits"})
    return decisions, report


def preview_refresh(
    config_dir: Path, data_dir: Path, currency: str, installed: dict[str, str]
) -> RefreshReport:
    """Read-only classification of what a refresh would do — for the detect()
    preview. Reads bundle + on-disk files; writes nothing."""
    _, report = _plan(config_dir, data_dir, currency, installed)
    return report


def apply_seed_refresh(
    state: "UpgradeState",
    config_dir: Path,
    data_dir: Path,
    currency: str,
) -> RefreshReport:
    """Deliver bundled content per the §4 provenance rules: add new files and
    refresh pristine ones; never touch files the user created, edited, or deleted.
    Backs up every file before overwrite, writes atomically, re-records the
    installed hashes + the delivered content-digest, and returns a report.
    Idempotent: a run with nothing to do writes no files and creates no backups.
    Per-file failures are captured in `report.failed`, never raised. There is no
    force/overwrite mode — a user's edits can never be clobbered here."""
    decisions, report = _plan(config_dir, data_dir, currency, state.installed_hashes())

    installed_updates: dict[str, str] = {}
    for d in decisions:
        if d.action == _BASELINE:
            # Already current — record the hash so a later edit is distinguishable
            # from pristine, but touch nothing on disk.
            installed_updates[d.file.relpath] = d.target_hash
            continue
        if d.action not in (_ADD, _REFRESH):
            continue
        try:
            d.target.parent.mkdir(parents=True, exist_ok=True)
            timestamped_backup(d.target, d.target.parent)  # best-effort; None for new files
            atomic_write_text(d.target, d.file.target_bytes(currency).decode("utf-8"))
            installed_updates[d.file.relpath] = d.target_hash
        except Exception as e:  # noqa: BLE001 — best-effort per file; report, don't abort
            logger.warning("Seed refresh: could not write %s — %s", d.target, e)
            report.failed.append({"path": d.display, "reason": str(e)})

    # Keep the outcome honest: a file that failed to write isn't "succeeded".
    failed_paths = {e["path"] for e in report.failed}
    report.added = [p for p in report.added if p["path"] not in failed_paths]
    report.refreshed = [p for p in report.refreshed if p["path"] not in failed_paths]

    state.record_seed_apply(installed_updates, report.content_digest)
    if report.would_change() or report.failed:
        logger.info("Seed refresh (%s): %s", config_dir, report.summary())
    return report


def record_seed_baseline(config_dir: Path, data_dir: Path) -> None:
    """Record the provenance baseline after first-run seeding: the hash of every
    bundled file that now exists on disk, plus the current content-digest — so a
    later release's refresh can tell pristine from edited (§14.3). Without this,
    every first-run-seeded file would later look `user-created` and never refresh.

    Hashes the **actual on-disk bytes** (not a recomputed target), so it's correct
    for every seed path: `demo`/`fresh` write currency-substituted ledgers, while
    `existing` copies the raw fake ledger for troubleshooting — either way the
    baseline records exactly what is there. Reads no user edits; the files were
    just written by first-run seeding."""
    from app.startup_tasks.upgrade_state import UpgradeState  # local: avoid import cycle

    files = walk_bundle()
    installed: dict[str, str] = {}
    for f in files:
        on_disk = _hash_file(f.target_path(config_dir, data_dir))
        if on_disk is not None:
            installed[f.relpath] = on_disk
    UpgradeState(config_dir).record_seed_apply(installed, content_digest(files))
    logger.info("Recorded seed baseline: %d file(s) under %s", len(installed), config_dir)


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return None
