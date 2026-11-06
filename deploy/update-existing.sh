#!/usr/bin/env bash
# One-command, no-data-restore, safe code updater for an installed Masiha panel.
# It never imports backups, creates DBs, overwrites /etc/x-ui or starts an
# inactive Telegram worker. Builds new code before touching live services.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-update}"
LIVE=/usr/local/x-ui/x-ui
EXT=/opt/masiha-vpn-extension
NGINX=/etc/nginx/sites-available/masiha-vpn
WEB=/var/www/masiha-ui
PIN='c621ba9014a6aa21fd7c9ecf45655e44e24ebf13'
TOOLS=/opt/masiha-vpn-toolchains
PATCH_HASH="$(sha256sum "$ROOT/deploy/3x-ui.patch" | awk '{print $1}')"
BR="${MASIHA_UPDATE_BUILD_ROOT:-/opt/masiha-release-builds/${PATCH_HASH:0:16}}"
[[ "$EUID" == 0 ]] || { echo 'Please run as root.' >&2;exit 2; }
[[ "$MODE" == update || "$MODE" == --check ]] || { echo 'Usage: update-existing.sh [--check]' >&2; exit 2; }
[[ -x "$LIVE" ]] || { echo '3x-ui is not installed. Use the INSTALL command on a fresh server.' >&2;exit 3; }
[[ -s "$ROOT/deploy/3x-ui.patch" && -s "$ROOT/runtime/full_backup.py" &&
   -s "$ROOT/sidecar/server.py" && -s "$ROOT/deploy/prepare-portable-build.sh" ]] || {
 echo 'Incomplete Git release checkout.' >&2;exit 1;
}
[[ -s "$NGINX" ]] || { echo 'Nginx Masiha site is missing; do not replace a partial installation.' >&2;exit 1; }
[[ -d "$EXT" ]] || { echo 'Masiha extension installation directory is missing.' >&2;exit 1; }
[[ -d "$WEB" ]] || { echo 'Masiha web extension directory is missing.' >&2;exit 1; }
command -v flock >/dev/null || { echo 'flock is required.' >&2;exit 1; }
install -d -m 0755 /run/lock
exec 9>/run/lock/masiha-update.lock
flock -n 9 || { echo 'Another Masiha update is already running.' >&2;exit 1; }

VERSION="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "UPDATE_RELEASE=$VERSION"
echo 'UPDATE_BACKUP_REQUIRED=NO'
echo 'UPDATE_RESTORES_DATABASES=NO'
for dbfile in /etc/x-ui/x-ui.db /etc/masiha-vpn-extension/users.db /etc/masiha-vpn-extension/sales/sales.db;do
  [[ -s "$dbfile" ]] || echo "DATA_NOT_IMPORTED_YET=$dbfile (update can continue)"
done
echo "CURRENT_XUI=$("$LIVE" -v 2>&1 | head -1)"
echo "NATIVE_BUILD_CACHE=$BR"
if [[ "$MODE" == --check ]];then
 echo 'MASIHA_UPDATE_CHECK_OK'
 exit 0
fi

[[ "$(uname -m)" == x86_64 ]] || { echo 'Update build supports Linux x86_64 only.' >&2;exit 1; }
if [[ ! -s "$BR/x-ui" || ! -s "$BR/x-ui.sha256" ||
      ! -s "$BR/source-patch.sha256" ||
      "$(cat "$BR/source-patch.sha256" 2>/dev/null || true)" != "$PIN $PATCH_HASH" ]] ||
   ! (cd "$BR" 2>/dev/null && sha256sum -c --status x-ui.sha256);then
  AVAILABLE_KB="$(df -Pk /opt | awk 'NR==2{print $4}')"
  [[ "$AVAILABLE_KB" =~ ^[0-9]+$ && "$AVAILABLE_KB" -ge 3145728 ]] || {
   echo 'Not enough free space for source compilation (need at least 3 GiB).' >&2
   echo 'No live files have been changed.' >&2;exit 1;
  }
  echo '=== Building and validating the latest full Masiha panel in isolated workspace ==='
  MASIHA_BUILD_ROOT="$BR" bash "$ROOT/deploy/prepare-portable-build.sh"
else
  echo 'Verified native build cache reused.'
fi
[[ "$("$BR/x-ui" -v 2>&1 | head -1)" == 3.11.0 ]] || {
 echo 'Compiled panel version mismatch; live files unchanged.' >&2; exit 1;
}
export PATH="$TOOLS/go1.27.3/bin:$TOOLS/node-v26.13.0-linux-x64/bin:$PATH"
SRC="$BR/src/frontend"
for f in "$ROOT/sidecar/masiha-vite.config.mjs" "$ROOT/sidecar/masiha-standalone.html" \
         "$ROOT/sidecar/masiha-standalone.tsx" "$ROOT/sidecar/standalone.css" \
         "$ROOT/sidecar/nav.js" "$ROOT/sidecar/nav.css";do
  [[ -s "$f" ]] || { echo "Missing frontend file $f";exit 1; }
done
[[ -x "$SRC/node_modules/.bin/vite" ]] || { echo 'Frontend build cache missing.' >&2;exit 1; }
STMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLL="/root/masiha-updates/rollback-$STMP"
install -d -m 0700 "$ROLL"
install -d -m 0700 "$ROLL/web-next"
echo '=== Building standalone web UI into a private temporary folder ==='
# Reapply the same source overlays as the fresh installer (users, AI,
# media, social, IPSec), without touching live nginx/systemd/webroot.
bash "$ROOT/deploy/prepare-sidecar-source.sh" "$SRC"
install -m 0644 "$ROOT/sidecar/masiha-vite.config.mjs" "$SRC/masiha-vite.config.mjs"
install -m 0644 "$ROOT/sidecar/masiha-standalone.html" "$SRC/masiha-standalone.html"
install -m 0644 "$ROOT/sidecar/masiha-standalone.tsx" "$SRC/src/masiha-standalone.tsx"
install -m 0644 "$ROOT/sidecar/standalone.css" "$SRC/src/masiha-standalone.css"
(
 cd "$SRC"
 ./node_modules/.bin/vite build --config masiha-vite.config.mjs --outDir "$ROLL/web-next"
)
install -m 0644 "$ROOT/sidecar/nav.js" "$ROLL/web-next/nav.js"
install -m 0644 "$ROOT/sidecar/nav.css" "$ROLL/web-next/nav.css"
[[ -s "$ROLL/web-next/masiha-standalone.html" ]] || {
 echo 'Standalone frontend output missing; no live files changed.' >&2;exit 1;
}
chmod -R a+rX "$ROLL/web-next"

echo '=== Preparing application rollback before swapping anything ==='
cp -a "$LIVE" "$ROLL/x-ui.before"
cp -a "$NGINX" "$ROLL/nginx.before"
mkdir -m 0700 "$ROLL/code.before"
: > "$ROLL/code.new"
for source in "$ROOT"/runtime/*.py "$ROOT"/runtime/*.json \
              "$ROOT/deploy/web-backup-worker.py" "$ROOT/sidecar/server.py";do
 [[ -f "$source" ]] || continue
 case "$source" in
   */web-backup-worker.py) dest="$EXT/web_backup_worker.py" ;;
   */sidecar/server.py) dest="$EXT/sidecar_server.py" ;;
   *) dest="$EXT/$(basename "$source")" ;;
 esac
 if [[ -e "$dest" ]];then cp -a "$dest" "$ROLL/code.before/$(basename "$dest")";else echo "$dest" >> "$ROLL/code.new";fi
done
WEB_SWAPPED=0
BINARY_SWAPPED=0
CODE_SWAPPED=0
NGINX_SWAPPED=0
XUI_WAS_ACTIVE=0
SIDE_WAS_ACTIVE=0
BOT_WAS_ACTIVE=0
systemctl is-active --quiet x-ui.service && XUI_WAS_ACTIVE=1 || true
systemctl is-active --quiet masiha-vpn-sidecar.service && SIDE_WAS_ACTIVE=1 || true
systemctl is-active --quiet masiha-vpn-extension.service && BOT_WAS_ACTIVE=1 || true
rollback() {
  local rc=$?
  trap - ERR INT TERM
  echo "UPDATE_FAILED=$rc; restoring executable files and previous web assets." >&2
  if [[ "$BINARY_SWAPPED" == 1 ]];then
    cp -a "$ROLL/x-ui.before" "$LIVE.rollback"
    chmod 0755 "$LIVE.rollback"
    mv -f "$LIVE.rollback" "$LIVE"
  fi
  if [[ "$WEB_SWAPPED" == 1 && -d "$ROLL/web.before" ]];then
    rm -rf "$WEB"
    mv "$ROLL/web.before" "$WEB"
  fi
  if [[ "$CODE_SWAPPED" == 1 ]];then
    cp -a "$ROLL/code.before/." "$EXT/" || true
    while IFS= read -r created;do [[ -z "$created" ]] || rm -f -- "$created";done < "$ROLL/code.new"
  fi
  if [[ "$NGINX_SWAPPED" == 1 ]];then
    cp -a "$ROLL/nginx.before" "$NGINX"
  fi
  if [[ "$XUI_WAS_ACTIVE" == 1 ]];then systemctl restart x-ui.service || true;fi
  if [[ "$SIDE_WAS_ACTIVE" == 1 ]];then systemctl restart masiha-vpn-sidecar.service || true;fi
  if [[ "$BOT_WAS_ACTIVE" == 1 ]];then systemctl restart masiha-vpn-extension.service || true;fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx.service || true
  echo "ROLLBACK_PATH=$ROLL" >&2
  exit "$rc"
}
trap rollback ERR INT TERM
echo '=== Installing program code and web assets; no DB, SSL or bot token modifications ==='
CODE_SWAPPED=1
for source in "$ROOT"/runtime/*.py "$ROOT"/runtime/*.json;do
 [[ -f "$source" ]] || continue
 install -m 0644 "$source" "$EXT/$(basename "$source")"
done
install -m 0644 "$ROOT/deploy/web-backup-worker.py" "$EXT/web_backup_worker.py"
install -m 0644 "$ROOT/sidecar/server.py" "$EXT/sidecar_server.py"
python3 -m py_compile "$EXT/full_backup.py" "$EXT/portable_migration.py" \
                       "$EXT/web_backup_worker.py" "$EXT/sidecar_server.py"
# Change only the upload limit for authenticated backup endpoints.
NGINX_SWAPPED=1
python3 - "$NGINX" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
s=s.replace("client_max_body_size 512m;","client_max_body_size 6g;")
s=s.replace("proxy_request_buffering on;","proxy_request_buffering off;")
p.write_text(s)
PY
nginx -t
# Replace UI with a staged tree; retain a full rollback copy.
WEB_SWAPPED=1
mv "$WEB" "$ROLL/web.before"
mv "$ROLL/web-next" "$WEB"
install -m 0755 "$BR/x-ui" "$LIVE.next"
mv -f "$LIVE.next" "$LIVE"
BINARY_SWAPPED=1
[[ "$XUI_WAS_ACTIVE" == 1 ]] && systemctl restart x-ui.service || true
[[ "$SIDE_WAS_ACTIVE" == 1 ]] && systemctl restart masiha-vpn-sidecar.service || true
systemctl reload nginx.service
[[ "$BOT_WAS_ACTIVE" == 1 ]] && systemctl restart masiha-vpn-extension.service || true
# Never enable an inactive bot on S3: S2 might still be processing updates.
[[ "$XUI_WAS_ACTIVE" != 1 ]] || systemctl is-active --quiet x-ui.service
[[ "$SIDE_WAS_ACTIVE" != 1 ]] || systemctl is-active --quiet masiha-vpn-sidecar.service
[[ "$BOT_WAS_ACTIVE" != 1 ]] || systemctl is-active --quiet masiha-vpn-extension.service
nginx -t
# Validate local TLS Nginx without trusting Cloudflare DNS/proxy routing.
DOMAIN="$(python3 - <<'PY'
import json
from pathlib import Path
p=Path('/etc/masiha-vpn-extension/config.json')
print(json.loads(p.read_text()).get('domain','') if p.is_file() else '')
PY
)"
if [[ "$XUI_WAS_ACTIVE" == 1 && -n "$DOMAIN" ]];then
   ready=0
   for i in $(seq 1 25);do
     status="$(curl -sk --connect-timeout 3 --max-time 4 \
       --resolve "$DOMAIN:443:127.0.0.1" -o /dev/null -w '%{http_code}' \
       "https://$DOMAIN/xui/" || true)"
     if [[ "$status" == 200 || "$status" == 302 ]];then ready=1;break;fi
     sleep 1
   done
   [[ "$ready" == 1 ]] || {
     echo 'Panel HTTP health test failed after upgrade.' >&2;false;
   }
fi
trap - ERR INT TERM
echo "MASIHA_UPDATE_OK=$VERSION"
echo "BACKUP_RESTORE_NOT_STARTED=YES"
echo "MIGRATION_DATABASES_NOT_TOUCHED=YES"
echo "ROLLBACK_PATH=$ROLL"
