#!/bin/bash
# Build FinzyTrack desktop app using PyInstaller.
#
# Usage:
#   source ../venv/bin/activate
#   cd desktop/
#   ./build.sh [--clean]
#
set -e

# Sanity check: ensure we're running inside the finzytrack venv
if [[ "$VIRTUAL_ENV" != *"finzytrack/venv"* ]]; then
    echo "ERROR: finzytrack venv is not active."
    echo "Run: source ../venv/bin/activate"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

if [[ "$1" == "--clean" ]]; then
    echo "==> Cleaning previous build artifacts..."
    rm -rf "$SCRIPT_DIR/build" "$SCRIPT_DIR/dist"
fi

echo "==> Building Vue frontend..."
cd "$FRONTEND_DIR"
npm run build
echo "    Frontend built: $FRONTEND_DIR/dist"

echo "==> Running PyInstaller..."
cd "$SCRIPT_DIR"
pyinstaller finzytrack.spec --noconfirm

echo "==> Signing .app bundle (ad-hoc)..."
# Sign all nested Mach-O binaries first, then the bundle itself.
# A signed .app bundle is verified once by Gatekeeper, avoiding the
# per-binary scan that causes a long first-launch delay.
codesign --force --deep --sign - "$SCRIPT_DIR/dist/FinzyTrack.app"

echo "==> Removing quarantine attribute..."
xattr -cr "$SCRIPT_DIR/dist/FinzyTrack.app"

echo ""
echo "==> Done. App bundle is at: $SCRIPT_DIR/dist/FinzyTrack.app"
echo "    To run: open dist/FinzyTrack.app"
