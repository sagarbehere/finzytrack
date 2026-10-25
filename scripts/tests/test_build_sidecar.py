"""Meta-tests for the packaged runtime's build (`shell/sidecar/build_sidecar.py`, ADR-0056; EP-24 pins).

Expected outcomes come from the script's contract and ADR-0056: an archive is used only when its SHA-256 equals
the pin, whether downloaded now or found in the cache; members that would land outside the tree, links leaving
it and special files are refused; an unpinned target is refused; the finished tree holds no symlinks (the
Tauri resource copy follows them); Mach-O files are found by content, deepest path first, for inside-out
signing. Each accepting check sits beside the case it must refuse (EP-29).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shell/sidecar"))

import build_sidecar as bs  # noqa: E402

LOCK = json.loads((ROOT / "shell/sidecar/pbs.lock.json").read_text())


def make_tar(path: Path, members: list[tarfile.TarInfo], data: bytes = b"x") -> None:
    with tarfile.open(path, "w:gz") as tar:
        for m in members:
            tar.addfile(m, io.BytesIO(data) if m.isfile() else None)


def member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    m = tarfile.TarInfo(name)
    m.type, m.linkname, m.size = kind, linkname, 1 if kind == tarfile.REGTYPE else 0
    return m


class Lock(unittest.TestCase):
    def test_the_pins_cover_exactly_the_decided_platforms(self) -> None:
        # ADR-0007 + ADR-0056: Apple-silicon macOS, Windows x64, Linux x64.
        self.assertEqual(set(LOCK["targets"]),
                         {"aarch64-apple-darwin", "x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"})

    def test_each_pin_names_the_locked_python_and_release_with_a_full_hash(self) -> None:
        for target, entry in LOCK["targets"].items():
            with self.subTest(target=target):
                self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(entry["file"],
                                 f"cpython-{LOCK['python']}+{LOCK['release']}-{target}-install_only_stripped.tar.gz")
        self.assertEqual(LOCK["release"], LOCK["released"].replace("-", ""))
        self.assertTrue(LOCK["url"].startswith("https://"))

    def test_python_matches_the_architecture_pin(self) -> None:
        arch = (ROOT / "design/ARCHITECTURE.md").read_text()
        self.assertIsNotNone(re.search(rf"\*\*{re.escape(LOCK['python'])}\*\*", arch))

    def test_an_unpinned_target_is_refused(self) -> None:
        with self.assertRaises(bs.BuildError):
            bs.pinned(LOCK, "x86_64-apple-darwin")  # Intel Macs are not built (ADR-0007)


class Fetch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "src.tar.gz"
        self.source.write_bytes(b"archive bytes")
        self.good = hashlib.sha256(b"archive bytes").hexdigest()

    def test_a_download_with_the_pinned_hash_is_used(self) -> None:
        dest = self.tmp / "cache/a.tar.gz"
        self.assertEqual(bs.fetch(self.source.as_uri(), dest, self.good), dest)

    def test_a_download_with_another_hash_is_refused(self) -> None:
        with self.assertRaises(bs.BuildError):
            bs.fetch(self.source.as_uri(), self.tmp / "cache/a.tar.gz", "0" * 64)

    def test_a_tampered_cached_archive_is_refused(self) -> None:
        dest = self.tmp / "cache/a.tar.gz"
        bs.fetch(self.source.as_uri(), dest, self.good)
        dest.write_bytes(b"archive bytes, changed")
        with self.assertRaises(bs.BuildError):
            bs.fetch(self.source.as_uri(), dest, self.good)


class Extract(unittest.TestCase):
    def extract(self, members: list[tarfile.TarInfo]) -> Path:
        tmp = Path(tempfile.mkdtemp())
        make_tar(tmp / "a.tar.gz", members)
        (tmp / "out").mkdir()
        bs.extract(tmp / "a.tar.gz", tmp / "out")
        return tmp / "out"

    def test_ordinary_members_and_inner_links_extract(self) -> None:
        out = self.extract([member("python/bin/python3.13"),
                            member("python/bin/python3", tarfile.SYMTYPE, "python3.13")])
        self.assertTrue((out / "python/bin/python3.13").is_file())

    def test_unsafe_members_are_refused(self) -> None:
        cases = {
            "dot-dot": [member("../escape")],
            "absolute": [member("/tmp/escape")],
            "symlink out": [member("python/link", tarfile.SYMTYPE, "/etc/passwd")],
            "relative symlink out": [member("python/link", tarfile.SYMTYPE, "../../outside")],
            "hard link out": [member("python/hard", tarfile.LNKTYPE, "/etc/passwd")],
            "device file": [member("python/dev", tarfile.CHRTYPE)],
            "fifo": [member("python/fifo", tarfile.FIFOTYPE)],
        }
        for name, members in cases.items():
            with self.subTest(case=name), self.assertRaises(bs.BuildError):
                self.extract(members)


class Tree(unittest.TestCase):
    def test_no_symlink_survives(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        (tmp / "bin/python3.13").write_text("x")
        (tmp / "bin/python3").symlink_to("python3.13")
        (tmp / "dangling").symlink_to("nowhere")
        (tmp / "dirlink").symlink_to("bin", target_is_directory=True)
        self.assertEqual(bs.remove_symlinks(tmp), 3)
        self.assertEqual([p for p in tmp.rglob("*") if p.is_symlink()], [])
        self.assertTrue((tmp / "bin/python3.13").is_file())

    def test_macho_files_are_found_by_content_deepest_first(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a/b").mkdir(parents=True)
        (tmp / "top.so").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)  # 64-bit little-endian Mach-O
        (tmp / "a/b/deep.dylib").write_bytes(b"\xca\xfe\xba\xbe" + b"\0" * 8)  # universal
        (tmp / "a/python3.13").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)  # no extension
        (tmp / "a/script.so").write_bytes(b"#!/bin/sh\n")  # extension alone is not enough
        (tmp / "a/link.so").symlink_to("../top.so")
        found = [p.relative_to(tmp).as_posix() for p in bs.macho_files(tmp)]
        self.assertEqual(found, ["a/b/deep.dylib", "a/python3.13", "top.so"])


if __name__ == "__main__":
    unittest.main()
