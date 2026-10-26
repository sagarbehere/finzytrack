"""Build the python-build-standalone tree the packaged app ships as `bundle.resources` (ADR-0056).

Output: `build/sidecar/python/`, a relocatable CPython (pinned in `pbs.lock.json`) with the main sidecar's
package and its locked runtime dependencies installed in its own site-packages, and `build/sidecar/engine/
site-packages/`, the ledger engine's package and its locked runtime closure (ADR-0054, ADR-0056; S0038). One
interpreter runs both processes; the engine runs it with `-S` and only its own site (`app.platform.adapters.
engine_client.EngineCommand.for_packaged`), so the engine's GPL-2.0-only closure and the main sidecar's packages never
share a process (ADR-0054 decision 5). The build checks the finished tree: each site-packages holds exactly its own
locked closure, and the package's own self-check (`python -I -B -m app.selfcheck`) shows a real handshake and ping
with the packaged engine and neither side seeing the other's packages.

    python3 shell/sidecar/build_sidecar.py [--target TRIPLE]

Fails closed: an archive whose SHA-256 differs from the pin is refused, also from the download cache; archive
members that would land outside the tree, links and device files are refused by tarfile's `data` filter;
an unknown target is refused; a tree that fails the separation or the self-check is refused. The finished tree holds no symlinks, because the Tauri resource copy follows
them (EP-30: the class is removed, not checked case by case). On macOS every Mach-O is ad-hoc signed,
deepest path first. Stdlib only; runs on the build machine of each target, from the repo root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
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
# The packaged layout inside OUT; the main sidecar finds the engine at `<prefix>/../engine/site-packages`
# (`app.platform.adapters.engine_client.packaged_engine_site`; a meta-test keeps the two equal).
PYTHON_DIR = "python"
ENGINE_SITE = Path("engine/site-packages")
MAIN_DISTRIBUTION = "finzytrack-backend"
ENGINE_DISTRIBUTION = "finzytrack-engine"
# Top-level names that must never be importable on the other side (beside the exact closure check).
ENGINE_MODULES = ("beancount", "beanquery", "ledger_engine")
MAIN_MODULES = ("app",)

# Parts of the runtime a sidecar never uses (GUI toolkits, the bundled pip, headers, the stdlib's own test
# suite). Paths are relative to `python/`; a missing path is fine (layouts differ by OS).
TRIM = {
    "unix": [
        "lib/python3.13/idlelib", "lib/python3.13/ensurepip", "lib/python3.13/tkinter",
        "lib/python3.13/turtledemo", "lib/python3.13/test", "lib/python3.13/site-packages/pip",
        "include", "share", "lib/pkgconfig",
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
    # Tcl/Tk and its extensions, whatever versions the release carries (tcl9.0, itcl4.3.8, thread3.0.6, ...).
    for pattern in ("lib/tcl[0-9]*", "lib/tk[0-9]*", "lib/itcl[0-9]*", "lib/thread[0-9]*"):
        for p in python.glob(pattern):
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


def export_closure(project: Path) -> str:
    """The project's locked runtime closure (no dev dependencies, not the project itself), with hashes."""
    return run(["uv", "export", "--directory", str(project), "--frozen", "--no-dev", "--no-emit-project",
                "--no-header", "--format", "requirements-txt"], capture_output=True).stdout


def install_project(project: Path, install: list[str]) -> None:
    """`project`'s locked runtime closure (hash-checked), then its own wheel, with the `install` command."""
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "runtime.txt"
        exported = export_closure(project)
        req.write_text(exported, encoding="utf-8")
        if closure_names(exported):
            run([*install, "--require-hashes", "-r", str(req)])
        run(["uv", "build", "--wheel", "--out-dir", tmp, str(project)], capture_output=True)
        wheels = list(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            raise BuildError(f"expected one wheel of {project.name}, found {len(wheels)}")
        run([*install, str(wheels[0])])


def install_sidecar(py: Path) -> None:
    """The main sidecar into the interpreter's own site-packages."""
    install_project(ROOT / "backend",
                    ["uv", "pip", "install", "--python", str(py), "--break-system-packages", "--no-deps"])


def install_engine(py: Path, site: Path) -> None:
    """The engine into its own site-packages, never into the interpreter (`py` only selects the wheels' tags).
    Console scripts are removed: their shebangs name this build's interpreter, and only the client runs the engine."""
    site.mkdir(parents=True, exist_ok=True)
    install_project(ROOT / "engine",
                    ["uv", "pip", "install", "--python", str(py), "--target", str(site), "--no-deps"])
    scripts = site / "bin"
    if scripts.is_dir():
        shutil.rmtree(scripts)


def normalized(name: str) -> str:
    """A distribution name as PEP 503 compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def closure_names(exported: str) -> set[str]:
    """The distributions named by an exported requirements file (`name==version ... \\` lines)."""
    found = (re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==", line.strip()) for line in exported.splitlines())
    return {normalized(m.group(1)) for m in found if m}


def distributions(site: Path) -> set[str]:
    """The distributions installed in `site`, by their `.dist-info` folders."""
    return {normalized(p.name.removesuffix(".dist-info").rsplit("-", 1)[0]) for p in site.glob("*.dist-info")}


def check_separation(main_site: Path, engine_site: Path, main_expected: set[str],
                     engine_expected: set[str]) -> list[str]:
    """Each site-packages holds exactly its own locked closure, and neither holds the other's top-level packages
    (ADR-0054 decision 5). Returns the problems; none means the two sides are apart."""
    problems = []
    for name, site, expected in (("main sidecar", main_site, main_expected), ("engine", engine_site, engine_expected)):
        have = distributions(site)
        if extra := sorted(have - {normalized(n) for n in expected}):
            problems.append(f"{name} site-packages holds unlocked distributions: {', '.join(extra)}")
        if missing := sorted({normalized(n) for n in expected} - have):
            problems.append(f"{name} site-packages lacks locked distributions: {', '.join(missing)}")
    for name, site, foreign in (("main sidecar", main_site, ENGINE_MODULES), ("engine", engine_site, MAIN_MODULES)):
        if found := sorted(m for m in foreign if (site / m).exists() or (site / f"{m}.py").exists()):
            problems.append(f"{name} site-packages holds the other side's packages: {', '.join(found)}")
    return problems


def _inside(path: str, folder: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except (OSError, ValueError):
        return False


def judge_selfcheck(report: object, tree: Path) -> list[str]:
    """The problems in `app.selfcheck`'s report (the same facts the packaged `--smoke-test` judges)."""
    if not isinstance(report, dict):
        return ["the self-check report is not an object"]
    problems = []
    if report.get("error") is not None:
        problems.append(f"the engine failed: {report.get('error')}")
    handshake = report.get("handshake")
    if not (isinstance(handshake, dict) and isinstance(handshake.get("beancount_version"), str)
            and handshake["beancount_version"]):
        problems.append("no handshake with a Beancount version")
    if report.get("pong") is not True:
        problems.append("no pong")
    engine_site = report.get("site")
    if not (isinstance(engine_site, str) and _inside(engine_site, tree)):
        problems.append("the engine site is not inside the tree")
        return problems
    finds = report.get("main_finds")
    if not (isinstance(finds, dict) and set(ENGINE_MODULES) <= set(finds) and not any(finds.values())):
        problems.append(f"the main side finds engine packages: {finds}")
    if report.get("engine_finds_app") is not False:
        problems.append("the engine side finds the main sidecar's package (or did not report)")
    main_sites = report.get("main_sites")
    engine_path = report.get("engine_path")
    main_path = report.get("main_path")
    if not (isinstance(main_sites, list) and main_sites and isinstance(engine_path, list)
            and isinstance(main_path, list)):
        problems.append("the report lacks a path")
        return problems
    for entry in engine_path:
        if any(_inside(str(entry), Path(str(s))) for s in main_sites):
            problems.append(f"the engine's path holds the main sidecar's site-packages: {entry}")
    for entry in main_path:
        if _inside(str(entry), Path(engine_site)):
            problems.append(f"the main side's path holds the engine site: {entry}")
    return problems


def selfcheck(py: Path) -> object:
    out = run([str(py), "-I", "-B", "-m", "app.selfcheck"], capture_output=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise BuildError(f"the self-check printed no JSON report: {e}") from e


def main_site_packages(python: Path, layout: str) -> Path:
    return python / ("Lib/site-packages" if layout == "windows" else "lib/python3.13/site-packages")


def verify(out: Path, python: Path, py: Path, layout: str) -> None:
    """Refuse a tree whose two sides are not apart, or whose engine does not answer."""
    problems = check_separation(main_site_packages(python, layout), out / ENGINE_SITE,
                                closure_names(export_closure(ROOT / "backend")) | {MAIN_DISTRIBUTION},
                                closure_names(export_closure(ROOT / "engine")) | {ENGINE_DISTRIBUTION})
    problems += judge_selfcheck(selfcheck(py), out)
    if problems:
        raise BuildError("the packaged tree failed its checks:\n  " + "\n  ".join(problems))


def compile_bytecode(py: Path, tree: Path) -> None:
    # The installed bundle is read-only and runs with -B, so every module (both sides) is compiled now;
    # unchecked-hash bytecode is used whatever the file times are after the installer copied it.
    run([str(py), "-I", "-B", "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         str(tree)], capture_output=True)


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
    for p in files:
        # ADR-0056: signed by us, inside-out. The linker's own ad-hoc signature ("linker-signed") would also
        # verify, so its absence is what shows this step ran.
        info = run(["codesign", "-dv", str(p)], capture_output=True).stderr
        if "linker-signed" in info:
            raise BuildError(f"{p.name} still carries only the linker's signature")
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
    python = out / PYTHON_DIR
    py = interpreter(python, layout)
    if not py.is_file():
        raise BuildError(f"{py.relative_to(out)} missing after extraction")
    install_sidecar(py)
    install_engine(py, out / ENGINE_SITE)
    trim(python, layout)
    compile_bytecode(py, out)
    links = remove_symlinks(out)
    signed = adhoc_sign(out) if target.endswith("apple-darwin") else 0
    verify(out, python, py, layout)
    print(f"sidecar tree: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} "
          f"(Python {lock['python']}, PBS {lock['release']}, {target}); "
          f"{links} symlinks removed; {signed} Mach-O files ad-hoc signed; engine site apart and answering")
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
