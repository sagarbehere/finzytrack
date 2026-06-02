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

# On Linux, drop bundled copies of system runtime libraries that must come
# from the user's OS rather than from this bundle. The canonical AppImage
# rule: build on the oldest distro you want to support, then let the user's
# OS supply C/C++ runtime, GLib/GObject, GTK, Pango, Cairo, X11, etc. These
# libraries are forward-compatible (an app built against an older version
# runs fine against a newer one), but the reverse fails when *other* system
# libraries demand newer symbols than the bundled copy provides — e.g.
# libwebkit2gtk → libgudev → GLib's g_once_init_enter_pointer (added in
# 2.80). Bundling our older 22.04 copy of GLib breaks this chain on Debian
# 13 etc.; bundling our older libstdc++ breaks ICU's newer CXXABI symbols.
# Mirrors the AppImage excludelist subset relevant to GTK apps.
if sys.platform.startswith('linux'):
    _LINUX_SYSTEM_LIBS = {
        # C / C++ runtime
        'libstdc++.so.6', 'libgcc_s.so.1', 'libc.so.6', 'libm.so.6',
        'libpthread.so.0', 'libdl.so.2', 'librt.so.1', 'libutil.so.1',
        'libresolv.so.2', 'ld-linux-x86-64.so.2',
        # GLib / GObject / GIO
        'libglib-2.0.so.0', 'libgobject-2.0.so.0', 'libgio-2.0.so.0',
        'libgmodule-2.0.so.0', 'libgthread-2.0.so.0', 'libffi.so.7',
        'libffi.so.8', 'libgirepository-1.0.so.1',
        # GTK / Pango / Cairo / GDK-Pixbuf
        'libgtk-3.so.0', 'libgdk-3.so.0', 'libgdk_pixbuf-2.0.so.0',
        'libpango-1.0.so.0', 'libpangocairo-1.0.so.0', 'libpangoft2-1.0.so.0',
        'libcairo.so.2', 'libcairo-gobject.so.2', 'libatk-1.0.so.0',
        'libepoxy.so.0',
        # X11 / Wayland
        'libX11.so.6', 'libxcb.so.1', 'libxcb-render.so.0', 'libxcb-shm.so.0',
        'libXrandr.so.2', 'libXrender.so.1', 'libXfixes.so.3', 'libXext.so.6',
        'libXi.so.6', 'libXcomposite.so.1', 'libXcursor.so.1',
        'libXdamage.so.1', 'libXinerama.so.1', 'libXtst.so.6',
        'libwayland-client.so.0', 'libwayland-cursor.so.0',
        'libwayland-egl.so.1', 'libwayland-server.so.0',
        # Common system services
        'libdbus-1.so.3', 'libfontconfig.so.1', 'libfreetype.so.6',
        'libharfbuzz.so.0', 'libharfbuzz-gobject.so.0', 'libxml2.so.2',
        'libz.so.1', 'libzstd.so.1', 'libudev.so.1', 'libsystemd.so.0',
        'libdrm.so.2', 'libgbm.so.1', 'libEGL.so.1', 'libGL.so.1',
        'libGLX.so.0', 'libOpenGL.so.0',
    }
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
