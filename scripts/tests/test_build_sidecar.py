"""Meta-tests for the packaged runtime's build (`shell/sidecar/build_sidecar.py`, ADR-0056; EP-24 pins).

Expected outcomes come from the script's contract and ADR-0056: an archive is used only when its SHA-256 equals
the pin, whether downloaded now or found in the cache; members that would land outside the tree, links leaving
it and special files are refused; an unpinned target is refused; the finished tree holds no symlinks (the
Tauri resource copy follows them); Mach-O files are found by content, deepest path first, for inside-out
signing. Each accepting check sits beside the case it must refuse (EP-29).
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import re
import sys
import subprocess
import tarfile
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

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

    def test_trim_removes_tcl_tk_of_any_version_and_keeps_the_runtime(self) -> None:
        # The layout of PBS 20261009 (verifier S0012 F1): versioned Tcl/Tk folders and their libraries.
        python = Path(tempfile.mkdtemp()) / "python"
        keep = ["lib/libpython3.13.dylib", "lib/python3.13/json/__init__.py", "bin/python3.13"]
        drop = ["lib/tcl9.0/init.tcl", "lib/tk9.0/tk.tcl", "lib/itcl4.3.8/x", "lib/thread3.0.6/x",
                "lib/libtcl9.0.dylib", "lib/python3.13/tkinter/__init__.py", "lib/python3.13/test/x.py",
                "bin/python3.13-config", "bin/pip3"]
        for rel in keep + drop:
            (python / rel).parent.mkdir(parents=True, exist_ok=True)
            (python / rel).write_text("x")
        bs.trim(python, "unix")
        self.assertEqual([r for r in keep + drop if (python / r).exists()], keep)

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


# -- The engine's own site-packages (S0038; ADR-0054 decisions 1 and 5, ADR-0056) --------------------------------

EXPORT = "beancount==3.2.0 \\\n    --hash=sha256:" + "a" * 64 + "\nregex==2026.9.29 \\\n    --hash=sha256:" + "b" * 64 + "\n"


class FakeRun:
    """Records each command; answers `uv export` with EXPORT and `uv build` with one wheel in its out dir; an
    install with `--target` leaves a console script in `<target>/bin`, as uv does."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        out = ""
        if cmd[:2] == ["uv", "export"]:
            out = EXPORT
        elif cmd[:2] == ["uv", "build"]:
            dest = Path(cmd[cmd.index("--out-dir") + 1])
            (dest / "finzytrack_engine-0.4.0.dev0-py3-none-any.whl").write_bytes(b"wheel")
        elif "--target" in cmd:
            target = Path(cmd[cmd.index("--target") + 1])
            (target / "bin").mkdir(parents=True, exist_ok=True)
            (target / "bin" / "bean-check").write_text("#!/build/python\n")
            (target / ".lock").write_text("")  # uv's install lock at the target's root (S0038 verifier F3)
        return subprocess.CompletedProcess(cmd, 0, out, "")


class EngineInstall(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / "sidecar" / bs.ENGINE_SITE
        self.fake = FakeRun()
        patcher = mock.patch.object(bs, "run", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        bs.install_engine(self.tmp / "python/bin/python3.13", self.site)

    def installs(self) -> list[list[str]]:
        return [c for c in self.fake.calls if c[:3] == ["uv", "pip", "install"]]

    def test_the_closure_is_the_engines_locked_runtime_closure(self) -> None:
        exports = [c for c in self.fake.calls if c[:2] == ["uv", "export"]]
        self.assertEqual(len(exports), 1)
        export = exports[0]
        self.assertEqual(Path(export[export.index("--directory") + 1]), ROOT / "engine")
        for flag in ("--frozen", "--no-dev", "--no-emit-project"):
            self.assertIn(flag, export)
        builds = [c for c in self.fake.calls if c[:2] == ["uv", "build"]]
        self.assertEqual([Path(c[-1]) for c in builds], [ROOT / "engine"])

    def test_everything_goes_into_the_engine_site_never_into_the_interpreter(self) -> None:
        installs = self.installs()
        self.assertEqual(len(installs), 2)  # the closure, then the engine's own wheel
        for cmd in installs:
            self.assertEqual(Path(cmd[cmd.index("--target") + 1]), self.site)
            self.assertIn("--no-deps", cmd)
            self.assertNotIn("--break-system-packages", cmd)  # that flag is for installing into the interpreter
        self.assertIn("--require-hashes", installs[0])
        self.assertTrue(installs[1][-1].endswith(".whl"))

    def test_console_scripts_are_removed(self) -> None:
        # Their shebangs name the build machine's interpreter; the engine is only ever run by the client.
        self.assertFalse((self.site / "bin").exists())

    def test_uvs_install_lock_is_not_shipped(self) -> None:  # S0038 verifier F3
        self.assertFalse((self.site / ".lock").exists())

    def test_the_engine_site_is_where_the_main_sidecar_looks_for_it(self) -> None:
        source = (ROOT / "backend/src/app/platform/adapters/engine_client.py").read_text(encoding="utf-8")
        parts = next(
            ast.literal_eval(node.value)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "ENGINE_SITE_PARTS" for t in node.targets)
        )
        # app: packaged_engine_site(prefix) = prefix.parent / *ENGINE_SITE_PARTS, with prefix = <sidecar>/python.
        self.assertEqual(bs.ENGINE_SITE, Path(*parts))
        self.assertEqual(bs.PYTHON_DIR, "python")


def make_site(site: Path, dists: Sequence[str], dirs: Sequence[str] = ()) -> Path:
    site.mkdir(parents=True, exist_ok=True)
    for d in dists:
        (site / f"{d}-1.0.dist-info").mkdir()
    for d in dirs:
        (site / d).mkdir()
    return site


class Separation(unittest.TestCase):
    """Each site-packages holds exactly its own locked closure: the engine's GPL-2.0-only closure never in the main
    sidecar's, the main sidecar never in the engine's (ADR-0054 decision 5)."""

    MAIN = {"finzytrack-backend"}
    ENGINE = {"beancount", "regex", "finzytrack-engine"}

    def check(self, main: Sequence[str], engine: Sequence[str], main_dirs: Sequence[str] = (),
              engine_dirs: Sequence[str] = ()) -> list[str]:
        tmp = Path(tempfile.mkdtemp())
        return bs.check_separation(make_site(tmp / "main", main, ["app", *main_dirs]),
                                   make_site(tmp / "engine", engine, ["beancount", "ledger_engine", *engine_dirs]),
                                   self.MAIN, self.ENGINE)

    def test_exact_closures_pass(self) -> None:
        self.assertEqual(self.check(["finzytrack_backend"], ["beancount", "regex", "finzytrack_engine"]), [])

    def test_names_are_compared_normalized(self) -> None:
        self.assertEqual(self.check(["Finzytrack.Backend"], ["Beancount", "regex", "finzytrack-engine"]), [])

    def test_must_not_pass(self) -> None:
        main, engine = ["finzytrack_backend"], ["beancount", "regex", "finzytrack_engine"]
        cases = {
            "beancount in the main site": (main + ["beancount"], engine, [], []),
            "the engine's package in the main site": (main, engine, ["ledger_engine"], []),
            "beanquery's module in the main site": (main, engine, ["beanquery"], []),
            "the main sidecar in the engine site": (main, engine + ["finzytrack_backend"], [], []),
            "the app package in the engine site": (main, engine, [], ["app"]),
            "an unlocked package in the engine site": (main, engine + ["requests"], [], []),
            "a locked package missing from the engine site": (main, ["beancount", "finzytrack_engine"], [], []),
            "the main sidecar missing": ([], engine, [], []),
            "an unlocked package in the main site": (main + ["six"], engine, [], []),
        }
        for name, (m, e, md, ed) in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.check(m, e, md, ed), [])

    def test_closure_names_read_the_requirement_lines(self) -> None:
        self.assertEqual(bs.closure_names(EXPORT + "# a comment\n\n"), {"beancount", "regex"})
        self.assertEqual(bs.closure_names("Typing_Extensions==4.16.0 ; python_version >= '3.9' \\\n"),
                         {"typing-extensions"})


def good_report(tree: Path) -> dict[str, object]:
    main_site = str(tree / "python/lib/python3.13/site-packages")
    engine_site = str(tree / "engine/site-packages")
    return {
        "site": engine_site,
        "handshake": {"protocol": 1, "engine_version": "0.4.0.dev0", "beancount_version": "3.2.0"},
        "pong": True,
        "error": None,
        "main_finds": {"beancount": False, "beanquery": False, "ledger_engine": False},
        "main_path": [str(tree / "python/lib/python3.13"), main_site],
        "main_sites": [main_site],
        "engine_finds_app": False,
        "engine_path": [str(tree / "python/lib/python3.13"), engine_site],
    }


class SelfcheckJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = Path(tempfile.mkdtemp()) / "sidecar"
        for d in ("python/lib/python3.13/site-packages", "engine/site-packages"):
            (self.tree / d).mkdir(parents=True)

    def test_a_working_engine_passes(self) -> None:
        self.assertEqual(bs.judge_selfcheck(good_report(self.tree), self.tree), [])

    def test_must_not_pass(self) -> None:
        main_site = str(self.tree / "python/lib/python3.13/site-packages")
        engine_site = str(self.tree / "engine/site-packages")
        cases: dict[str, tuple[str, object]] = {
            "an error": ("error", "start_failed: handshake: crashed"),
            "no pong": ("pong", False),
            "pong not a boolean": ("pong", "yes"),
            "no handshake": ("handshake", None),
            "no Beancount version": ("handshake", {"protocol": 1, "engine_version": "x", "beancount_version": ""}),
            "main finds beancount": ("main_finds", {"beancount": True, "beanquery": False, "ledger_engine": False}),
            "main finds the engine": ("main_finds", {"beancount": False, "beanquery": False, "ledger_engine": True}),
            "main finds nothing reported": ("main_finds", {}),
            "engine finds app": ("engine_finds_app", True),
            "engine view missing": ("engine_finds_app", None),
            "engine path holds the main site": ("engine_path", [engine_site, main_site]),
            "engine path holds a folder inside the main site": ("engine_path", [engine_site, main_site + "/app"]),
            "engine path missing": ("engine_path", None),
            "main path holds the engine site": ("main_path", [main_site, engine_site]),
            "engine site outside the tree": ("site", "/elsewhere/engine/site-packages"),
        }
        for name, (key, value) in cases.items():
            with self.subTest(case=name):
                report = good_report(self.tree)
                report[key] = value
                self.assertNotEqual(bs.judge_selfcheck(report, self.tree), [])

    def test_a_report_that_is_not_an_object_is_refused(self) -> None:
        self.assertNotEqual(bs.judge_selfcheck([], self.tree), [])


class BuildOrder(unittest.TestCase):
    """`build` installs both sides, then checks the finished (signed) tree, and a failed check fails the build."""

    def run_build(self, verify: object) -> list[str]:
        calls: list[str] = []
        tmp = Path(tempfile.mkdtemp())

        def fake_extract(archive: Path, dest: Path) -> None:
            (dest / "python/bin").mkdir(parents=True)
            (dest / "python/bin/python3.13").write_text("")

        def record(name: str):  # noqa: ANN202
            return lambda *a, **k: calls.append(name) or 0

        patches = {
            "fetch": lambda *a, **k: tmp / "a.tar.gz",
            "extract": fake_extract,
            "install_sidecar": record("install_sidecar"),
            "install_engine": record("install_engine"),
            "trim": record("trim"),
            "compile_bytecode": record("compile_bytecode"),
            "remove_symlinks": record("remove_symlinks"),
            "adhoc_sign": record("adhoc_sign"),
            "verify": verify if verify is not None else record("verify"),
        }
        with contextlib.ExitStack() as stack:
            for name, fn in patches.items():
                stack.enter_context(mock.patch.object(bs, name, fn))
            bs.build("aarch64-apple-darwin", out=tmp / "out", cache=tmp / "cache")
        return calls

    def test_the_tree_is_verified_last(self) -> None:
        calls = self.run_build(None)
        self.assertEqual(calls[-1], "verify")
        self.assertLess(calls.index("install_engine"), calls.index("verify"))
        self.assertLess(calls.index("adhoc_sign"), calls.index("verify"))

    def test_a_failed_verification_fails_the_build(self) -> None:
        def refuse(*a: object, **k: object) -> None:
            raise bs.BuildError("the engine site holds app")

        with self.assertRaises(bs.BuildError):
            self.run_build(refuse)


if __name__ == "__main__":
    unittest.main()
