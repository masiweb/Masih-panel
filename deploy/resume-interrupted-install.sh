#!/usr/bin/env bash
# Recover an interrupted FRESH install without rerunning upstream install.sh.
set -Eeuo pipefail
trap 'rc=$?; echo "RECOVERY_FAILED at line ${LINENO} exit=$rc; saved DB/SSL/CA preserved." >&2' ERR
umask 077
[[ $EUID == 0 ]] || { echo "Run as root" >&2;exit 1; }
[[ "${1:-}" == "" || "${1:-}" == "--check" ]] || { echo "Usage: $0 [--check]" >&2;exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MARKER=/etc/masiha-vpn-extension/.finalize-in-progress
CREDS=/root/masiha-vpn-initial-login.env
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-v3.11.0}"
[[ -s /etc/masiha-vpn-extension/config.json ]] || { echo "Missing Masiha config" >&2;exit 1; }
DOMAIN="$(python3 - <<'PY'
import json
print(json.load(open('/etc/masiha-vpn-extension/config.json'))['domain'])
PY
)"
[[ "$DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]] || exit 1
echo "DOMAIN=$DOMAIN"
for p in /usr/local/x-ui/x-ui /etc/x-ui/x-ui.db "$CREDS" \
 "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" \
 /etc/nginx/sites-available/masiha-vpn "$MARKER" \
 /etc/systemd/system/masiha-vpn-extension.service; do
 if [[ -s "$p" ]]; then echo "PRESENT $p"; else echo "MISSING $p"; fi
done
[[ "${1:-}" != "--check" ]] || exit 0
if [[ -e /etc/systemd/system/masiha-vpn-extension.service ]]; then
 if [[ -f "$MARKER" ]]; then
  exec bash "$ROOT/deploy/finalize-ubuntu-24.04.sh" --resume
 fi
 [[ -s "$CREDS" && -s /etc/nginx/sites-available/masiha-vpn ]] || exit 1
 if systemctl is-active --quiet masiha-vpn-sidecar.service; then
  echo INSTALL_ALREADY_COMPLETE
  exit 0
 fi
 exec bash "$ROOT/deploy/finish-install.sh" --resume
fi
[[ ! -e "$MARKER" ]] || { echo "Unexpected interrupted marker: inspect manually" >&2; exit 1; }
for f in "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui /etc/x-ui/x-ui.db /etc/x-ui/install-result.env; do
 [[ -s "$f" ]] || { echo "Missing prerequisite $f" >&2;exit 1; }
done
cmp -s "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui ||
 { echo "Binary mismatch, cannot safely resume" >&2;exit 1; }
python3 - <<'PY'
import sqlite3
with sqlite3.connect('file:/etc/x-ui/x-ui.db?mode=ro',uri=True) as db:
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='client_traffics'").fetchone():
  assert db.execute("SELECT COUNT(*) FROM client_traffics").fetchone()[0]==0, "Live clients: recovery aborted"
PY
if [[ ! -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
 install -d -m 0755 /var/www/html/.well-known/acme-challenge
 if [[ -n "${EMAIL:-}" ]]; then
  certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos \
   --email "$EMAIL" -d "$DOMAIN" --keep-until-expiring
 else
  echo "No EMAIL supplied: register without email; add renewal contact later."
  certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos \
   --register-unsafely-without-email -d "$DOMAIN" --keep-until-expiring
 fi
fi
[[ -s "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]] || exit 1
if [[ -s "$CREDS" && -s /etc/nginx/sites-available/masiha-vpn ]]; then
 echo "Preserving existing administrator credentials and Nginx site."
elif [[ ! -e "$CREDS" && ! -e /etc/nginx/sites-available/masiha-vpn ]]; then
 PANEL_PATH=/xui/
 ADMIN_USER="${PANEL_ADMIN_USER:-admin}"
 STAGE="$(mktemp /root/.masiha-partial-stage3.XXXXXX)"
 trap 'rm -f "$STAGE"' EXIT
 python3 - "$ROOT/deploy/install-ubuntu-24.04.sh" > "$STAGE" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
a='# Isolate native panel on loopback behind HTTPS Nginx.'
b='# The final step is shared with the safe recovery path.'
assert s.count(a)==s.count(b)==1
print('set -Eeuo pipefail')
print(s[s.index(a):s.index(b)])
PY
 export DOMAIN PANEL_PATH ADMIN_USER
 bash "$STAGE"
 rm -f "$STAGE"
else
 echo "Conflicting partial credentials/site; refuse to overwrite. Manual recovery needed." >&2
 exit 1
fi
exec bash "$ROOT/deploy/finalize-ubuntu-24.04.sh" --resume
