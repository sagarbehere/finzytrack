# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for FinzyTrack desktop app.

Build with:  pyinstaller finzytrack.spec
Output:      dist/FinzyTrack.app  (macOS .app bundle)
"""

import os
import sys
import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Platform-specific icon
ICONS = Path('..') / 'assets' / 'icons'
if sys.platform == 'darwin':
    ICON = 'icon.icns'
elif sys.platform == 'win32':
    ICON = str(ICONS / 'windows' / 'app.ico')
else:
    ICON = None  # Linux: icons set via .desktop file, not embedded in binary

ROOT = Path('..')

# Find the active venv's site-packages
import site
SITE_PACKAGES = Path(site.getsitepackages()[0])

# Collect all beancount and beanquery submodules (many are dynamically imported)
beancount_hidden = collect_submodules('beancount')
beanquery_hidden = collect_submodules('beanquery')
BACKEND = ROOT / 'backend'
FRONTEND_DIST = ROOT / 'frontend' / 'dist'

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        # Seed config template (copied to user's config/ on first run)
        (str(BACKEND / 'resources' / 'seed_config'), 'backend/seed_config'),
        # Seed data template (copied to user's data/ on first run)
        (str(BACKEND / 'resources' / 'seed_data'), 'backend/seed_data'),
        # Frontend built assets
        (str(FRONTEND_DIST), 'frontend_dist'),
        # beancount VERSION file (read at import time)
        (str(SITE_PACKAGES / 'beancount' / 'VERSION'), 'beancount'),
    ],
    hiddenimports=[
        *beancount_hidden,
        *beanquery_hidden,
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
        'sklearn.neighbors._typedefs',
        'sklearn.neighbors._quad_tree',
        'sklearn.tree._utils',
        # other
        'ruamel.yaml',
        'ruamel.yaml.clib',
        'duckdb',
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FinzyTrack',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=False,
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
    strip=True,
    upx=True,
    upx_exclude=[],
    name='FinzyTrack',
)

# macOS .app bundle — Gatekeeper verifies the bundle signature once
# instead of scanning every individual Mach-O binary on first launch.
app = BUNDLE(
    coll,
    name='FinzyTrack.app',
    bundle_identifier='com.finzytrack.app',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '0.1.0',
    },
    icon='icon.icns',
)
