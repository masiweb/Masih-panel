#!/usr/bin/env bash
# First-run credential repair for a NEW Masiha test installation.
# Never run against an established deployment containing customer accounts.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo "Please run this command with sudo." >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CREDS=/root/masiha-vpn-initial-login.env
LOCAL_CREDS=/etc/x-ui/install-result.env
DB=/etc/x-ui/x-ui.db
[[ -s "$DB" && -s "$CREDS" && -s "$LOCAL_CREDS" && -x /usr/local/x-ui/x-ui ]] || {
  echo "This is not a provisioned Masiha installation." >&2; exit 1;
}
# Refuse risky credentials resets on populated servers.
python3 - <<'PY'
import sqlite3
with sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True) as c:
 if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]!=1:
  raise SystemExit("Refusing credential repair: expected exactly one initial admin")
 if c.execute("SELECT COUNT(*) FROM client_traffics").fetchone()[0]:
  raise SystemExit("Refusing credential repair: customer accounts already exist")
PY
BACKUP="/root/masiha-admin-recovery-$(date -u +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP"
python3 - "$BACKUP" <<'PY'
import sqlite3,sys,pathlib,shutil
out=pathlib.Path(sys.argv[1])
with sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True) as src:
 with sqlite3.connect(str(out/"x-ui.db")) as dest:src.backup(dest)
for p in ("/root/masiha-vpn-initial-login.env","/etc/x-ui/install-result.env"):
 shutil.copy2(p,out/pathlib.Path(p).name)
PY
# A previous, partially completed automated recovery might already have issued
# a replacement password. Reuse it rather than rotating unexpectedly again.
if [[ -s /root/masiha-pending-password ]]; then
  ADMIN_PASS="$(cat /root/masiha-pending-password)"
else
  ADMIN_PASS="$(openssl rand -hex 24)"
fi
[[ "$ADMIN_PASS" =~ ^[a-f0-9]{48}$ ]] || {
  echo "Unexpected replacement password format. No changes made." >&2; exit 1;
}
ADMIN_USER=admin
echo "=== Reconcile x-ui admin account with root-only saved credentials ==="
systemctl stop masiha-vpn-extension.service || true
systemctl stop x-ui.service
/usr/local/x-ui/x-ui setting -username "$ADMIN_USER" -password "$ADMIN_PASS" >/dev/null
python3 - <<'PY'
import sqlite3
with sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True) as c:
 r=c.execute("SELECT username,password FROM users ORDER BY id LIMIT 1").fetchone()
 if r is None or r[0]!="admin" or not r[1].startswith("$2"):
  raise SystemExit("Native admin update did not persist in SQLite")
print("SQLite admin username and hashed password: PASS")
PY
for dest in "$LOCAL_CREDS" "$CREDS"; do
  tmp="$dest.new"
  {
    printf "XUI_PANEL_PORT='%s'\n" 62053
    printf "XUI_WEB_BASE_PATH='%s'\n" /xui/
    printf "XUI_USERNAME='%s'\n" "$ADMIN_USER"
    printf "XUI_PASSWORD='%s'\n" "$ADMIN_PASS"
  } > "$tmp"
  chmod 0600 "$tmp"
  mv -f "$tmp" "$dest"
done
systemctl restart x-ui.service
for attempt in $(seq 1 20); do
  if curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:62053/xui/;then break;fi
  sleep 1
done
python3 "$ROOT/deploy/check-panel-login.py"
systemctl start masiha-vpn-extension.service
systemctl is-active --quiet x-ui.service
systemctl is-active --quiet masiha-vpn-extension.service
# Remove staging secret only after BOTH user and internal API are in sync.
rm -f /root/masiha-pending-password
echo "=== Masiha first-run admin login repaired and tested ==="
echo "Panel URL : https://$(python3 -c 'import json;print(json.load(open("/etc/masiha-vpn-extension/config.json"))["domain"])')/xui/"
echo "Recovery credentials: sudo cat $CREDS"
echo "Backup directory: $BACKUP"
# Only reveal a password in a directly-attached interactive SSH terminal,
# not when automation tools capture logs or in install artifact files.
if [[ -t 1 ]]; then
  echo "Username: $ADMIN_USER"
  echo "Password: $ADMIN_PASS"
fi
