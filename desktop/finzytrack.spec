# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Finzytrack desktop app.

Build with:  pyinstaller finzytrack.spec
Output:      dist/Finzytrack.app  (macOS .app bundle)
"""

import os
import sys
import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

# Platform-specific icon — built from assets/icons/ at build time
ICONS = Path('..') / 'assets' / 'icons'
if sys.platform == 'darwin':
    ICON = str(Path('build') / 'icon.icns')
    iconset = ICONS / 'macos' / 'AppIcon.iconset'
    if iconset.exists():
        Path('build').mkdir(exist_ok=True)
        os.system(f'iconutil -c icns "{iconset}" -o "{ICON}"')
elif sys.platform == 'win32':
    ICON = str(ICONS / 'windows' / 'app.ico')
else:
    ICON = None  # Linux: icons set via .desktop file, not embedded in binary

ROOT = Path('..')

# Single source of truth for the app version — see /VERSION
VERSION = (ROOT / 'VERSION').read_text().strip()

# Find the active venv's site-packages. Use sysconfig instead of
# site.getsitepackages()[0] — on Windows the latter returns the install
# prefix first and Lib/site-packages second, so [0] points at the wrong
# directory and the beancount/VERSION data file can't be located.
import sysconfig
SITE_PACKAGES = Path(sysconfig.get_paths()['purelib'])

# Collect all beancount and beanquery submodules (many are dynamically imported)
beancount_hidden = collect_submodules('beancount')
beanquery_hidden = collect_submodules('beanquery')

# pywebview on Windows uses pythonnet + clr_loader to drive the EdgeChromium
# WebView2 control via the .NET CLR. Those packages ship native DLLs and
# runtime support files that PyInstaller's default analysis misses, which
# causes "Failed to resolve Python.Runtime.Loader.Initialize" when the
# window tries to render. collect_all pulls in every binary, data file,
# and submodule for each. (Harmless on macOS/Linux where pywebview uses
# native backends and these collections are empty/no-ops.)
webview_datas, webview_binaries, webview_hidden = collect_all('webview')
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all('pythonnet')
clr_loader_datas, clr_loader_binaries, clr_loader_hidden = collect_all('clr_loader')

BACKEND = ROOT / 'backend'
FRONTEND_DIST = ROOT / 'frontend' / 'dist'

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[str(BACKEND)],
    binaries=[
        *webview_binaries,
        *pythonnet_binaries,
        *clr_loader_binaries,
    ],
    datas=[
        *webview_datas,
        *pythonnet_datas,
        *clr_loader_datas,
        # Seed config template (copied to user's config/ on first run)
        (str(BACKEND / 'resources' / 'seed_config'), 'backend/seed_config'),
        # Seed data template (copied to user's data/ on first run)
        (str(BACKEND / 'resources' / 'seed_data'), 'backend/seed_data'),
        # Rule + ledger templates (YAML/beancount data files inside the Python package)
        (str(BACKEND / 'app' / 'templates'), 'app/templates'),
        # AI prompt templates
        (str(BACKEND / 'resources' / 'prompts'), 'resources/prompts'),
        # AI reference source files (synced from frontend/ by scripts/sync_ai_reference.py)
        (str(BACKEND / 'resources' / 'ai_reference'), 'resources/ai_reference'),
        # JSON Schemas — recipe_validation.py reads recipe.schema.json from here at runtime
        (str(BACKEND / 'resources' / 'schemas'), 'resources/schemas'),
        # Frontend built assets
        (str(FRONTEND_DIST), 'frontend_dist'),
        # beancount VERSION file (read at import time)
        (str(SITE_PACKAGES / 'beancount' / 'VERSION'), 'beancount'),
        # Finzytrack VERSION file — read by backend/app/_version.py at runtime
        (str(ROOT / 'VERSION'), '.'),
    ],
    hiddenimports=[
        *beancount_hidden,
        *beanquery_hidden,
        *webview_hidden,
        *pythonnet_hidden,
        *clr_loader_hidden,
        # FastAPI / uvicorn
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # scikit-learn
        'sklearn',
        'sklearn.utils._cython_blas',
        'sklearn.neighbors._quad_tree',
        'sklearn.tree._utils',
        # other
        'ruamel.yaml',
        'rapidfuzz',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mypy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# On Linux, drop bundled copies of the C/C++ runtime libraries. PyInstaller
# picks these up from the build machine, but bundling an older copy than
# the user's system causes "version `CXXABI_x.y.z' not found" failures when
# the user's other system libraries (e.g. libwebkit2gtk → libicui18n) demand
# a newer C++ ABI than we ship. The canonical AppImage rule is "build on the
# oldest distro you want to support, but let the user's OS supply the
# C++ runtime" — these libraries are forward-compatible.
if sys.platform.startswith('linux'):
    _LINUX_SYSTEM_LIBS = {'libstdc++.so.6', 'libgcc_s.so.1'}
    a.binaries = [b for b in a.binaries
                  if Path(b[0]).name not in _LINUX_SYSTEM_LIBS]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Finzytrack',
    debug=False,
    bootloader_ignore_signals=False,
    # strip / UPX both corrupt python3xx.dll on Windows when MinGW's strip
    # or UPX is on PATH (which it is on the GitHub windows-latest runner),
    # producing "LoadLibrary: Invalid access to memory location" at startup.
    # The size savings aren't worth the breakage on any platform.
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Finzytrack',
)

# macOS .app bundle — Gatekeeper verifies the bundle signature once
# instead of scanning every individual Mach-O binary on first launch.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Finzytrack.app',
        bundle_identifier='com.finzytrack.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': VERSION,
        },
        icon=ICON,
    )
