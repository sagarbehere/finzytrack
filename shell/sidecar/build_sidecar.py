"""Build the python-build-standalone tree the packaged app ships as `bundle.resources` (ADR-0056).

Output: `build/sidecar/python/`, a relocatable CPython (pinned in `pbs.lock.json`) with the main sidecar's
package and its locked runtime dependencies installed (ADR-0054; the engine process joins with BL-0041).

    python3 shell/sidecar/build_sidecar.py [--target TRIPLE]

Fails closed: an archive whose SHA-256 differs from the pin is refused, also from the download cache; archive
members that would land outside the tree, links and device files are refused by tarfile's `data` filter;
an unknown target is refused. The finished tree holds no symlinks, because the Tauri resource copy follows
them (EP-30: the class is removed, not checked case by case). On macOS every Mach-O is ad-hoc signed,
deepest path first. Stdlib only; runs on the build machine of each target, from the repo root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).with_name("pbs.lock.json")
OUT = ROOT / "build/sidecar"
CACHE = ROOT / "build/downloads"

# Parts of the runtime a sidecar never uses (GUI toolkits, the bundled pip, headers, the stdlib's own test
# suite). Paths are relative to `python/`; a missing path is fine (layouts differ by OS).
TRIM = {
    "unix": [
        "lib/python3.13/idlelib", "lib/python3.13/ensurepip", "lib/python3.13/tkinter",
        "lib/python3.13/turtledemo", "lib/python3.13/test", "lib/python3.13/site-packages/pip",
        "include", "share", "lib/pkgconfig", "lib/tcl9", "lib/tk9", "lib/tcl8", "lib/tk8", "lib/tcl8.6",
        "lib/tk8.6", "lib/itcl4.3.2", "lib/thread3.0.1",
    ],
    "windows": [
        "Lib/idlelib", "Lib/ensurepip", "Lib/tkinter", "Lib/turtledemo", "Lib/test", "Lib/site-packages/pip",
        "include", "tcl", "Scripts",
    ],
}
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


class BuildError(Exception):
    pass


def host_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine == "arm64":
        return "aarch64-apple-darwin"
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "x86_64-unknown-linux-gnu"
    raise BuildError(f"no packaged build for {sys.platform}/{machine} (ADR-0007 platforms)")


def pinned(lock: dict, target: str) -> dict:
    entry = lock["targets"].get(target)
    if entry is None:
        raise BuildError(f"target {target!r} is not pinned in {LOCK.name}")
    return entry


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, dest: Path, expected: str) -> Path:
    """The archive at `dest`, downloaded if absent; its hash is checked every time, also for a cached copy."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (https URL from the lock)
                shutil.copyfileobj(response, tmp)
        os.replace(tmp.name, dest)
    actual = sha256(dest)
    if actual != expected:
        raise BuildError(f"{dest.name}: SHA-256 {actual} differs from the pin {expected}; delete it and retry")
    return dest


def extract(archive: Path, dest: Path) -> None:
    """Unpack with tarfile's `data` filter: absolute paths, `..`, links leaving `dest` and special files fail."""
    with tarfile.open(archive) as tar:
        # The data filter would strip a leading "/" and extract inside `dest`; refuse instead.
        absolute = [m.name for m in tar.getmembers() if m.name.startswith(("/", "\\")) or m.name[1:2] == ":"]
        if absolute:
            raise BuildError(f"{archive.name}: unsafe archive member: absolute path {absolute[0]!r}")
        try:
            tar.extractall(dest, filter="data")
        except tarfile.FilterError as e:
            raise BuildError(f"{archive.name}: unsafe archive member: {e}") from e


def remove_symlinks(tree: Path) -> int:
    links = [p for p in tree.rglob("*") if p.is_symlink()]
    for p in links:
        p.unlink()
    return len(links)


def trim(python: Path, layout: str) -> None:
    for rel in TRIM[layout]:
        p = python / rel
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
    for pattern in ("bin/idle3*", "bin/pip*", "bin/pydoc3*", "bin/2to3*", "lib/libtcl*", "lib/libtk*",
                    "DLLs/_tkinter*", "DLLs/tcl*", "DLLs/tk*", "lib/python3.13/lib-dynload/_tkinter*", "bin/python3*-config"):
        for p in python.glob(pattern):
            p.unlink()
    for p in [*python.glob("lib/python3.13/site-packages/pip-*"), *python.glob("Lib/site-packages/pip-*")]:
        shutil.rmtree(p)


def interpreter(python: Path, layout: str) -> Path:
    return python / ("python.exe" if layout == "windows" else "bin/python3.13")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, **kw)


def install_sidecar(py: Path) -> None:
    """The main sidecar's locked runtime closure (hash-checked), then its own wheel, into the runtime."""
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "runtime.txt"
        exported = run(["uv", "export", "--directory", str(ROOT / "backend"), "--frozen", "--no-dev",
                        "--no-emit-project", "--no-header", "--format", "requirements-txt"],
                       capture_output=True).stdout
        req.write_text(exported, encoding="utf-8")
        common = ["uv", "pip", "install", "--python", str(py), "--break-system-packages", "--no-deps"]
        if any(line.strip() and not line.lstrip().startswith("#") for line in exported.splitlines()):
            run([*common, "--require-hashes", "-r", str(req)])
        run(["uv", "build", "--wheel", "--out-dir", tmp, str(ROOT / "backend")], capture_output=True)
        wheels = list(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            raise BuildError(f"expected one sidecar wheel, found {len(wheels)}")
        run([*common, str(wheels[0])])


def compile_bytecode(py: Path, python: Path) -> None:
    # The installed bundle is read-only and runs with -B, so every module is compiled now; unchecked-hash
    # bytecode is used whatever the file times are after the installer copied it.
    run([str(py), "-I", "-B", "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         str(python)], capture_output=True)


def macho_files(tree: Path) -> list[Path]:
    found = []
    for p in tree.rglob("*"):
        if p.is_file() and not p.is_symlink():
            with p.open("rb") as f:
                if f.read(4) in MACHO_MAGIC:
                    found.append(p)
    return sorted(found, key=lambda p: (-len(p.parts), str(p)))  # inside-out: deepest first


def adhoc_sign(tree: Path) -> int:
    files = macho_files(tree)
    for i in range(0, len(files), 50):
        batch = [str(p) for p in files[i:i + 50]]
        run(["codesign", "-s", "-", "-f", "--timestamp=none", *batch], capture_output=True)
        run(["codesign", "--verify", "--strict", *batch], capture_output=True)
    return len(files)


def build(target: str, out: Path = OUT, cache: Path = CACHE) -> Path:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    entry = pinned(lock, target)
    layout = "windows" if "windows" in target else "unix"
    archive = fetch(lock["url"].format(release=lock["release"], file=entry["file"]), cache / entry["file"],
                    entry["sha256"])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    extract(archive, out)
    python = out / "python"
    py = interpreter(python, layout)
    if not py.is_file():
        raise BuildError(f"{py.relative_to(out)} missing after extraction")
    install_sidecar(py)
    trim(python, layout)
    compile_bytecode(py, python)
    links = remove_symlinks(out)
    signed = adhoc_sign(out) if target.endswith("apple-darwin") else 0
    print(f"sidecar tree: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} "
          f"(Python {lock['python']}, PBS {lock['release']}, {target}); "
          f"{links} symlinks removed; {signed} Mach-O files ad-hoc signed")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", help="target triple (default: this machine's)")
    args = parser.parse_args(argv)
    try:
        build(args.target or host_target())
    except (BuildError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", None) or ""
        print(f"build_sidecar: {e}\n{detail}".rstrip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
