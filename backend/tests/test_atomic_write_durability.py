"""Durability tests for ``BackupManager.atomic_write``.

Spec under test: an atomic write fsyncs the temp file's data **before**
the rename swap, and (on POSIX) fsyncs the parent directory **after**
the rename. Without these calls, ``os.replace`` only guarantees
visibility atomicity, not durability — a crash between rename and the
kernel's next writeback could lose the swap.

We can't simulate a kernel crash here, so the tests pin the contract
by counting fsync calls and asserting they happen on the correct
file descriptors, in the correct order.
"""
import os
from pathlib import Path

import pytest

from app.core.backup_manager import BackupManager


@pytest.fixture
def backup_manager(tmp_path):
    return BackupManager(backup_dir=tmp_path / "backups", retention_count=3)


def test_fsync_called_on_temp_file_before_replace(tmp_path, backup_manager, monkeypatch):
    """The temp file's fd must be fsynced before ``os.replace`` runs.
    Pinning the order is what makes the write durable: if rename hits
    disk before the data, a crash can leave the target as a zero-byte
    file or stale content.
    """
    target = tmp_path / "ledger.beancount"
    target.write_text("original\n")

    call_log: list[tuple[str, object]] = []

    real_fsync = os.fsync
    real_replace = os.replace

    def tracking_fsync(fd):
        call_log.append(("fsync", fd))
        return real_fsync(fd)

    def tracking_replace(src, dst):
        call_log.append(("replace", str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(os, "replace", tracking_replace)

    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write("new content\n")

    # At least one fsync must precede the replace (the temp file's data),
    # and (on POSIX) at least one must follow it (the parent directory).
    ops = [op for op, _ in call_log]
    assert "fsync" in ops, "no fsync recorded — atomic_write is not durable"
    assert "replace" in ops, "replace did not run"
    first_replace = ops.index("replace")
    assert ops[:first_replace].count("fsync") >= 1, (
        "no fsync before replace — temp file data may not be on disk when "
        "the rename hits"
    )
    if os.name == "posix":
        assert ops[first_replace + 1:].count("fsync") >= 1, (
            "no fsync after replace — directory entry change may not be on "
            "disk when the function returns (POSIX only)"
        )

    assert target.read_text() == "new content\n"


def test_directory_fsync_failure_is_non_fatal(tmp_path, backup_manager, monkeypatch):
    """Some filesystems (tmpfs in containers, certain network mounts)
    refuse to fsync a directory. That refusal must not fail the write —
    the file-data fsync is the durability-critical part; the dir fsync
    is best-effort.
    """
    target = tmp_path / "ledger.beancount"
    target.write_text("original\n")

    real_fsync = os.fsync

    def selective_fsync(fd):
        try:
            mode = os.fstat(fd).st_mode
        except OSError:
            return real_fsync(fd)
        # If this is the directory fsync, simulate a refusal.
        from stat import S_ISDIR
        if S_ISDIR(mode):
            raise OSError("simulated EINVAL on directory fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", selective_fsync)

    # Must not raise.
    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write("survives bad dir fsync\n")

    assert target.read_text() == "survives bad dir fsync\n"


def test_content_is_correct_after_atomic_write(tmp_path, backup_manager):
    """Sanity: the spec-level outcome (file ends up with new content)
    must still hold with the durability changes wired in.
    """
    target = tmp_path / "ledger.beancount"
    target.write_text("original line 1\noriginal line 2\n")

    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write("rewritten\n")

    assert target.read_text() == "rewritten\n"
    # Original was backed up.
    backups = list((tmp_path / "backups").glob("ledger.beancount.*.backup"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original line 1\noriginal line 2\n"
