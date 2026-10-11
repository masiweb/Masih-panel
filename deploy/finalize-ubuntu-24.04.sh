#!/usr/bin/env bash
# Complete Masiha VPN after native x-ui, nginx and SSL are installed.
# Can safely finish the partial install caused by Easy-RSA CN conflicts.
# Deliberately NOT a general repair/re-install tool for live deployments.
set -Eeuo pipefail
umask 077
[[ "$EUID" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ "${1:-}" == "--resume" || "${1:-}" == "--from-installer" ]] ||
  { echo "Usage: sudo bash deploy/finalize-ubuntu-24.04.sh --resume" >&2; exit 2; }
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-20261011}"
# No replacing existing production settings/services in this completion step.
if [[ -e /etc/systemd/system/masiha-vpn-extension.service ]]; then
  echo "Masiha extension already installed. Refusing to re-finalize a working/partial deployment." >&2
  exit 1
fi
for required in "$BUILD_ROOT/x-ui" \
  "$BUILD_ROOT/src/extensions/masiha-vpn/openvpn.conf" \
  "$BUILD_ROOT/src/extensions/masiha-vpn/ocserv.conf" \
  /usr/local/x-ui/x-ui /etc/x-ui/x-ui.db \
  /etc/x-ui/install-result.env /root/masiha-vpn-initial-login.env \
  /etc/masiha-vpn-extension/config.json /etc/nginx/sites-available/masiha-vpn; do
  [[ -s "$required" ]] || {
    echo "Prerequisite missing or empty: $required. Refusing unsafe resume." >&2
    exit 1
  }
done
# Ensure this is the same Masiha binary, not a preexisting unrelated x-ui.
cmp -s "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui || {
  echo "Native x-ui binary differs from Masiha build; unsafe to resume." >&2
  exit 1
}
# Sensitive admin settings were created before the interrupted PKI step.
# Restrict reading to root files and avoid displaying any secret until success.
[[ "$(stat -c '%u:%a' /root/masiha-vpn-initial-login.env)" == "0:600" ]] ||
  { echo "Unexpected credentials owner/mode; aborting." >&2; exit 1; }
# shellcheck disable=SC1091
source /root/masiha-vpn-initial-login.env
ADMIN_USER="${XUI_USERNAME:?Missing initial admin username}"
ADMIN_PASS="${XUI_PASSWORD:?Missing initial admin password}"
PANEL_PATH="${XUI_WEB_BASE_PATH:?Missing panel path}"
DOMAIN="$(python3 - <<'PY'
import json
p='/etc/masiha-vpn-extension/config.json'
print(json.load(open(p))['domain'])
PY
)"
[[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && "$DOMAIN" == *.* ]] ||
  { echo "Bad domain in saved config." >&2; exit 1; }
[[ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" &&
   -s "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]] ||
  { echo "Missing expected TLS certificate for $DOMAIN." >&2; exit 1; }
grep -Fq "server_name $DOMAIN;" /etc/nginx/sites-available/masiha-vpn ||
  { echo "Nginx domain mismatch; refusing to resume." >&2; exit 1; }
# Do not expose a previously printed official-install API token; rotate it.
# A fresh token is emitted only to /dev/null; existing sessions are independent.
if [[ ! -e /etc/x-ui/.masiha-initial-token-rotated ]]; then
  /usr/local/x-ui/x-ui setting -getApiToken -tokenName install >/dev/null
  install -m 0600 /dev/null /etc/x-ui/.masiha-initial-token-rotated
  echo "Upstream install API token rotated."
fi
echo "Resuming verified installation for $DOMAIN; existing CA/database/SSL retained."
# Per-server CA & TLS-crypt; do not reuse keys from production unless migrating.
install -d -m 0700 /etc/openvpn/masiha
export EASYRSA_PKI=/etc/openvpn/masiha/pki EASYRSA_BATCH=1 EASYRSA_ALGO=ec \
  EASYRSA_CURVE=prime256v1
export EASYRSA_CERT_EXPIRE=3650 EASYRSA_CRL_DAYS=3650
# EASYRSA_REQ_CN is allowed for build-ca, but FORBIDDEN by build-server-full.
unset EASYRSA_REQ_CN
EASY=/usr/share/easy-rsa/easyrsa
[[ -x "$EASY" ]] || { echo "easy-rsa missing: $EASY" >&2; exit 1; }
if [[ -s "$EASYRSA_PKI/ca.crt" ]]; then
  [[ -s "$EASYRSA_PKI/private/ca.key" ]] ||
    { echo "Existing CA certificate has no private CA key; refusing to replace it." >&2; exit 1; }
  echo "Keeping the existing OpenVPN CA and private key."
else
  [[ ! -e "$EASYRSA_PKI/private/ca.key" ]] ||
    { echo "Partial CA private key exists without CA certificate; manual recovery required." >&2; exit 1; }
  [[ ! -e "$EASYRSA_PKI" ]] ||
    { echo "Existing incomplete PKI directory; refusing to reset it automatically." >&2; exit 1; }
  "$EASY" init-pki
  EASYRSA_REQ_CN='Masiha VPN CA' "$EASY" build-ca nopass
fi
if [[ -s "$EASYRSA_PKI/issued/masiha-openvpn.crt" &&
      -s "$EASYRSA_PKI/private/masiha-openvpn.key" ]]; then
  echo "Keeping existing OpenVPN server certificate and key."
else
  [[ ! -e "$EASYRSA_PKI/issued/masiha-openvpn.crt" &&
     ! -e "$EASYRSA_PKI/private/masiha-openvpn.key" ]] ||
    { echo "Partial OpenVPN server key/certificate; manual recovery required." >&2; exit 1; }
  "$EASY" build-server-full masiha-openvpn nopass
fi
if [[ ! -s "$EASYRSA_PKI/crl.pem" ]]; then
  "$EASY" gen-crl
fi
chmod 0644 /etc/openvpn/masiha/pki/crl.pem
if [[ ! -s /etc/openvpn/masiha/tls-crypt.key ]]; then
  openvpn --genkey tls-crypt /etc/openvpn/masiha/tls-crypt.key
fi
install -d -m 0755 /etc/openvpn/server
cp "$BUILD_ROOT/src/extensions/masiha-vpn/openvpn.conf" /etc/openvpn/server/masiha.conf
cat >> /etc/openvpn/server/masiha.conf <<'EOF'
# Shared 3x-ui subscription accounting
script-security 2
client-connect /opt/masiha-vpn-extension/vpn-account-hook.sh
client-disconnect /opt/masiha-vpn-extension/vpn-account-hook.sh
EOF
# OpenConnect credentials are created on demand by the extension.
install -d -m 0700 /etc/ocserv
if [[ ! -e /etc/ocserv/masiha.passwd ]]; then
  install -m 0600 /dev/null /etc/ocserv/masiha.passwd
fi
sed "s/xu\.chanelchat\.ir/$DOMAIN/g" "$BUILD_ROOT/src/extensions/masiha-vpn/ocserv.conf" > /etc/ocserv/ocserv.conf
cat >> /etc/ocserv/ocserv.conf <<'EOF'
# Shared 3x-ui subscription accounting
connect-script = /opt/masiha-vpn-extension/vpn-account-hook.sh
disconnect-script = /opt/masiha-vpn-extension/vpn-account-hook.sh
EOF
ocserv -t -c /etc/ocserv/ocserv.conf
cat > /etc/systemd/system/masiha-vpn-extension.service <<'EOF'
[Unit]
Description=Masiha VPN shared traffic, expiration and Telegram commerce
Wants=network-online.target x-ui.service
After=network-online.target x-ui.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/masiha-vpn-extension/worker.py
Restart=on-failure
RestartSec=5
UMask=0077
PrivateTmp=true
ProtectHome=true
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/sysctl.d/98-masiha-vpn-forward.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl --system >/dev/null
# Pre-allow management before enabling any firewall; do not replace custom policies.
ufw allow OpenSSH >/dev/null || true
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 1194/udp >/dev/null
ufw allow 4443/tcp >/dev/null
ufw allow 4443/udp >/dev/null
for xray_port in 8443 8444 8445 8446 8447 8448; do
  ufw allow "$xray_port/tcp" >/dev/null
done
systemctl daemon-reload
# This finalizer only handles fresh/previously interrupted installs. Stop x-ui
# briefly so SQLite is offline while the default Xray inbounds are initialized.
# If any inbound is already present, the initializer leaves all inbounds alone.
systemctl stop x-ui.service
# The upstream installer chooses a random subscription path that does NOT
# match our Nginx /sub/ proxy. Configure the complete HTTPS subscription URL
# before opening the Xray services, and listen only on loopback.
python3 - "$DOMAIN" <<'PY'
import sqlite3,sys
domain=sys.argv[1]
db=sqlite3.connect('/etc/x-ui/x-ui.db',timeout=30)
try:
 db.execute('BEGIN IMMEDIATE')
 # Fresh-install protection; existing customers must never lose old URLs.
 ct=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='client_traffics'").fetchone()
 if ct and db.execute("SELECT count(*) FROM client_traffics").fetchone()[0]:
  raise RuntimeError('Existing clients detected; refusing to change subscription URI')
 values={
  'subEnable':'true','subListen':'127.0.0.1','subPort':'2096',
  'subPath':'/sub/','subDomain':domain,'subURI':'https://'+domain+'/sub/',
 }
 for key,value in values.items():
  if db.execute('SELECT 1 FROM settings WHERE key=?',(key,)).fetchone():
   db.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
  else:
   db.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,value))
 db.commit()
 print('HTTPS subscription configured: https://'+domain+'/sub/')
except Exception:
 db.rollback()
 raise
finally:
 db.close()
PY
python3 "$REPO_ROOT/deploy/bootstrap-inbounds.py" \
  --db /etc/x-ui/x-ui.db --domain "$DOMAIN" \
  --xray /usr/local/x-ui/bin/xray-linux-amd64
# Starting here also loads the patched Masiha binary instead of the
# original upstream installer process.
systemctl enable --now x-ui.service
# Native upstream installer had already started x-ui before its binary was replaced.
systemctl enable --now openvpn-server@masiha.service
systemctl enable --now ocserv.service
systemctl enable --now masiha-vpn-extension.service
MASIHA_EXPECT_DEFAULT_INBOUNDS=1 bash "$REPO_ROOT/deploy/verify-install.sh"
# Print credentials immediately on the terminal, ONLY after health checks pass.
# Keep a 0600 copy for recovery; do not print during partial/failed installs.
printf '\n===============================================\n'
printf ' MASIHA VPN INSTALLED SUCCESSFULLY\n'
printf '===============================================\n'
printf ' Panel URL : https://%s%s\n' "$DOMAIN" "$PANEL_PATH"
printf ' Username  : %s\n' "$ADMIN_USER"
printf ' Password  : %s\n' "$ADMIN_PASS"
printf ' HTTPS SSL : Let\047s Encrypt (%s)\n' "$DOMAIN"
printf ' Recovery  : /root/masiha-vpn-initial-login.env (root only)\n'
printf '===============================================\n'
printf ' Subscription : https://%s/sub/ (configured automatically)\n' "$DOMAIN"
printf ' Xray Presets : 6 TCP inbounds (8443-8448), firewall rules created\n'
printf ' Next         : Configure Telegram BotFather token/admins/plans in the sales UI.\n'

