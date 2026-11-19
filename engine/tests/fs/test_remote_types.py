"""Which Linux file-system types are network shares (S0134, BL-0298): Linux has no flag that says a volume is local
(macOS has `MNT_LOCAL`, Windows `FILE_REMOTE_DEVICE`), so the Linux adapter's `remote()` asks the mount's type
against a list (`ledger_engine.fs.posix.network_type`). Pure; run on macOS and Linux (the module is POSIX-only)."""

from __future__ import annotations

import sys

import pytest

POSIX_ONLY = "the POSIX adapters' list; Windows asks FILE_REMOTE_DEVICE (tests/fs/test_windows.py)"

if sys.platform == "win32":
    raise pytest.skip.Exception(POSIX_ONLY, allow_module_level=True)

from ledger_engine.fs.posix import network_type  # noqa: E402 (after the platform guard)


@pytest.mark.parametrize(
    "fs_type",
    ["nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "ceph", "glusterfs", "lustre", "fuse.sshfs", "fuse.rclone"],
)
def test_network_types_are_remote(fs_type: str) -> None:
    assert network_type(fs_type)


@pytest.mark.parametrize(
    "fs_type", ["ext4", "xfs", "btrfs", "tmpfs", "vfat", "exfat", "ntfs3", "fuse", "fuse.ntfs-3g", "overlay", "9p", ""]
)
def test_local_types_are_not(fs_type: str) -> None:
    """The must-not-pass: a local file system, a local FUSE one, an unknown (empty) type and a type the list does not
    name are taken as local (`9p`: WSL's own drives and a VM's shared folder, on this machine)."""
    assert not network_type(fs_type)
