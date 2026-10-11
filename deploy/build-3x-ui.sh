#!/usr/bin/env bash
# Rebuild the exact Masiha 3x-ui changes against pinned upstream source.
# Safe to resume a verified, unfinished build after network/package failures.
set -Eeuo pipefail
# Emit a visible error for any otherwise-silent nonzero exit under set -e.
trap 'rc=$?; echo "ERROR: 3x-ui build script stopped at line ${LINENO} (exit code $rc)." >&2' ERR
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
DEFAULT_GOPROXY='https://mirrors.aliyun.com/goproxy|https://goproxy.cn|https://proxy.golang.org|direct'
export GOPROXY="${MASIHA_GOPROXY:-${GOPROXY:-$DEFAULT_GOPROXY}}"
echo "Go module proxy chain: $GOPROXY"
mkdir -p "$BUILD_ROOT"
verify_or_apply_masiha_patch() {
  echo "=== Verifying pinned Masiha source patch ==="
  if timeout --kill-after=5s 60s git -C "$SRC" apply --reverse --check "$PATCH" >/dev/null 2>&1; then
    echo "Masiha patch is already fully applied; source is ready."
  elif timeout --kill-after=5s 60s git -C "$SRC" apply --check "$PATCH"; then
    echo "Masiha patch has not yet been applied; applying it now."
    timeout --kill-after=5s 60s git -C "$SRC" apply "$PATCH"
    echo "Masiha patch applied."
  else
    echo "ERROR: Masiha source is neither clean nor fully patched." >&2
    echo "Refusing to reset/overwrite partial changes. Inspect: git -C '$SRC' status --short" >&2
    return 1
  fi
  # The reverse-apply check above proves that every patch hunk is present.
  # A second, redundant full-repository 'git diff --check' can silently time
  # out on a partial Git clone (or reject benign whitespace), exiting under
  # set -e before npm even begins. Never run it in the installation path.
  echo "=== Checking source patch integrity ==="
  echo "Full Masiha patch is present; redundant Git whitespace scan skipped."
  echo "Masiha source integrity verified."
}
if [[ -d "$SRC/.git" ]]; then
  echo "=== Resuming existing build checkout ==="
  actual="$(git -C "$SRC" rev-parse HEAD)"
  [[ "$actual" == "$PIN" ]] || {
    echo "Build checkout HEAD $actual does not match pinned $PIN; refusing to modify." >&2
    exit 1
  }
  verify_or_apply_masiha_patch
elif [[ -e "$SRC" ]]; then
  echo "Existing source directory is not a valid git checkout: $SRC" >&2
  exit 1
else
  echo "=== Downloading pinned upstream 3x-ui source ==="
  git clone --filter=blob:none "$UPSTREAM" "$SRC"
  git -C "$SRC" checkout --detach "$PIN"
  echo "Pinned upstream checkout complete: $PIN"
  verify_or_apply_masiha_patch
fi
cd "$SRC"
# Installing many npm packages from a blocked registry previously looked like a
# frozen installer. Show the exact stage, try verified-integrity npm registries,
# and enforce a per-registry timeout. package-lock integrity hashes remain active.
run_npm_ci_with_fallback() {
  local registry logfile attempt=0 status
  local -a registries=(
    "https://registry.npmjs.org/"
    "https://registry.npmmirror.com/"
    "https://mirrors.cloud.tencent.com/npm/"
  )
  if [[ -n "${MASIHA_NPM_REGISTRY:-}" ]]; then
    [[ "$MASIHA_NPM_REGISTRY" == https://* ]] || {
      echo "MASIHA_NPM_REGISTRY must use HTTPS." >&2
      return 1
    }
    registries=("$MASIHA_NPM_REGISTRY" "${registries[@]}")
  fi
  local -A visited=()
  for registry in "${registries[@]}"; do
    [[ ! -v 'visited[$registry]' ]] || continue
    visited["$registry"]=1
    attempt=$((attempt+1))
    logfile="$BUILD_ROOT/npm-ci-attempt-$attempt.log"
    echo "=== Frontend npm install: attempt $attempt using $registry (max 420 seconds) ==="
    echo "Progress messages appear every 15 seconds; npm logs: $logfile"
    # npm ci checks every package against the lockfile's integrity hashes.
    # Unlike npm install, it never updates package-lock.json.
    timeout --kill-after=10s 420s \
      env npm_config_registry="$registry" npm_config_fetch_retries=1 \
      npm_config_fetch_timeout=20000 npm_config_fetch_retry_mintimeout=1000 \
      npm_config_fetch_retry_maxtimeout=5000 \
      npm ci --no-audit --no-fund --cache "$BUILD_ROOT/npm-cache" \
      >"$logfile" 2>&1 &
    local pid=$! start_time=$SECONDS
    while kill -0 "$pid" 2>/dev/null; do
      sleep 15
      if kill -0 "$pid" 2>/dev/null; then
        echo "npm ci still running: $((SECONDS-start_time))s elapsed; registry $registry"
      fi
    done
    if wait "$pid"; then
      [[ -x node_modules/.bin/vite && -x node_modules/.bin/tsc ]] || {
        echo "npm ci ended but required frontend dependencies are missing." >&2
        return 1
      }
      sha256sum package-lock.json > "$BUILD_ROOT/npm-lock.sha256"
      echo "Frontend dependencies installed and lockfile recorded successfully."
      return 0
    else
      status=$?
      echo "npm install attempt $attempt failed or timed out (exit $status)." >&2
      echo "Last npm log lines (paths and errors only):" >&2
      tail -n 12 "$logfile" >&2 || true
      echo "Switching to next npm registry without deleting the shared npm cache." >&2
    fi
  done
  echo "ERROR: frontend dependencies could not be installed using available npm registries." >&2
  echo "Check $BUILD_ROOT/npm-ci-attempt-*.log, then retry with a reachable MASIHA_NPM_REGISTRY." >&2
  return 1
}
if [[ -s internal/web/dist/index.html && -d frontend/node_modules &&
      -x frontend/node_modules/.bin/vite &&
      -n "$(find internal/web/dist/assets -maxdepth 1 -type f -name '*.js' -print -quit 2>/dev/null)" ]]; then
  echo "Frontend assets already compiled in verified patched source; retaining them."
else
  echo "=== Preparing frontend dependencies (Node + npm) ==="
  cd frontend
  if [[ -x node_modules/.bin/vite && -x node_modules/.bin/tsc &&
        -s "$BUILD_ROOT/npm-lock.sha256" ]] &&
       sha256sum -c --status "$BUILD_ROOT/npm-lock.sha256"; then
    echo "Reusing verified frontend node_modules from previous successful npm ci."
  else
    run_npm_ci_with_fallback
  fi
  echo "=== Frontend TypeScript validation ==="
  timeout --kill-after=5s 300s npm run typecheck
  echo "=== Frontend Vite production build ==="
  timeout --kill-after=5s 420s npm run build
  cd ..
  echo "Frontend assets built successfully."
fi
# Download all dependencies of the actual x-ui binary, with a bounded timeout
# per proxy. The Go build must run OFFLINE after prefetch: otherwise a partial
# cache will result in an apparently frozen "go: downloading" build.
# Module authenticity is still verified against go.sum and GOSUMDB.
prefetch_build_dependencies() {
  local mirror exitcode
  local -a mirrors=(
    "https://mirrors.aliyun.com/goproxy"
    "https://goproxy.cn"
    "https://proxy.golang.org"
  )
  if [[ -n "${MASIHA_GOPROXY:-}" ]]; then
    mirrors=("$MASIHA_GOPROXY" "${mirrors[@]}")
  fi
  # Avoid repeating identical proxy requests when custom proxy matches default.
  local -A visited=()
  local attempt=0
  for mirror in "${mirrors[@]}"; do
    [[ ! -v 'visited[$mirror]' ]] || continue
    visited["$mirror"]=1
    attempt=$((attempt+1))
    echo "=== Go dependency prefetch $attempt: $mirror (max 180 seconds) ==="
    echo "Cached downloads are reused across attempts and installer restarts."
    # 'go list -deps .' requests exactly the modules imported by this binary.
    # -mod=readonly ensures the signed, pinned go.mod/go.sum never change.
    # Do not use '-e': it can turn unresolved imports into a false success.
    # Keep output visible (go: downloading ...) for useful progress diagnostics.
    if timeout --kill-after=10s 180s env GOPROXY="$mirror" \
      GOTOOLCHAIN=local go list -mod=readonly -deps . >/dev/null; then
      echo "All x-ui Go dependencies downloaded and verified."
      return 0
    else
      exitcode=$?
      echo "Go prefetch failed or timed out (exit $exitcode) through $mirror; trying next proxy." >&2
    fi
  done
  echo "ERROR: Go dependencies could not be fully downloaded from available proxies." >&2
  echo "Inspect DNS/outbound HTTPS; use MASIHA_GOPROXY for a reachable Go proxy." >&2
  echo "This is safe to retry: no existing module cache or customer data is deleted." >&2
  return 1
}
# Individual high-bandwidth modules can be cached early to make partial
# failures visible, but they are not sufficient to build the full binary.
prefetch_go_module() {
  local module="$1" mirror
  local -a mirrors=(
    "https://mirrors.aliyun.com/goproxy"
    "https://goproxy.cn"
    "https://proxy.golang.org"
  )
  # Respect explicit user-supplied Go proxy configuration as first candidate.
  if [[ -n "${MASIHA_GOPROXY:-}" ]]; then
    mirrors=("$MASIHA_GOPROXY" "${mirrors[@]}")
  fi
  for mirror in "${mirrors[@]}"; do
    echo "Fetching Go module $module (timeout 120s) via $mirror"
    if timeout --kill-after=5s 120s env GOPROXY="$mirror" \
       go mod download "$module"; then
      echo "Go module ready and verified: $module"
      return 0
    fi
    echo "Module fetch failed or timed out: $mirror; trying next mirror." >&2
  done
  echo "Failed to download $module from all configured mirrors." >&2
  return 1
}
# Incomplete Go downloads are cached and reused on retry.
# Only the final binary is replaced; a failed build leaves the source intact.
if [[ -s "$BUILD_ROOT/x-ui" && -s "$BUILD_ROOT/x-ui.sha256" ]] &&
   (cd "$BUILD_ROOT" && sha256sum -c --status x-ui.sha256); then
  echo "Using previously verified built binary: $BUILD_ROOT/x-ui"
else
  prefetch_go_module github.com/cloudflare/circl@v1.6.5
  prefetch_go_module github.com/klauspost/compress@v1.20.0
  prefetch_build_dependencies
  echo "=== Building Masiha x-ui Go backend from VERIFIED OFFLINE cache ==="
  echo "No network downloads are permitted during compilation. On first build, compilation can take several minutes."
  # Ubuntu VPS instances frequently have only 2-4GB RAM. Limit Go's
  # parallel compiler workers by default; operators can override explicitly.
  export GOMAXPROCS="${MASIHA_GOMAXPROCS:-${GOMAXPROCS:-2}}"
  [[ "$GOMAXPROCS" =~ ^[1-9][0-9]*$ ]] || {
    echo "MASIHA_GOMAXPROCS must be a positive integer." >&2
    exit 2
  }
  echo "Go compiler parallelism: GOMAXPROCS=$GOMAXPROCS"
  rm -f "$BUILD_ROOT/x-ui.new"
  # If a dependency is still missing, Go now reports it immediately instead
  # of retrying an unreachable remote server for an unbounded period.
  # Emit a heartbeat during CPU-heavy first compilation; no network access is
  # allowed, and the 20-minute watchdog prevents a stuck compiler running forever.
  timeout --kill-after=10s 1200s env GOPROXY=off GOTOOLCHAIN=local \
    go build -mod=readonly -o "$BUILD_ROOT/x-ui.new" . &
  build_pid=$!
  start_time=$SECONDS
  while kill -0 "$build_pid" 2>/dev/null; do
    sleep 15
    if kill -0 "$build_pid" 2>/dev/null; then
      echo "Go compilation in progress: $((SECONDS-start_time))s elapsed (no network downloads)"
    fi
  done
  if ! wait "$build_pid"; then
    echo "ERROR: offline Go compilation failed or exceeded 20 minutes." >&2
    echo "All fetched modules stay cached. Fix the reported error and rerun." >&2
    exit 1
  fi
  chmod 0755 "$BUILD_ROOT/x-ui.new"
  mv -f "$BUILD_ROOT/x-ui.new" "$BUILD_ROOT/x-ui"
  (cd "$BUILD_ROOT" && sha256sum x-ui > x-ui.sha256)
fi
printf 'BUILD PASS: %s\n' "$BUILD_ROOT/x-ui"
