#!/usr/bin/env bash
# Rebuild the exact Masiha 3x-ui changes against pinned upstream source.
set -Eeuo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM="https://github.com/MHSanaei/3x-ui.git"
PIN="7ef22f94c950ff09f0870e2295fa65ad5968742c"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-20261011}"
if [[ -e "$BUILD_ROOT/src" ]]; then
  echo "Refusing to modify an existing build tree: $BUILD_ROOT/src" >&2
  exit 1
fi
command -v git >/dev/null
command -v go >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
mkdir -p "$BUILD_ROOT"
git clone --filter=blob:none "$UPSTREAM" "$BUILD_ROOT/src"
cd "$BUILD_ROOT/src"
git checkout --detach "$PIN"
git apply --check "$REPO_ROOT/deploy/3x-ui.patch"
git apply "$REPO_ROOT/deploy/3x-ui.patch"
git diff --check
cd frontend
npm ci --no-audit --no-fund
npm run typecheck
npm run build
cd ..
go build -o "$BUILD_ROOT/x-ui" .
chmod 0755 "$BUILD_ROOT/x-ui"
sha256sum "$BUILD_ROOT/x-ui" > "$BUILD_ROOT/x-ui.sha256"
printf 'BUILD PASS: %s\n' "$BUILD_ROOT/x-ui"
