"""BackupManager path-namespacing: backups are keyed by source *path*, not by
basename, so two files sharing a name keep independent retention buckets.

Pre-fix, a flat backup dir keyed on ``file_path.name`` meant a dashboard and a
widget both named ``spending.json`` shared one bucket, so one's writes could
prune the other's backups. These tests pin the fix. See BackupManager.
"""

from pathlib import Path

from app.core.backup_manager import BackupManager, BackupError


def _overwrite(bm: BackupManager, path: Path, text: str) -> None:
    with bm.atomic_write(str(path)) as f:
        f.truncate()
        f.write(text)


def test_same_basename_different_dirs_do_not_collide(tmp_path: Path):
    root = tmp_path / "root"
    bdir = tmp_path / "backups"
    bm = BackupManager(backup_dir=bdir, retention_count=10, base_dir=root)

    dash = root / "config" / "recipes" / "dashboards" / "spending.json"
    widget = root / "config" / "recipes" / "widgets" / "spending.json"
    for p in (dash, widget):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("v0", encoding="utf-8")

    # Two overwrites each → two backups each (the first snapshots the seeded file).
    for i in range(1, 3):
        _overwrite(bm, dash, f"d{i}")
        _overwrite(bm, widget, f"w{i}")

    dash_baks = list((bdir / "config" / "recipes" / "dashboards").glob("spending.json.*.backup"))
    widget_baks = list((bdir / "config" / "recipes" / "widgets").glob("spending.json.*.backup"))
    assert len(dash_baks) == 2
    assert len(widget_baks) == 2
    # Nothing landed in the flat root of the backup dir — all namespaced.
    assert list(bdir.glob("spending.json.*.backup")) == []


def test_retention_is_independent_per_source_path(tmp_path: Path):
    root = tmp_path / "root"
    bdir = tmp_path / "backups"
    bm = BackupManager(backup_dir=bdir, retention_count=2, base_dir=root)

    a = root / "x" / "f.json"
    b = root / "y" / "f.json"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("seed", encoding="utf-8")

    for i in range(5):
        _overwrite(bm, a, f"a{i}")
    for i in range(5):
        _overwrite(bm, b, f"b{i}")

    # Each path pruned to its own retention_count; a's churn never touches b.
    assert len(list((bdir / "x").glob("f.json.*.backup"))) == 2
    assert len(list((bdir / "y").glob("f.json.*.backup"))) == 2


def test_file_outside_base_dir_falls_back_to_flat(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    bdir = tmp_path / "backups"
    bm = BackupManager(backup_dir=bdir, retention_count=5, base_dir=root)

    outside = tmp_path / "elsewhere" / "ledger.beancount"
    outside.parent.mkdir(parents=True)
    outside.write_text("x", encoding="utf-8")

    bm._create_backup(outside)
    assert list(bdir.glob("ledger.beancount.*.backup"))  # flat fallback


def test_no_base_dir_keeps_flat_layout(tmp_path: Path):
    bdir = tmp_path / "backups"
    bm = BackupManager(backup_dir=bdir, retention_count=5)  # base_dir omitted

    f = tmp_path / "sub" / "config.yaml"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")

    bm._create_backup(f)
    assert list(bdir.glob("config.yaml.*.backup"))  # flat, subdir ignored


def test_create_backup_of_missing_file_raises(tmp_path: Path):
    bm = BackupManager(backup_dir=tmp_path / "backups", retention_count=5)
    try:
        bm._create_backup(tmp_path / "ghost.json")
    except BackupError:
        return
    raise AssertionError("expected BackupError for a missing source file")
