#!/usr/bin/env bash
# Upgrade ONLY backup/restore on an already-installed Masiha VPN node.
# Do NOT run the full fresh installer on a node containing client data.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-v3.11.0}"
SRC="$BUILD_ROOT/src"
GO=/opt/masiha-vpn-toolchains/go1.27.3/bin
NODE=/opt/masiha-vpn-toolchains/node-v26.13.0-linux-x64/bin
LIVE=/usr/local/x-ui/x-ui
NGINX=/etc/nginx/sites-available/masiha-vpn
CONFIG=/etc/masiha-vpn-extension/config.json
PIN=c621ba9014a6aa21fd7c9ecf45655e44e24ebf13

[[ "$(id -u)" == "0" ]] || { echo "Run as root (sudo bash)"; exit 1; }
[[ "${1:-}" == "" || "${1:-}" == "--check" || "${1:-}" == "--prepare" ]] || {
 echo "Usage: $0 [--check|--prepare]"; exit 2;
}
# On migrated servers, the running 3x-ui binary is installed but the
# original build source/toolchains can be absent. Use an isolated, resumable
# workspace instead of relying on /opt/.../src or changing any live files.
LEGACY_BUILD_ROOT="$BUILD_ROOT"
PORTABLE_ROOT="${MASIHA_PORTABLE_BUILD_ROOT:-/opt/masiha-portable-build-v3.11.0}"
if [[ -s "$SRC/go.mod" && -s "$SRC/frontend/package-lock.json" &&
      -s "$SRC/internal/web/controller/masiha_backup.go" &&
      -x "$SRC/frontend/node_modules/.bin/vite" ]];then
 SOURCE_ORIGIN=existing
else
 BUILD_ROOT="$PORTABLE_ROOT"
 SRC="$BUILD_ROOT/src"
 SOURCE_ORIGIN=isolated
fi
for path in "$ROOT/sidecar/masiha-vite.config.mjs" "$ROOT/sidecar/masiha-standalone.tsx" \
            "$ROOT/sidecar/masiha-standalone.html" "$ROOT/sidecar/standalone.css" \
            "$ROOT/deploy/prepare-portable-build.sh" "$ROOT/deploy/3x-ui.patch" \
            "$LIVE" "$CONFIG" "$NGINX" \
            "$ROOT/runtime/full_backup.py" "$ROOT/runtime/portable_migration.py" \
            "$ROOT/deploy/native-web/BackupRestorePanel.tsx" "$ROOT/deploy/native-web/masiha_backup.go";do
 [[ -e "$path" ]] || { echo "Missing application file: $path" >&2;exit 1; }
done
if [[ ! -s "$SRC/go.mod" || ! -s "$SRC/frontend/package-lock.json" ||
      ! -s "$SRC/internal/web/controller/masiha_backup.go" ||
      ! -x "$SRC/frontend/node_modules/.bin/vite" ]];then
 SOURCE_NEEDS_PREP=1
else
 SOURCE_NEEDS_PREP=0
fi
if [[ "$SOURCE_NEEDS_PREP" == 0 ]];then
 if [[ -e "$SRC/.git" ]];then
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || {
   echo "Pinned 3x-ui Git revision mismatch; refusing to modify destination." >&2;exit 1;
  }
  echo "Pinned source checkout verified via Git."
 else
  MARKER="$BUILD_ROOT/source-patch.sha256"
  [[ -s "$MARKER" && -s "$BUILD_ROOT/x-ui" && -s "$BUILD_ROOT/x-ui.sha256" ]] || {
   echo "Build provenance missing; use an isolated verified workspace." >&2;exit 1;
  }
  EXPECTED_ID="$PIN $(sha256sum "$ROOT/deploy/3x-ui.patch" | awk '{print $1}')"
  [[ "$(cat "$MARKER")" == "$EXPECTED_ID" ]] || {
   echo "Build provenance differs from current release patch." >&2;exit 1;
  }
  (cd "$BUILD_ROOT" && sha256sum -c --status x-ui.sha256) || {
   echo "Existing build checksum mismatch." >&2;exit 1;
  }
  echo "Pinned non-Git build verified by exact release patch and SHA256."
 fi
else
 echo "Pinned 3x-ui build workspace is not present: $SRC"
 echo "An isolated verified build will be prepared; no live files touched."
fi
prepare_source() {
if [[ "$SOURCE_NEEDS_PREP" == 1 ]];then
 # Compile in a separate tree before replacing anything on the live server.
 # Source, Go and Node caches can be several gigabytes.
 AVAILABLE_KB=$(df -Pk /opt | awk 'NR==2 {print $4}')
 if [[ ! "$AVAILABLE_KB" =~ ^[0-9]+$ || "$AVAILABLE_KB" -lt 5242880 ]];then
  echo "At least 5 GiB free disk is required to bootstrap the pinned build workspace." >&2
  echo "Do not delete active backup archives to create space." >&2
  exit 1
 fi
 echo "=== PREPARING SOURCE / GO / NODE / NPM IN ISOLATED WORKSPACE ==="
 MASIHA_BUILD_ROOT="$BUILD_ROOT" bash "$ROOT/deploy/prepare-portable-build.sh"
fi
[[ -s "$SRC/go.mod" && -s "$SRC/frontend/package-lock.json" &&
    -x "$SRC/frontend/node_modules/.bin/vite" &&
    -s "$SRC/internal/web/controller/masiha_backup.go" ]] || {
 echo "Build preparation incomplete; live services not touched." >&2;exit 1;
}
[[ -x "$GO/go" && -x "$NODE/node" ]] || {
 echo "Pinned toolchains missing; prepare build first." >&2;exit 1;
}
export PATH="$GO:$NODE:$PATH" GOTOOLCHAIN=local GOPROXY=off
}

if [[ "${1:-}" == "--prepare" ]];then
 prepare_source
 echo "PORTABLE_BACKUP_BUILD_PREPARED"
 echo "NO_LIVE_SERVICES_CHANGED"
 exit 0
fi

DOMAIN="$(python3 - "$CONFIG" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["domain"])
PY
)"
[[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]+$ && -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] || {
 echo "Destination domain or TLS missing" >&2; exit 1;
}
MISSING_DBS=0
for dbfile in /etc/x-ui/x-ui.db /etc/masiha-vpn-extension/users.db \
              /etc/masiha-vpn-extension/sales/sales.db;do
 if [[ -s "$dbfile" && -f "$dbfile" && ! -L "$dbfile" ]];then
  echo "DESTINATION_DB_OK=$dbfile"
 else
  echo "DESTINATION_DB_MISSING_OR_EMPTY=$dbfile" >&2
  MISSING_DBS=1
 fi
done
if [[ "$MISSING_DBS" != 0 ]];then
 echo "Source preparation is still available: bash deploy/upgrade-portable-backup.sh --prepare" >&2
 echo "Do not create empty replacement DBs. Check preserved copies or current runtime DB paths." >&2
 exit 1
fi
python3 - <<'PY'
import sqlite3
for p in ("/etc/x-ui/x-ui.db","/etc/masiha-vpn-extension/users.db",
          "/etc/masiha-vpn-extension/sales/sales.db"):
 with sqlite3.connect("file:"+p+"?mode=ro",uri=True,timeout=30) as c:
  if c.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise SystemExit("Invalid target DB: "+p)
print("Destination database integrity check: PASS")
PY
echo "Destination domain will be retained: $DOMAIN"
if [[ "${1:-}" == "--check" ]];then
 if [[ "$SOURCE_NEEDS_PREP" == 1 ]];then
  echo "BUILD_SOURCE_MISSING=YES; bootstrap required before activation"
 fi
 echo "PORTABLE_BACKUP_UPGRADE_CHECK_OK"
 exit 0
fi
prepare_source
STMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLL="/root/masiha-backups/pre-portable-upgrade-$STMP"
install -d -m 0700 "$ROLL"
cp -a "$LIVE" "$ROLL/x-ui"
cp -a "$NGINX" "$ROLL/nginx-site.conf"
for name in full_backup.py portable_migration.py web_backup_worker.py sidecar_server.py;do
 if [[ -f "/opt/masiha-vpn-extension/$name" ]];then
  cp -a "/opt/masiha-vpn-extension/$name" "$ROLL/$name"
 fi
done
cp -a "$SRC/frontend/src/pages/vpn/BackupRestorePanel.tsx" "$ROLL/BackupRestorePanel.tsx"
cp -a "$SRC/internal/web/controller/masiha_backup.go" "$ROLL/masiha_backup.go"
cp -a /var/www/masiha-ui "$ROLL/masiha-ui"
BINARY_SWAPPED=0
CONFIGS_SWAPPED=0
ASSETS_SWAPPED=0
rollback() {
 local rc=$?
 trap - ERR
 echo "Upgrade failed (exit $rc). Restoring previous panel and web assets." >&2
 if [[ "$BINARY_SWAPPED" == "1" ]];then
  cp -a "$ROLL/x-ui" "$LIVE.rollback"
  chmod 0755 "$LIVE.rollback"
  mv -f "$LIVE.rollback" "$LIVE"
  systemctl restart x-ui.service || true
 fi
 if [[ "$ASSETS_SWAPPED" == "1" ]];then
  rm -rf /var/www/masiha-ui
  cp -a "$ROLL/masiha-ui" /var/www/masiha-ui
 fi
 if [[ "$CONFIGS_SWAPPED" == "1" ]];then
  cp -a "$ROLL/nginx-site.conf" "$NGINX"
  for name in full_backup.py portable_migration.py web_backup_worker.py sidecar_server.py;do
   [[ -f "$ROLL/$name" ]] && cp -a "$ROLL/$name" "/opt/masiha-vpn-extension/$name"
  done
  systemctl restart masiha-vpn-sidecar.service || true
  nginx -t && systemctl reload nginx.service || true
 fi
 echo "Rollback preserved at $ROLL" >&2
 exit "$rc"
}
trap rollback ERR

# Source is pinned. Only replace the two native files intentionally changed
# by the backup feature; all S3/custom panel features remain installed.
install -m 0644 "$ROOT/deploy/native-web/BackupRestorePanel.tsx" "$SRC/frontend/src/pages/vpn/BackupRestorePanel.tsx"
install -m 0644 "$ROOT/deploy/native-web/masiha_backup.go" "$SRC/internal/web/controller/masiha_backup.go"
# Standalone sources are installer-owned and may be absent from a pinned
# upstream Go build checkout after a clean source refresh.
install -m 0644 "$ROOT/sidecar/masiha-vite.config.mjs" "$SRC/frontend/masiha-vite.config.mjs"
install -m 0644 "$ROOT/sidecar/masiha-standalone.html" "$SRC/frontend/masiha-standalone.html"
install -m 0644 "$ROOT/sidecar/masiha-standalone.tsx" "$SRC/frontend/src/masiha-standalone.tsx"
install -m 0644 "$ROOT/sidecar/standalone.css" "$SRC/frontend/src/masiha-standalone.css"
echo "Building native and standalone backup screens ..."
ASSETS_SWAPPED=1
(
 cd "$SRC/frontend"
 npm run typecheck
 npm run build
 ./node_modules/.bin/vite build --config masiha-vite.config.mjs
)
# Vite replaces the standalone output folder; reinstall native navigation.
install -m 0644 "$ROOT/sidecar/nav.js" /var/www/masiha-ui/nav.js
install -m 0644 "$ROOT/sidecar/nav.css" /var/www/masiha-ui/nav.css
chmod -R a+rX /var/www/masiha-ui
# The isolated prep has already built a matching native binary and recorded
# its checksum and exact patch identity. Avoid compiling it a second time.
EXPECTED_ID="$PIN $(sha256sum "$ROOT/deploy/3x-ui.patch" | awk '{print $1}')"
if [[ "$SOURCE_ORIGIN" == isolated && -s "$BUILD_ROOT/x-ui" &&
      -s "$BUILD_ROOT/x-ui.sha256" && -s "$BUILD_ROOT/source-patch.sha256" &&
      "$(cat "$BUILD_ROOT/source-patch.sha256")" == "$EXPECTED_ID" ]] &&
    (cd "$BUILD_ROOT" && sha256sum -c --status x-ui.sha256);then
 cp -a "$BUILD_ROOT/x-ui" "$ROLL/x-ui.new"
 echo "Reusing verified native binary built in isolated workspace."
else
 (
  cd "$SRC"
  GOMAXPROCS="${MASIHA_GOMAXPROCS:-2}" timeout --kill-after=10s 1200s \
   go build -mod=readonly -o "$ROLL/x-ui.new" .
 )
fi
[[ -x "$ROLL/x-ui.new" ]] || { echo "Native binary build incomplete"; exit 1; }
[[ "$("$ROLL/x-ui.new" -v 2>&1 | head -1)" == "3.11.0" ]] || { echo "Wrong panel version";exit 1; }

echo "Installing backup backend without changing destination user databases ..."
CONFIGS_SWAPPED=1
install -m 0644 "$ROOT/runtime/full_backup.py" /opt/masiha-vpn-extension/full_backup.py
install -m 0644 "$ROOT/runtime/portable_migration.py" /opt/masiha-vpn-extension/portable_migration.py
install -m 0644 "$ROOT/deploy/web-backup-worker.py" /opt/masiha-vpn-extension/web_backup_worker.py
install -m 0644 "$ROOT/sidecar/server.py" /opt/masiha-vpn-extension/sidecar_server.py
python3 -m py_compile /opt/masiha-vpn-extension/{full_backup,portable_migration,web_backup_worker,sidecar_server}.py
python3 - "$NGINX" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
s=s.replace("client_max_body_size 512m;","client_max_body_size 6g;")
s=s.replace("proxy_request_buffering on;","proxy_request_buffering off;")
p.write_text(s)
PY
nginx -t
systemctl restart masiha-vpn-sidecar.service
systemctl reload nginx.service

echo "Installing rebuilt x-ui (brief restart; no DB reset) ..."
install -m 0755 "$ROLL/x-ui.new" "$LIVE.next"
mv -f "$LIVE.next" "$LIVE"
BINARY_SWAPPED=1
systemctl restart x-ui.service
READY=0
for attempt in $(seq 1 30);do
 if systemctl is-active --quiet x-ui.service &&
    systemctl is-active --quiet masiha-vpn-sidecar.service &&
    curl -sk --max-time 3 -o /dev/null -w '%{http_code}' "https://$DOMAIN/xui/" | grep -qx 200;then
  READY=1
  break
 fi
 sleep 1
done
[[ "$READY" == "1" ]] || { echo "New panel did not respond over HTTPS";exit 1; }
systemctl is-active --quiet x-ui.service
systemctl is-active --quiet nginx.service
systemctl is-active --quiet masiha-vpn-sidecar.service
[[ "$(python3 - "$CONFIG" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["domain"])
PY
)" == "$DOMAIN" ]] || { echo "Domain changed unexpectedly";exit 1; }

# This does not activate Telegram, remote nodes or media upload workers:
# S2 must be quiesced and the final data snapshot transferred before cutover.
trap - ERR
echo "PORTABLE_BACKUP_UPGRADE_COMPLETE"
echo "Destination identity preserved: $DOMAIN"
echo "Rollback: $ROLL"
