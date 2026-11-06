#!/usr/bin/env bash
# Prepare the pinned 3x-ui toolchains and source WITHOUT reinstalling services.
# Safe for an already installed Masiha VPN host; does not alter live databases,
# nginx, SSL, Xray runtime, bot workers, or /usr/local/x-ui.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${MASIHA_BUILD_ROOT:-/opt/masiha-portable-build-v3.11.0}"
TOOLS=/opt/masiha-vpn-toolchains
PIN=c621ba9014a6aa21fd7c9ecf45655e44e24ebf13
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 2; }
[[ -s "$ROOT/deploy/3x-ui.patch" && -x "$ROOT/deploy/build-3x-ui.sh" ]] || {
 echo 'Incomplete Masiha release checkout' >&2;exit 1;
}
for name in git curl tar xz sha256sum timeout;do
 command -v "$name" >/dev/null || { echo "Missing tool: $name" >&2;exit 1; }
done
mkdir -p "$TOOLS" "$TARGET"
download_checked() {
 local output="$1" expected="$2" url tmp="${1}.partial" protocol
 shift 2
 if [[ -s "$output" ]] &&
    printf '%s  %s\n' "$expected" "$output" | sha256sum --check --status;then
  echo "Using SHA256-verified cached $(basename "$output")"
  return 0
 fi
 for url in "$@";do
  for protocol in 4 6;do
   [[ "$url" == https://* ]] || { echo 'Non-HTTPS download rejected' >&2;exit 1; }
   rm -f "$tmp"
   echo "Downloading $(basename "$output") from $url (IPv$protocol)"
   if curl "-$protocol" --fail --location --retry 1 --connect-timeout 12 --max-time 300 \
      -o "$tmp" "$url" &&
      printf '%s  %s\n' "$expected" "$tmp" | sha256sum --check --status;then
    mv -f "$tmp" "$output"
    echo "Verified SHA256: $(basename "$output")"
    return 0
   fi
  done
 done
 rm -f "$tmp"
 echo "Could not obtain verified $(basename "$output"); no live services changed." >&2
 return 1
}
if [[ "${MASIHA_FRONTEND_ONLY:-0}" != "1" && ! -x "$TOOLS/go1.27.3/bin/go" ]];then
 download_checked "$TOOLS/go1.27.3.tar.gz" \
  65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40 \
  https://go.dev/dl/go1.27.3.linux-amd64.tar.gz \
  https://dl.google.com/go/go1.27.3.linux-amd64.tar.gz \
  https://mirrors.aliyun.com/golang/go1.27.3.linux-amd64.tar.gz
 install -d -m 0755 "$TOOLS/go1.27.3"
 tar -xzf "$TOOLS/go1.27.3.tar.gz" -C "$TOOLS/go1.27.3" --strip-components=1
fi
if [[ ! -x "$TOOLS/node-v26.13.0-linux-x64/bin/node" ]];then
 download_checked "$TOOLS/node-v26.13.0.tar.xz" \
  d28a96af6c38596f6d9d97c0ee531c13d238d757742387d1928de822d959e997 \
  https://nodejs.org/dist/v26.13.0/node-v26.13.0-linux-x64.tar.xz \
  https://nodejs.org/download/release/v26.13.0/node-v26.13.0-linux-x64.tar.xz \
  https://mirrors.aliyun.com/nodejs-release/v26.13.0/node-v26.13.0-linux-x64.tar.xz
 tar -xJf "$TOOLS/node-v26.13.0.tar.xz" -C "$TOOLS"
fi
export PATH="$TOOLS/go1.27.3/bin:$TOOLS/node-v26.13.0-linux-x64/bin:$PATH"
if [[ "${MASIHA_FRONTEND_ONLY:-0}" != "1" ]];then
  [[ "$(go version)" == *'go1.27.3'* ]] || { echo 'Unexpected Go version' >&2;exit 1; }
fi
[[ "$(node --version)" == 'v26.13.0' ]] || { echo 'Unexpected Node version' >&2;exit 1; }
command -v npm >/dev/null || { echo 'npm binary missing' >&2; exit 1; }
echo 'Preparing pinned 3x-ui source, dependencies and verified binary offline from live services.'
MASIHA_BUILD_ROOT="$TARGET" bash "$ROOT/deploy/build-3x-ui.sh"
[[ -s "$TARGET/src/go.mod" && -x "$TARGET/src/frontend/node_modules/.bin/vite" ]] || {
 echo 'Build source is incomplete' >&2;exit 1;
}
if [[ "${MASIHA_FRONTEND_ONLY:-0}" == "1" ]];then
  echo "PORTABLE_FRONTEND_PREPARED=$TARGET"
  echo 'GO_DOWNLOADS_AND_COMPILATION_SKIPPED'
  exit 0
fi
[[ -s "$TARGET/x-ui" && -s "$TARGET/x-ui.sha256" && -s "$TARGET/source-patch.sha256" ]] || {
 echo 'Compiled binary or build provenance missing' >&2;exit 1;
}
(cd "$TARGET" && sha256sum -c --status x-ui.sha256)
EXPECTED="$PIN $(sha256sum "$ROOT/deploy/3x-ui.patch" | awk '{print $1}')"
[[ "$(cat "$TARGET/source-patch.sha256")" == "$EXPECTED" ]] || {
 echo 'Binary does not match current Masiha source patch' >&2;exit 1;
}
[[ "$("$TARGET/x-ui" -v 2>&1 | head -1)" == "3.11.0" ]] || {
 echo 'Compiled 3x-ui version mismatch' >&2;exit 1;
}
echo "PORTABLE_BUILD_PREPARED=$TARGET"
echo 'NO_LIVE_SERVICES_CHANGED'
