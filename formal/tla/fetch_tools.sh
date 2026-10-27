#!/bin/sh
# Download tla2tools.jar (the version in tools.lock) into formal/tla/tools/ and verify its SHA-256.
# The jar is not committed (.gitignore). Run from anywhere: sh formal/tla/fetch_tools.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LOCK="$HERE/tools.lock"
JAR="$HERE/tools/tla2tools.jar"
VERSION=$(awk '$1 == "tla2tools.jar" {print $2}' "$LOCK")
WANT=$(awk '$1 == "tla2tools.jar" {print $3}' "$LOCK")
URL="https://github.com/tlaplus/tlaplus/releases/download/v$VERSION/tla2tools.jar"

sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else sha256sum "$1" | awk '{print $1}'; fi
}

if [ -f "$JAR" ] && [ "$(sha256 "$JAR")" = "$WANT" ]; then
  echo "tla2tools.jar $VERSION already present and verified"
  exit 0
fi
mkdir -p "$HERE/tools"
PART="$JAR.part"
rm -f "$PART"
echo "downloading tla2tools.jar $VERSION"
curl -fsSL -o "$PART" "$URL"
GOT=$(sha256 "$PART")
if [ "$GOT" != "$WANT" ]; then
  rm -f "$PART"
  echo "checksum mismatch: got $GOT, tools.lock pins $WANT; nothing installed" >&2
  exit 1
fi
mv "$PART" "$JAR"
echo "tla2tools.jar $VERSION verified ($GOT)"
