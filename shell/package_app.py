"""Build the packaged app for this machine's platform (ADR-0007, ADR-0056): the sidecar tree, the frontend
bundle, then `tauri build` with `tauri.packaged.conf.json`. Prints the artifact paths, one per line.

    python3 shell/package_app.py

macOS: `Finzytrack.app`, ad-hoc signed (sidecar Mach-Os inside-out by build_sidecar.py, the shell and the
outer bundle by the Tauri bundler). Windows: the NSIS installer, unsigned. Linux: AppImage and `.deb`.
Release packaging (dmg, updater artifacts, signing keys) is M11's `release.yml`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sidecar"))
import build_sidecar  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "shell/src-tauri"
CLI = ROOT / "frontend/node_modules/@tauri-apps/cli/tauri.js"
BUNDLES = {
    "aarch64-apple-darwin": ("app", ["macos/*.app"]),
    "x86_64-pc-windows-msvc": ("nsis", ["nsis/*-setup.exe"]),
    "x86_64-unknown-linux-gnu": ("appimage,deb", ["appimage/*.AppImage", "deb/*.deb"]),
}


def main() -> int:
    target = build_sidecar.host_target()
    bundles, patterns = BUNDLES[target]
    build_sidecar.build(target)
    env = dict(os.environ)
    cargo_bin = Path.home() / ".cargo/bin"
    if cargo_bin.is_dir():
        env["PATH"] = f"{cargo_bin}{os.pathsep}{env['PATH']}"
    pnpm = shutil.which("pnpm", path=env["PATH"]) or "pnpm"
    subprocess.run([pnpm, "--dir", str(ROOT / "frontend"), "build"], check=True, env=env)
    out = TAURI_DIR / "target/release/bundle"
    if out.exists():
        shutil.rmtree(out)  # only this build's artifacts are reported
    subprocess.run(["node", str(CLI), "build", "--ci", "--config", "tauri.packaged.conf.json",
                    "--bundles", bundles], check=True, cwd=TAURI_DIR, env=env)
    artifacts = [p for pattern in patterns for p in sorted(out.glob(pattern))]
    if len(artifacts) != len(patterns):
        print(f"package_app: expected {len(patterns)} artifacts, found {artifacts}", file=sys.stderr)
        return 1
    for p in artifacts:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
