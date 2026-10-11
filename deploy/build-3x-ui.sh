#!/usr/bin/env bash
# Rebuild the exact Masiha 3x-ui changes against pinned upstream source.
# Safe to resume a verified, unfinished build after network/package failures.
set -Eeuo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM="https://github.com/MHSanaei/3x-ui.git"
PIN="7ef22f94c950ff09f0870e2295fa65ad5968742c"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-20261011}"
PATCH="$REPO_ROOT/deploy/3x-ui.patch"
SRC="$BUILD_ROOT/src"
command -v git >/dev/null
command -v go >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
# Use HTTPS module proxies that were verified to carry the required versions.
# Go's '|' separator (not ',') falls back on ALL HTTP failures including 403.
# Existing go.sum entries and the default checksum database remain enforced.
# Supply MASIHA_GOPROXY to override on an unusual network.
DEFAULT_GOPROXY='https://goproxy.cn|https://mirrors.aliyun.com/goproxy|https://proxy.golang.org|direct'
export GOPROXY="${MASIHA_GOPROXY:-${GOPROXY:-$DEFAULT_GOPROXY}}"
echo "Go module proxy chain: $GOPROXY"
mkdir -p "$BUILD_ROOT"
if [[ -d "$SRC/.git" ]]; then
  echo "Existing build source found; checking pinned commit and Masiha patch before resuming."
  actual="$(git -C "$SRC" rev-parse HEAD)"
  [[ "$actual" == "$PIN" ]] || {
    echo "Build checkout HEAD $actual does not match pinned $PIN; refusing to modify." >&2
    exit 1
  }
  git -C "$SRC" apply --reverse --check "$PATCH" || {
    echo "Existing build source does not match the expected Masiha patch; refusing to resume." >&2
    exit 1
  }
  echo "Verified pinned source and applied patch. Resuming existing build."
elif [[ -e "$SRC" ]]; then
  echo "Existing source directory is not a valid git checkout: $SRC" >&2
  exit 1
else
  git clone --filter=blob:none "$UPSTREAM" "$SRC"
  git -C "$SRC" checkout --detach "$PIN"
  git -C "$SRC" apply --check "$PATCH"
  git -C "$SRC" apply "$PATCH"
  git -C "$SRC" diff --check
fi
cd "$SRC"
if [[ -s internal/web/dist/index.html && -d frontend/node_modules &&
      -x frontend/node_modules/.bin/vite &&
      -n "$(find internal/web/dist/assets -maxdepth 1 -type f -name '*.js' -print -quit 2>/dev/null)" ]]; then
  echo "Frontend assets already compiled in verified patched source; retaining them."
else
  cd frontend
  npm ci --no-audit --no-fund
  npm run typecheck
  npm run build
  cd ..
fi
# Incomplete Go downloads are cached by Go and resumed from alternate mirrors.
# Only the final binary is replaced; a failed build leaves the source intact.
if [[ -s "$BUILD_ROOT/x-ui" && -s "$BUILD_ROOT/x-ui.sha256" ]] &&
   (cd "$BUILD_ROOT" && sha256sum -c --status x-ui.sha256); then
  echo "Using previously verified built binary: $BUILD_ROOT/x-ui"
else
  rm -f "$BUILD_ROOT/x-ui.new"
  go build -o "$BUILD_ROOT/x-ui.new" .
  chmod 0755 "$BUILD_ROOT/x-ui.new"
  mv -f "$BUILD_ROOT/x-ui.new" "$BUILD_ROOT/x-ui"
  (cd "$BUILD_ROOT" && sha256sum x-ui > x-ui.sha256)
fi
printf 'BUILD PASS: %s\n' "$BUILD_ROOT/x-ui"
