"""Seed-content refresh: the §4 provenance table, backup-aware apply, first-run
baseline, reset, and idempotency (dev-docs/seed-content-refresh.md).

Uses a small *synthetic* bundle (patched over the real seed trees) so the
provenance states can be exercised precisely and fast, independent of the real
demo content.
"""

import hashlib
from pathlib import Path

import pytest

from app.seed_refresh import (
    apply_seed_refresh,
    content_digest,
    preview_refresh,
    record_seed_baseline,
    walk_bundle,
)
from app.seed_refresh import bundle as bundle_mod
from app.seed_refresh.refresh import (
    classify, NEW, PRISTINE, USER_MODIFIED, USER_CREATED, USER_DELETED,
)
from app.startup_tasks.upgrade_state import UpgradeState


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A synthetic bundle patched over the real seed trees, plus empty target
    config/ and data/ dirs. Returns a small handle with helpers."""
    seed_config = tmp_path / "bundle" / "seed_config"
    seed_data = tmp_path / "bundle" / "seed_data"
    (seed_config / "recipes" / "dashboards").mkdir(parents=True)
    (seed_data / "ledgers").mkdir(parents=True)

    (seed_config / "recipes" / "dashboards" / "a.json").write_text('{"id":"a","v":1}\n')
    (seed_config / "recipes" / "dashboards" / "b.json").write_text('{"id":"b","v":1}\n')
    # A ledger carrying the currency placeholder, and one without.
    (seed_data / "ledgers" / "demo.beancount").write_text(
        "2020-01-01 open Assets:Cash {default_currency}\n"
    )
    (seed_data / "ledgers" / "plain.beancount").write_text(
        "2020-01-01 open Assets:Cash INR\n"
    )

    monkeypatch.setattr(bundle_mod, "SEED_CONFIG_DIR", seed_config)
    monkeypatch.setattr(bundle_mod, "SEED_DATA_DIR", seed_data)

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()

    class Handle:
        pass

    h = Handle()
    h.seed_config, h.seed_data = seed_config, seed_data
    h.config_dir, h.data_dir = config_dir, data_dir
    h.currency = "EUR"

    def target(relpath: str) -> Path:
        root = data_dir if relpath.startswith("ledgers/") else config_dir
        return root / relpath

    def seed_all():
        """Simulate first-run seeding: write every bundle file to its target
        (currency-substituted for the placeholder ledger), then baseline."""
        for f in walk_bundle():
            t = target(f.relpath)
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(f.target_bytes(h.currency))
        record_seed_baseline(config_dir, data_dir)

    h.target = target
    h.seed_all = seed_all
    return h


# ── classify() — the §4 table, in isolation ─────────────────────────────────


def test_classify_covers_every_state():
    assert classify("h", "h") == PRISTINE          # on disk, recorded, equal
    assert classify("h2", "h") == USER_MODIFIED     # on disk, recorded, differ
    assert classify("h", None) == USER_CREATED      # on disk, not recorded
    assert classify(None, "h") == USER_DELETED      # absent, recorded
    assert classify(None, None) == NEW              # absent, not recorded


# ── first-run baseline → nothing to refresh ──────────────────────────────────


def test_baseline_makes_everything_pristine(bundle):
    bundle.seed_all()
    state = UpgradeState(bundle.config_dir)
    report = preview_refresh(bundle.config_dir, bundle.data_dir, bundle.currency, state.installed_hashes())
    assert report.would_change() is False
    assert report.added == [] and report.refreshed == []
    # Digest was recorded so the notice self-retires.
    assert state.applied_content_digest() == content_digest(walk_bundle())


# ── new file delivered; unrelated on-disk files preserved ───────────────────


def test_new_file_is_added_and_recorded(bundle):
    # Only a.json + the two ledgers on disk & baselined; b.json is "new".
    for rel in ("recipes/dashboards/a.json", "ledgers/demo.beancount", "ledgers/plain.beancount"):
        t = bundle.target(rel)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(next(f for f in walk_bundle() if f.relpath == rel).target_bytes(bundle.currency))
    record_seed_baseline(bundle.config_dir, bundle.data_dir)

    state = UpgradeState(bundle.config_dir)
    report = apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)

    assert bundle.target("recipes/dashboards/b.json").is_file()
    assert [p["path"] for p in report.added] == ["config/recipes/dashboards/b.json"]
    assert report.refreshed == []
    assert UpgradeState(bundle.config_dir).installed_hashes()["recipes/dashboards/b.json"] == \
        _sha(bundle.target("recipes/dashboards/b.json"))


# ── pristine refresh backs up then overwrites ────────────────────────────────


def test_pristine_file_is_refreshed_with_backup(bundle):
    bundle.seed_all()
    # A new release improves a.json in the bundle.
    (bundle.seed_config / "recipes" / "dashboards" / "a.json").write_text('{"id":"a","v":2}\n')

    state = UpgradeState(bundle.config_dir)
    report = apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)

    assert [p["path"] for p in report.refreshed] == ["config/recipes/dashboards/a.json"]
    assert bundle.target("recipes/dashboards/a.json").read_text() == '{"id":"a","v":2}\n'
    # A timestamped backup of the previous version sits beside it.
    backups = list((bundle.config_dir / "recipes" / "dashboards").glob("a.json.*.backup"))
    assert len(backups) == 1
    assert backups[0].read_text() == '{"id":"a","v":1}\n'


# ── user edits are never clobbered ───────────────────────────────────────────


def test_user_modified_file_is_left_alone(bundle):
    bundle.seed_all()
    edited = bundle.target("recipes/dashboards/a.json")
    edited.write_text('{"id":"a","MINE":true}\n')
    # Even though the bundle also changed a.json, the user's edit wins.
    (bundle.seed_config / "recipes" / "dashboards" / "a.json").write_text('{"id":"a","v":9}\n')

    state = UpgradeState(bundle.config_dir)
    report = apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)

    assert edited.read_text() == '{"id":"a","MINE":true}\n'   # untouched
    assert [p["path"] for p in report.skipped] == ["config/recipes/dashboards/a.json"]
    assert report.refreshed == []


def test_user_deleted_file_is_not_resurrected(bundle):
    bundle.seed_all()
    deleted = bundle.target("recipes/dashboards/a.json")
    deleted.unlink()

    state = UpgradeState(bundle.config_dir)
    report = apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)

    assert not deleted.exists()                 # stays gone
    assert report.added == [] and report.refreshed == []


# ── idempotency ──────────────────────────────────────────────────────────────


def test_reapply_is_a_noop_without_backup_churn(bundle):
    bundle.seed_all()
    state = UpgradeState(bundle.config_dir)
    apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)  # nothing pending

    def backups():
        return list(bundle.config_dir.rglob("*.backup")) + list(bundle.data_dir.rglob("*.backup"))

    assert backups() == []
    report = apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)
    assert report.would_change() is False
    assert backups() == []   # no writes → no backups


# ── currency substitution + hash agreement ───────────────────────────────────


def test_ledger_is_currency_substituted_and_hash_matches(bundle):
    state = UpgradeState(bundle.config_dir)
    apply_seed_refresh(state, bundle.config_dir, bundle.data_dir, bundle.currency)  # first delivery

    demo = bundle.target("ledgers/demo.beancount")
    assert "{default_currency}" not in demo.read_text()
    assert "EUR" in demo.read_text()
    # The recorded installed hash is the post-substitution hash (so the file reads
    # pristine, not user-modified, on the next run).
    assert UpgradeState(bundle.config_dir).installed_hashes()["ledgers/demo.beancount"] == _sha(demo)
    follow = preview_refresh(bundle.config_dir, bundle.data_dir, bundle.currency,
                             UpgradeState(bundle.config_dir).installed_hashes())
    assert follow.would_change() is False


# ── demo ledgers are app-owned: replaced, not protected ──────────────────────


def _bundle_ledger_bytes(relpath, currency):
    f = next(x for x in walk_bundle() if x.relpath == relpath)
    return f.target_bytes(currency)


def test_user_modified_demo_ledger_is_replaced(bundle):
    """Unlike a dashboard, an edited demo ledger is NOT protected — a newer bundle
    replaces the on-disk copy (backing it up first), because the demo ledger is
    ours, not user data."""
    bundle.seed_all()
    edited = bundle.target("ledgers/plain.beancount")
    edited.write_text("2020-01-01 open Assets:Cash INR\n; my note\n")
    # A new release ships a different demo ledger.
    (bundle.seed_data / "ledgers" / "plain.beancount").write_text("2021-02-02 open Assets:New INR\n")

    report = apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)

    assert edited.read_text() == "2021-02-02 open Assets:New INR\n"     # replaced with bundle
    assert [p["path"] for p in report.refreshed] == ["data/ledgers/plain.beancount"]
    assert report.skipped == []                                         # never "kept your edits"
    backups = list(bundle.data_dir.rglob("*.backup"))
    assert any(b.read_text() == "2020-01-01 open Assets:Cash INR\n; my note\n" for b in backups)


def test_untracked_demo_ledger_is_refreshed(bundle):
    """The reported bug: a demo ledger seeded before provenance existed has NO
    installed record. It must still be replaced with the current bundle (it's
    ours) — not skipped as 'user-created' the way an untracked dashboard is."""
    stale = bundle.target("ledgers/plain.beancount")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("2019-01-01 open Assets:Old INR\n")               # old seeded copy, nothing recorded

    report = apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)

    assert stale.read_bytes() == _bundle_ledger_bytes("ledgers/plain.beancount", bundle.currency)
    assert "data/ledgers/plain.beancount" in [p["path"] for p in report.refreshed]


def test_unchanged_release_keeps_demo_ledger_edit(bundle):
    """We compare against last-delivered, not on-disk — so a demo user's edits are
    NOT wiped on every launch; only a genuinely newer bundle replaces them."""
    bundle.seed_all()
    edited = bundle.target("ledgers/plain.beancount")
    edited.write_text("2020-01-01 open Assets:Cash INR\n; exploring\n")

    report = apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)

    assert edited.read_text() == "2020-01-01 open Assets:Cash INR\n; exploring\n"   # kept — bundle unchanged
    assert report.would_change() is False


def test_result_notes_are_past_tense_preview_future(bundle):
    """The apply RESULT describes completed actions in past tense (added/refreshed);
    the pre-consent preview (to_details) keeps future tense (new/will refresh)."""
    bundle.seed_all()
    (bundle.seed_config / "recipes" / "dashboards" / "c.json").write_text('{"id":"c"}\n')   # new dashboard
    (bundle.seed_data / "ledgers" / "plain.beancount").write_text("2099-01-01 open Assets:X INR\n")  # newer ledger

    report = apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)

    result_notes = {p["path"]: p["note"] for p in report.to_result()["outcome"]["succeeded"]}
    assert result_notes["config/recipes/dashboards/c.json"] == "added"
    assert result_notes["data/ledgers/plain.beancount"] == "refreshed"

    preview_notes = {p["path"]: p["note"] for p in report.to_details()["items"]}
    assert preview_notes["config/recipes/dashboards/c.json"] == "new"
    assert preview_notes["data/ledgers/plain.beancount"] == "will refresh"


# ── there is no force/overwrite path (for recipes) ───────────────────────────


def test_apply_never_overwrites_a_user_edit(bundle):
    """The core has no reset/force mode — a modified file is preserved on every
    apply, no matter how many times a newer bundle changes it."""
    bundle.seed_all()
    edited = bundle.target("recipes/dashboards/a.json")
    edited.write_text('{"id":"a","MINE":true}\n')
    for v in (2, 3, 4):  # successive releases keep changing the bundled a.json
        (bundle.seed_config / "recipes" / "dashboards" / "a.json").write_text(f'{{"id":"a","v":{v}}}\n')
        apply_seed_refresh(UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency)
        assert edited.read_text() == '{"id":"a","MINE":true}\n'   # untouched every time


# ── state file schema (D2) ───────────────────────────────────────────────────


def test_apply_writes_nested_v1_state(bundle):
    import json
    bundle.seed_all()
    raw = json.loads((bundle.config_dir / ".upgrade-state.json").read_text())
    assert raw["schemaVersion"] == 1
    assert set(raw["tasks"]) == {"completed", "consented"}
    assert raw["seed"]["appliedContentDigest"] == content_digest(walk_bundle())
    assert set(raw["seed"]["installed"]) == {f.relpath for f in walk_bundle()}


def test_legacy_flat_state_is_migrated_in_place(tmp_path):
    """A pre-seed `.upgrade-state.json` (flat {completed, consented}) is read, its
    task records preserved, and the file rewritten to the nested v1 shape once a
    seed record is added (D2)."""
    import json
    config = tmp_path / "config"
    config.mkdir()
    (config / ".upgrade-state.json").write_text(
        json.dumps({"completed": ["old-notice"], "consented": ["recipes-upgrade"]})
    )
    state = UpgradeState(config)
    assert state.is_completed("old-notice")
    assert state.is_consented("recipes-upgrade")

    state.record_seed_apply({"recipes/x.json": "h"}, "digest123")

    raw = json.loads((config / ".upgrade-state.json").read_text())
    assert raw["schemaVersion"] == 1
    assert raw["tasks"]["completed"] == ["old-notice"]
    assert raw["tasks"]["consented"] == ["recipes-upgrade"]
    assert raw["seed"]["installed"] == {"recipes/x.json": "h"}
    assert raw["seed"]["appliedContentDigest"] == "digest123"


# ── SeedContentTask (the info notice) ────────────────────────────────────────


def _seed_task(bundle, setup_complete=True):
    from app.startup_tasks.tasks.seed_content_task import SeedContentTask
    state = UpgradeState(bundle.config_dir)
    return state, SeedContentTask(state, bundle.config_dir, bundle.data_dir, bundle.currency, setup_complete)


def test_task_silent_until_setup_complete(bundle):
    _, task = _seed_task(bundle, setup_complete=False)
    assert task.detect() is None   # pending content, but setup not done → quiet


def test_task_surfaces_info_notice_read_only(bundle):
    from app.schemas.startup_schemas import SEVERITY_INFO
    before = {p: p.read_bytes() for p in bundle.seed_config.rglob("*") if p.is_file()}
    _, task = _seed_task(bundle)
    info = task.detect()
    assert info is not None
    assert info.severity == SEVERITY_INFO
    assert info.requires_consent is False
    assert info.details["changed"] > 0
    # detect() mutated nothing on disk.
    assert {p: p.read_bytes() for p in bundle.seed_config.rglob("*") if p.is_file()} == before


def test_task_retires_once_current(bundle):
    bundle.seed_all()
    _, task = _seed_task(bundle)
    assert task.detect() is None


def test_task_apply_delivers_then_retires(bundle):
    state, task = _seed_task(bundle)
    result = task.apply()
    assert result["outcome"]["succeeded"]          # something delivered
    assert result["errors"] == []
    # Fresh task over the same state now finds nothing pending.
    _, task2 = _seed_task(bundle)
    assert task2.detect() is None


def test_task_snooze_suppresses_then_new_content_refires(bundle):
    state, task = _seed_task(bundle)
    assert task.detect() is not None
    task.snooze()
    # Re-read state (snooze persisted) and re-detect → suppressed.
    _, task2 = _seed_task(bundle)
    assert task2.detect() is None
    # A new release changes the bundle → different digest → notice returns.
    (bundle.seed_config / "recipes" / "dashboards" / "a.json").write_text('{"id":"a","v":2}\n')
    _, task3 = _seed_task(bundle)
    assert task3.detect() is not None


def test_registry_dismiss_snoozes_seed_task(bundle):
    from app.startup_tasks.registry import StartupTaskRegistry
    state, task = _seed_task(bundle)
    reg = StartupTaskRegistry(state)
    reg.register(task)
    assert len(reg.detect()) == 1
    reg.dismiss("seed-content")
    # Snooze persisted → a fresh registry over the same dir sees nothing.
    _, task2 = _seed_task(bundle)
    reg2 = StartupTaskRegistry(UpgradeState(bundle.config_dir))
    reg2.register(task2)
    assert reg2.detect() == []


def test_reopen_dismissed_resurfaces_snoozed_notice(bundle):
    from app.startup_tasks.registry import StartupTaskRegistry
    # Dismiss → snoozed → nothing pending.
    state, task = _seed_task(bundle)
    reg = StartupTaskRegistry(state)
    reg.register(task)
    reg.dismiss("seed-content")
    assert reg.detect() == []

    # Re-open (undo the accidental dismiss) → the notice comes back.
    _, task2 = _seed_task(bundle)
    reg2 = StartupTaskRegistry(UpgradeState(bundle.config_dir))
    reg2.register(task2)
    reg2.reopen_dismissed()
    assert [t.id for t in reg2.detect()] == ["seed-content"]


def test_task_reopen_clears_snooze(bundle):
    state, task = _seed_task(bundle)
    task.snooze()
    assert task.detect() is None
    task.reopen()
    assert task.detect() is not None


# ── Endpoint round-trip ──────────────────────────────────────────────────────


def test_reopen_endpoint_returns_pending_tasks(test_client):
    resp = test_client.post("/api/startup/notices/reopen")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"]["tasks"], list)


def test_dismiss_unknown_task_is_404(test_client):
    from app import error_codes as ec
    resp = test_client.post("/api/startup/tasks/does-not-exist/dismiss")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == ec.STARTUP_TASK_NOT_FOUND


# ── Dashboard themes: user-editable content, delivered + provenance-protected ──


def _add_theme_to_bundle(bundle, body='{"id":"dusty","v":1}\n'):
    d = bundle.seed_config / "dashboard-themes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "dusty.json").write_text(body)
    return "dashboard-themes/dusty.json"


def test_dashboard_theme_is_walked_and_targets_config(bundle):
    rel = _add_theme_to_bundle(bundle)
    sf = next(f for f in walk_bundle() if f.relpath == rel)
    assert sf.kind == "dashboard-theme"
    # User-editable → lands under config/, never data/.
    assert sf.target_path(bundle.config_dir, bundle.data_dir) == bundle.config_dir / rel


def test_dashboard_theme_delivered_to_existing_install(bundle):
    # Install seeded before the theme shipped (baseline has no theme entry).
    bundle.seed_all()
    rel = _add_theme_to_bundle(bundle)  # a later release adds the theme file
    report = apply_seed_refresh(
        UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency
    )
    assert (bundle.config_dir / rel).read_text() == '{"id":"dusty","v":1}\n'
    assert "config/dashboard-themes/dusty.json" in [p["path"] for p in report.added]


def test_user_edited_theme_is_never_clobbered(bundle):
    _add_theme_to_bundle(bundle)
    bundle.seed_all()  # baselines the theme as pristine
    edited = bundle.config_dir / "dashboard-themes" / "dusty.json"
    edited.write_text('{"id":"dusty","MINE":true}\n')
    # A later release improves the bundled theme — the user's edit still wins.
    _add_theme_to_bundle(bundle, '{"id":"dusty","v":2}\n')
    report = apply_seed_refresh(
        UpgradeState(bundle.config_dir), bundle.config_dir, bundle.data_dir, bundle.currency
    )
    assert edited.read_text() == '{"id":"dusty","MINE":true}\n'  # untouched
    assert "config/dashboard-themes/dusty.json" in [p["path"] for p in report.skipped]
