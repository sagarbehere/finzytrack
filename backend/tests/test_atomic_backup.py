"""Unit tests for the shared backup/atomic-write primitives.

These back both BackupManager (central data/backups) and the recipe migration
(in-place backups), so they're tested once here. See app/core/atomic_backup.py.
"""

from pathlib import Path

from app.core.atomic_backup import (
    BACKUP_SUFFIX,
    atomic_write_text,
    backup_filename,
    timestamped_backup,
)


def test_backup_filename_shape():
    name = backup_filename("dash.json")
    assert name.startswith("dash.json.")
    assert name.endswith(BACKUP_SUFFIX)


def test_timestamped_backup_copies_content(tmp_path: Path):
    src = tmp_path / "orig.json"
    src.write_text("hello", encoding="utf-8")
    dest_dir = tmp_path / "baks"

    backup = timestamped_backup(src, dest_dir)

    assert backup is not None
    assert backup.parent == dest_dir
    assert backup.name.startswith("orig.json.")
    assert backup.name.endswith(BACKUP_SUFFIX)
    assert backup.read_text(encoding="utf-8") == "hello"
    assert src.read_text(encoding="utf-8") == "hello"  # original untouched


def test_timestamped_backup_missing_source_returns_none(tmp_path: Path):
    assert timestamped_backup(tmp_path / "nope.json", tmp_path / "baks") is None


def test_timestamped_backup_name_override(tmp_path: Path):
    src = tmp_path / "main.json"
    src.write_text("x", encoding="utf-8")
    backup = timestamped_backup(src, tmp_path / "baks", name="widgets__main.json")
    assert backup is not None
    assert backup.name.startswith("widgets__main.json.")


def test_atomic_write_text_creates_and_roundtrips(tmp_path: Path):
    target = tmp_path / "out.json"
    atomic_write_text(target, "content")
    assert target.read_text(encoding="utf-8") == "content"


def test_atomic_write_text_fully_replaces_shorter(tmp_path: Path):
    target = tmp_path / "out.json"
    atomic_write_text(target, "a long original body")
    atomic_write_text(target, "hi")
    # Full overwrite — no trailing bytes from the longer original remain.
    assert target.read_text(encoding="utf-8") == "hi"


def test_atomic_write_text_leaves_no_temp_files(tmp_path: Path):
    target = tmp_path / "out.json"
    atomic_write_text(target, "x")
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []
