#!/usr/bin/env bash
# Masiha VPN fresh-host installer. Ubuntu 24.04 x86_64 ONLY.
# This script refuses an existing production installation.
set -Eeuo pipefail
umask 077
if [[ "${1:-}" == "--help" ]]; then
  echo 'Usage: DOMAIN=vpn.example.com EMAIL=admin@example.com sudo -E bash deploy/install-ubuntu-24.04.sh'
  echo 'Optional: PANEL_PATH=/xui/ PANEL_ADMIN_USER=admin MASIHA_BUILD_ROOT=/opt/masiha-vpn-build-20261011'
  exit 0
fi
[[ "$EUID" -eq 0 ]] || { echo "Run as root with sudo -E" >&2; exit 1; }
[[ -r /etc/os-release ]] || exit 1
source /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" && "$(uname -m)" == "x86_64" ]] ||
  { echo "Only fresh Ubuntu 24.04 amd64 is supported" >&2; exit 1; }
DOMAIN="${DOMAIN:?Set DOMAIN to the public DNS name of this server}"
EMAIL="${EMAIL:?Set EMAIL for the TLS certificate}"
PANEL_PATH="${PANEL_PATH:-/xui/}"
ADMIN_USER="${PANEL_ADMIN_USER:-admin}"
[[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && "$DOMAIN" == *.* ]] ||
  { echo "Invalid DOMAIN" >&2; exit 1; }
[[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
  { echo "Invalid EMAIL" >&2; exit 1; }
[[ "$PANEL_PATH" =~ ^/[A-Za-z0-9_/-]+/$ ]] ||
  { echo "Invalid PANEL_PATH" >&2; exit 1; }
[[ "$ADMIN_USER" =~ ^[a-zA-Z0-9_-]{3,30}$ ]] ||
  { echo "Invalid PANEL_ADMIN_USER" >&2; exit 1; }
for path in /etc/x-ui/x-ui.db /etc/masiha-vpn-extension/users.db /usr/local/x-ui/x-ui; do
  [[ ! -e "$path" ]] || { echo "Existing installation detected at $path; do NOT run this fresh-host installer. See migration guide." >&2; exit 1; }
done
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$REPO_ROOT/deploy/3x-ui.patch" && -f "$REPO_ROOT/runtime/unified.py" ]] ||
  { echo "Incomplete Masiha release checkout" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git tar xz-utils build-essential pkg-config python3 \
  sqlite3 jq nginx certbot openvpn openconnect ocserv easy-rsa iptables ufw openssl
# Distribution ocserv may start on TCP/443 before Masiha config is installed.
# Release that listener so Nginx can terminate the public HTTPS endpoint.
systemctl stop ocserv.service 2>/dev/null || true
systemctl disable ocserv.service 2>/dev/null || true
# Install exact tool versions needed by the pinned fork; never use system Node 18.
TOOLS=/opt/masiha-vpn-toolchains
mkdir -p "$TOOLS"
if [[ ! -x "$TOOLS/go/bin/go" ]]; then
  curl --fail --location --retry 3 --output "$TOOLS/go.tar.gz" https://go.dev/dl/go1.27.1.linux-amd64.tar.gz
  tar -xzf "$TOOLS/go.tar.gz" -C "$TOOLS"
fi
if [[ ! -x "$TOOLS/node-v24.21.0-linux-x64/bin/node" ]]; then
  curl --fail --location --retry 3 --output "$TOOLS/node.tar.xz" https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz
  tar -xJf "$TOOLS/node.tar.xz" -C "$TOOLS"
fi
export PATH="$TOOLS/go/bin:$TOOLS/node-v24.21.0-linux-x64/bin:$PATH"
[[ "$(go version)" == *"go1.27.1"* ]] || { echo "Go version mismatch" >&2; exit 1; }
[[ "$(node -v)" == "v24.21.0" ]] || { echo "Node version mismatch" >&2; exit 1; }
bash "$REPO_ROOT/deploy/build-3x-ui.sh"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-20261011}"
[[ -s "$BUILD_ROOT/x-ui" ]] || exit 1
# Official pinned installer supplies Xray, geo data and native systemd helpers.
# The official downloaded 3x-ui binary is IMMEDIATELY replaced by the verified Masiha build.
XUI_NONINTERACTIVE=1 bash "$BUILD_ROOT/src/install.sh"
[[ -d /usr/local/x-ui/bin && -f /etc/systemd/system/x-ui.service ]] ||
  { echo "3x-ui dependencies not installed; stop without starting Masiha extension" >&2; exit 1; }
systemctl stop x-ui.service 2>/dev/null || true
install -m 0755 "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui
install -d -m 0700 /etc/x-ui /etc/masiha-vpn-extension /opt/masiha-vpn-extension
for file in manager.py unified.py worker.py sales.py sales_worker.py runtime_config.py; do
  install -m 0644 "$REPO_ROOT/runtime/$file" "/opt/masiha-vpn-extension/$file"
done
for file in test_unified.py test_sales.py smoke_panel.py test_tunnels.py test-oc-route.sh; do
  install -m 0644 "$REPO_ROOT/runtime/$file" "/opt/masiha-vpn-extension/$file"
done
install -m 0755 "$REPO_ROOT/runtime/vpn-account-hook.sh" /opt/masiha-vpn-extension/vpn-account-hook.sh
# Root-only runtime configuration; account DBs, bot tokens and passwords stay OFF Git.
python3 - "$DOMAIN" "$PANEL_PATH" <<'PY'
import json,sys,pathlib
p=pathlib.Path('/etc/masiha-vpn-extension/config.json')
p.write_text(json.dumps({'domain':sys.argv[1],'panel_path':sys.argv[2]},indent=2)+'\n')
p.chmod(0o600)
PY
# ACME HTTP-01 needs public TCP/80 and the DNS A record to target this host.
systemctl enable --now nginx
certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos \
  --email "$EMAIL" -d "$DOMAIN" --keep-until-expiring
[[ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] || exit 1
# Isolate native panel on loopback behind HTTPS Nginx.
ADMIN_PASS="$(openssl rand -hex 24)"
/usr/local/x-ui/x-ui setting -port 62053 -listenIP 127.0.0.1 -webBasePath "$PANEL_PATH" \
  -username "$ADMIN_USER" -password "$ADMIN_PASS"
# Native commerce helper reads login from this local root-only file.
install -d -m 0700 /etc/x-ui
{
  printf "XUI_PANEL_PORT='%s'\n" 62053
  printf "XUI_WEB_BASE_PATH='%s'\n" "$PANEL_PATH"
  printf "XUI_USERNAME='%s'\n" "$ADMIN_USER"
  printf "XUI_PASSWORD='%s'\n" "$ADMIN_PASS"
} > /etc/x-ui/install-result.env
chmod 0600 /etc/x-ui/install-result.env
# Keep a recoverable copy of first-run admin credentials.
install -m 0600 /etc/x-ui/install-result.env /root/masiha-vpn-initial-login.env
# Reverse proxy both native panel and the subscription service.
cat > /etc/nginx/sites-available/masiha-vpn <<EOF
server {
  listen 80;
  server_name $DOMAIN;
  location / { return 301 https://\$host\$request_uri; }
}
server {
  listen 443 ssl http2;
  server_name $DOMAIN;
  ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
  client_max_body_size 16m;
  location /sub/ {
    proxy_pass http://127.0.0.1:2096;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP \$remote_addr;
  }
  location / {
    proxy_pass http://127.0.0.1:62053;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
  }
}
EOF
ln -sfn /etc/nginx/sites-available/masiha-vpn /etc/nginx/sites-enabled/masiha-vpn
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
# Per-server CA & TLS-crypt; do not reuse keys from production unless migrating.
install -d -m 0700 /etc/openvpn/masiha
export EASYRSA_PKI=/etc/openvpn/masiha/pki EASYRSA_BATCH=1 EASYRSA_ALGO=ec \
  EASYRSA_CURVE=prime256v1 EASYRSA_REQ_CN='Masiha VPN CA'
export EASYRSA_CERT_EXPIRE=3650 EASYRSA_CRL_DAYS=3650
EASY=/usr/share/easy-rsa/easyrsa
"$EASY" init-pki
"$EASY" build-ca nopass
"$EASY" build-server-full masiha-openvpn nopass
"$EASY" gen-crl
chmod 0644 /etc/openvpn/masiha/pki/crl.pem
openvpn --genkey tls-crypt /etc/openvpn/masiha/tls-crypt.key
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
install -m 0600 /dev/null /etc/ocserv/masiha.passwd
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
systemctl daemon-reload
systemctl enable --now x-ui.service
systemctl enable --now openvpn-server@masiha.service
systemctl enable --now ocserv.service
systemctl enable --now masiha-vpn-extension.service
bash "$REPO_ROOT/deploy/verify-install.sh"
cat <<EOF
Masiha installation complete.
Panel: https://$DOMAIN$PANEL_PATH
Root-only credentials: /root/masiha-vpn-initial-login.env
Set 3x-ui subscription URI to https://$DOMAIN/sub and bind subscription to 127.0.0.1:2096.
Create Xray inbounds in the panel and allow ONLY their intended ports in your firewall.
Configure Telegram bot token and admins in the sales-bot UI; no bot token is stored in Git.
EOF
