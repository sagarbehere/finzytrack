#!/usr/bin/env python3
"""
Cross-platform build script for Finzytrack desktop app.

Usage (from the desktop/ directory, with venv active):
    python build.py [--clean]

Builds:
    macOS  -> dist/Finzytrack.app
    Linux  -> dist/Finzytrack-x86_64.AppImage
    Windows -> dist/Finzytrack-windows.zip
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
FRONTEND_DIR = ROOT_DIR / 'frontend'
DIST_DIR = SCRIPT_DIR / 'dist'
BUILD_DIR = SCRIPT_DIR / 'build'
ICONS_DIR = ROOT_DIR / 'assets' / 'icons'
VERSION_FILE = ROOT_DIR / 'VERSION'


def run(cmd: list[str], **kwargs):
    """Run a command, printing it first, and abort on failure.

    On Windows, resolve the executable via shutil.which() so wrappers like
    `npm.cmd` and `pyinstaller.exe` are found — subprocess.run() on Windows
    calls CreateProcess directly, which doesn't do the PATHEXT lookup the
    shell does.
    """
    if platform.system() == 'Windows' and cmd and not Path(cmd[0]).is_absolute():
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd = [resolved, *cmd[1:]]
    print(f'  $ {" ".join(cmd)}', flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def check_venv():
    venv = os.environ.get('VIRTUAL_ENV', '')
    if 'finzytrack' not in venv.lower():
        print('WARNING: finzytrack venv does not appear to be active.')
        print(f'  VIRTUAL_ENV={venv}')
        print('  Proceeding anyway — make sure dependencies are installed.')


def clean():
    print('==> Cleaning previous build artifacts...')
    for d in (BUILD_DIR, DIST_DIR):
        if d.exists():
            shutil.rmtree(d)


def ensure_icons():
    """Generate platform icon assets from master.svg if any are missing.

    The platform icon directories under assets/icons/ are gitignored — only the
    SVG source and generator are tracked. A fresh clone has no PNG/ICO/ICNS
    files, so the later packaging steps would fail without this.
    """
    required = {
        'Darwin': ICONS_DIR / 'macos' / 'AppIcon.iconset' / 'icon_512x512.png',
        'Linux': ICONS_DIR / 'linux' / '256x256' / 'finzytrack.png',
        'Windows': ICONS_DIR / 'windows' / 'app.ico',
    }
    probe = required.get(platform.system())
    if probe is None or probe.exists():
        return
    print(f'==> Platform icons missing ({probe}). Generating from master.svg...')
    run([sys.executable, 'generate.py'], cwd=str(ICONS_DIR))


def sync_ai_resources():
    """Copy AI reference / schema files from frontend/ into backend/resources/.

    Must run before both the frontend and PyInstaller steps: the frontend
    needs `recipes.generated.ts` (regenerated below), and PyInstaller bundles
    `backend/resources/ai_reference/` and `backend/resources/schemas/`.
    """
    print('==> Syncing AI reference files...')
    run([sys.executable, str(ROOT_DIR / 'scripts' / 'sync_ai_reference.py')])

    print('==> Regenerating recipe schema appendix...')
    run([sys.executable, str(ROOT_DIR / 'scripts' / 'generate_recipe_schema_doc.py')])

    print('==> Regenerating TS types from recipe.schema.json...')
    run(['npm', 'run', 'generate-recipe-types'], cwd=str(FRONTEND_DIR))


def sync_frontend_version():
    """Mirror /VERSION into frontend/package.json's "version" field.

    The frontend doesn't currently display its package.json version anywhere,
    but keeping the two in sync means the npm metadata doesn't lie about
    what build produced the bundle.
    """
    import json
    version = VERSION_FILE.read_text().strip()
    pkg_path = FRONTEND_DIR / 'package.json'
    pkg = json.loads(pkg_path.read_text())
    if pkg.get('version') == version:
        return
    print(f'==> Syncing frontend/package.json version → {version}')
    pkg['version'] = version
    pkg_path.write_text(json.dumps(pkg, indent=2) + '\n')


def build_frontend():
    sync_frontend_version()
    print('==> Building Vue frontend...')
    run(['npm', 'run', 'build'], cwd=str(FRONTEND_DIR))
    print(f'    Frontend built: {FRONTEND_DIR / "dist"}')


def run_pyinstaller():
    print('==> Running PyInstaller...')
    run(['pyinstaller', 'finzytrack.spec', '--noconfirm'], cwd=str(SCRIPT_DIR))


def package_macos():
    """Ad-hoc codesign and strip quarantine on the .app bundle."""
    app_path = DIST_DIR / 'Finzytrack.app'
    if not app_path.exists():
        print(f'ERROR: {app_path} not found after PyInstaller build.')
        sys.exit(1)

    print('==> Signing .app bundle (ad-hoc)...')
    run(['codesign', '--force', '--deep', '--sign', '-', str(app_path)])

    print('==> Removing quarantine attribute...')
    run(['xattr', '-cr', str(app_path)])

    print(f'\n==> Done. App bundle: {app_path}')
    print('    To run: open dist/Finzytrack.app')


def package_linux():
    """Build an AppImage from the PyInstaller COLLECT output."""
    collect_dir = DIST_DIR / 'Finzytrack'
    if not collect_dir.exists():
        print(f'ERROR: {collect_dir} not found after PyInstaller build.')
        sys.exit(1)

    print('==> Assembling AppDir...')
    appdir = DIST_DIR / 'Finzytrack.AppDir'
    if appdir.exists():
        shutil.rmtree(appdir)
    appdir.mkdir()

    # AppDir structure:
    #   AppRun          (entry point)
    #   finzytrack.desktop
    #   finzytrack.png  (top-level icon for appimaged)
    #   usr/bin/        (PyInstaller output)
    #   usr/share/icons/hicolor/256x256/apps/finzytrack.png
    linux_assets = SCRIPT_DIR / 'linux'
    shutil.copy2(linux_assets / 'AppRun', appdir / 'AppRun')
    os.chmod(appdir / 'AppRun', 0o755)
    shutil.copy2(linux_assets / 'finzytrack.desktop', appdir / 'finzytrack.desktop')

    icon_src = ICONS_DIR / 'linux' / '256x256' / 'finzytrack.png'
    shutil.copy2(icon_src, appdir / 'finzytrack.png')

    icon_dest = appdir / 'usr' / 'share' / 'icons' / 'hicolor' / '256x256' / 'apps'
    icon_dest.mkdir(parents=True)
    shutil.copy2(icon_src, icon_dest / 'finzytrack.png')

    usr_bin = appdir / 'usr' / 'bin'
    usr_bin.mkdir(parents=True)
    # Move the COLLECT contents into usr/bin/
    for item in collect_dir.iterdir():
        shutil.move(str(item), str(usr_bin / item.name))

    # Download appimagetool if not present
    appimagetool = BUILD_DIR / 'appimagetool'
    if not appimagetool.exists():
        print('==> Downloading appimagetool...')
        BUILD_DIR.mkdir(exist_ok=True)
        arch = 'x86_64' if platform.machine() in ('x86_64', 'AMD64') else 'aarch64'
        url = (
            f'https://github.com/AppImage/appimagetool/releases/download/continuous/'
            f'appimagetool-{arch}.AppImage'
        )
        run(['curl', '-L', '-o', str(appimagetool), url])
        os.chmod(appimagetool, 0o755)

    print('==> Building AppImage...')
    arch = 'x86_64' if platform.machine() in ('x86_64', 'AMD64') else 'aarch64'
    appimage_name = f'Finzytrack-{arch}.AppImage'
    env = {**os.environ, 'ARCH': arch}
    run([str(appimagetool), str(appdir), str(DIST_DIR / appimage_name)], env=env)

    print(f'\n==> Done. AppImage: {DIST_DIR / appimage_name}')
    print(f'    To run: ./dist/{appimage_name}')


def package_windows():
    """Zip the PyInstaller COLLECT output for Windows distribution."""
    collect_dir = DIST_DIR / 'Finzytrack'
    if not collect_dir.exists():
        print(f'ERROR: {collect_dir} not found after PyInstaller build.')
        sys.exit(1)

    print('==> Creating Windows zip archive...')
    archive_path = DIST_DIR / 'Finzytrack-windows'
    shutil.make_archive(str(archive_path), 'zip', root_dir=str(DIST_DIR), base_dir='Finzytrack')

    print(f'\n==> Done. Archive: {archive_path}.zip')
    print('    Extract and run Finzytrack/Finzytrack.exe')


def main():
    parser = argparse.ArgumentParser(description='Build Finzytrack desktop app')
    parser.add_argument('--clean', action='store_true', help='Remove build/dist before building')
    parser.add_argument('--skip-frontend', action='store_true',
                        help='Skip frontend build (use existing frontend/dist)')
    args = parser.parse_args()

    check_venv()

    if args.clean:
        clean()

    ensure_icons()

    # AI reference sync + schema-driven codegen must happen before the frontend
    # build (which compiles recipes.generated.ts) and before PyInstaller (which
    # bundles backend/resources/ai_reference and backend/resources/schemas).
    sync_ai_resources()

    if not args.skip_frontend:
        build_frontend()

    run_pyinstaller()

    system = platform.system()
    if system == 'Darwin':
        package_macos()
    elif system == 'Linux':
        package_linux()
    elif system == 'Windows':
        package_windows()
    else:
        print(f'WARNING: No packaging step for {system}. '
              f'Raw output is in dist/Finzytrack/')


if __name__ == '__main__':
    main()
