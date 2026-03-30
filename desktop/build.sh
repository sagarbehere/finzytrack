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

echo ""
echo "==> Done. Bundle is at: $SCRIPT_DIR/dist/FinzyTrack/"
echo "    To run: ./dist/FinzyTrack/FinzyTrack"
