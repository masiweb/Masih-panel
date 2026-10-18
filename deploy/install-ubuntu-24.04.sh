#!/usr/bin/env bash
# Masiha VPN fresh-host installer. Ubuntu 24.04 x86_64 ONLY.
# This script refuses an existing production installation.
set -Eeuo pipefail
# Do not include credentials in error diagnostics.
trap 'rc=$?; echo "ERROR: Fresh-server installation stopped at line ${LINENO} (exit $rc)." >&2' ERR
umask 077
if [[ "${1:-}" == "--help" ]]; then
  echo 'Usage (interactive): sudo bash deploy/install-ubuntu-24.04.sh'
  echo 'Usage (automated): sudo env DOMAIN=vpn.example.com EMAIL=admin@example.com bash deploy/install-ubuntu-24.04.sh'
  echo 'Optional: PANEL_PATH=/xui/ PANEL_ADMIN_USER=admin MASIHA_BUILD_ROOT=/opt/masiha-vpn-build-20261011'
  exit 0
fi
[[ "$EUID" -eq 0 ]] || { echo "Run as root with sudo -E" >&2; exit 1; }
[[ -r /etc/os-release ]] || exit 1
source /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" && "$(uname -m)" == "x86_64" ]] ||
  { echo "Only fresh Ubuntu 24.04 amd64 is supported" >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# A partially completed setup has already created the machine's unique VPN
# CA and panel SQLite file. NEVER regenerate them through a second fresh run.
if [[ -f /etc/masiha-vpn-extension/.finalize-in-progress ]]; then
  echo "INCOMPLETE Masiha installation detected (safe resume marker present)." >&2
  echo "Do not rerun the fresh installer; this would risk existing SSL/CA state." >&2
  echo "Continue with: sudo bash $REPO_ROOT/deploy/finalize-ubuntu-24.04.sh --resume" >&2
  exit 3
fi
# Interactive prompts are required when values are not supplied via environment.
# On unattended runs, demand values explicitly instead of silently choosing a domain.
request_value() {
  local name="$1" label="$2" result
  if [[ ! -t 0 ]]; then
    echo "Missing $name. For noninteractive installation, pass DOMAIN=... EMAIL=... via sudo env." >&2
    exit 2
  fi
  read -r -p "$label: " result
  printf -v "$name" '%s' "$result"
}
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
if [[ -z "$DOMAIN" ]]; then request_value DOMAIN "Enter the VPN domain (DNS A record points here)"; fi
if [[ -z "$EMAIL" ]]; then request_value EMAIL "Enter the email for automatic Let's Encrypt SSL"; fi
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
# DNS validation belongs BEFORE source downloads and before installing x-ui.
# If DNS is missing, the operator can fix it and retry without risking a
# half-installed panel that the fresh-server guard would then refuse.
echo "=== Preflight: validating DNS A record for $DOMAIN ==="
if ! timeout 20s getent ahostsv4 "$DOMAIN" >/dev/null; then
  echo "ERROR: No resolvable IPv4 DNS A record for $DOMAIN." >&2
  echo "Point this domain to the new server and rerun before installing x-ui." >&2
  exit 1
fi
echo "DNS A record resolves for $DOMAIN. Ensure TCP/80 and TCP/443 reach this host."
for path in /etc/x-ui/x-ui.db /etc/masiha-vpn-extension/users.db /usr/local/x-ui/x-ui; do
  [[ ! -e "$path" ]] || { echo "Existing installation detected at $path; do NOT run this fresh-host installer. See migration guide." >&2; exit 1; }
done
[[ -f "$REPO_ROOT/deploy/3x-ui.patch" && -f "$REPO_ROOT/runtime/unified.py" ]] ||
  { echo "Incomplete Masiha release checkout" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git tar xz-utils build-essential pkg-config python3 \
  sqlite3 jq nginx certbot openvpn openconnect ocserv easy-rsa iptables ufw openssl gnupg python3-qrcode python3-pil
# Distribution ocserv may start on TCP/443 before Masiha config is installed.
# Release that listener so Nginx can terminate the public HTTPS endpoint.
systemctl stop ocserv.service 2>/dev/null || true
systemctl disable ocserv.service 2>/dev/null || true
# Official archives, pinned SHA256 and alternate URLs. HTTP 404 on a mirror
# must never be confused with a successful installation.
TOOLS=/opt/masiha-vpn-toolchains
mkdir -p "$TOOLS"
download_verified() {
  # $1 destination, $2 official sha256, $3 optional local archive path,
  # $4 optional user-selected HTTPS mirror, $5... trusted mirrors.
  local filename="$1" expected="$2" supplied="$3" alternate="$4" url tmp ip_mode
  shift 4
  tmp="${filename}.part"
  if [[ -s "$filename" ]] &&
     printf '%s  %s\n' "$expected" "$filename" | sha256sum --check --status; then
    echo "Using verified existing $(basename "$filename")"
    return 0
  fi
  if [[ -n "$supplied" ]]; then
    [[ -f "$supplied" ]] || { echo "Local archive not found: $supplied" >&2; return 1; }
    echo "Verifying local archive: $supplied"
    if ! printf '%s  %s\n' "$expected" "$supplied" | sha256sum --check; then
      echo "Local archive checksum does not match official release. Aborting." >&2
      return 1
    fi
    if [[ "$supplied" != "$filename" ]]; then
      cp "$supplied" "$filename"
    fi
    return 0
  fi
  local urls=()
  if [[ -n "$alternate" ]]; then
    [[ "$alternate" == https://* ]] || {
      echo "Custom mirror must use HTTPS." >&2; return 1;
    }
    urls+=("$alternate")
  fi
  urls+=("$@")
  # IPv4 takes priority. Only if it fails do we try IPv6 for that SAME URL.
  # If both fail (including checksum mismatch), move to the next URL.
  # This also prevents broken IPv6 routing from stalling normal IPv4 hosts.
  for url in "${urls[@]}"; do
    for ip_mode in 4 6; do
      echo "Downloading $(basename "$filename"): IPv$ip_mode -> $url"
      rm -f "$tmp"
      if curl "-$ip_mode" --fail --location --retry 1 --retry-delay 1 \
         --connect-timeout 8 --max-time 240 --output "$tmp" "$url"; then
        if printf '%s  %s\n' "$expected" "$tmp" | sha256sum --check --status; then
          mv -f "$tmp" "$filename"
          echo "Verified SHA256 of $(basename "$filename") via IPv$ip_mode"
          return 0
        fi
        echo "Checksum mismatch on IPv$ip_mode ($url); rejecting archive." >&2
      else
        echo "Download failed on IPv$ip_mode ($url)." >&2
      fi
    done
    echo "Both IP families failed on this mirror; checking next URL." >&2
  done
  rm -f "$tmp"
  echo "Cannot download a SHA256-verified $(basename "$filename")." >&2
  echo "Try a reachable HTTPS endpoint via MASIHA_GO_URL or MASIHA_NODE_URL." >&2
  echo "Or copy a valid archive to the server and set MASIHA_GO_ARCHIVE" >&2
  echo "or MASIHA_NODE_ARCHIVE before re-running the installer." >&2
  return 1
}
if [[ ! -x "$TOOLS/go/bin/go" ]]; then
  download_verified "$TOOLS/go.tar.gz" \
    "63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445" \
    "${MASIHA_GO_ARCHIVE:-}" "${MASIHA_GO_URL:-}" \
    "https://mirrors.aliyun.com/golang/go1.27.1.linux-amd64.tar.gz" \
    "https://go.dev/dl/go1.27.1.linux-amd64.tar.gz" \
    "https://dl.google.com/go/go1.27.1.linux-amd64.tar.gz" \
    "https://storage.googleapis.com/golang/go1.27.1.linux-amd64.tar.gz" \
    "https://golang.google.cn/dl/go1.27.1.linux-amd64.tar.gz"
  tar -xzf "$TOOLS/go.tar.gz" -C "$TOOLS"
fi
if [[ ! -x "$TOOLS/node-v24.21.0-linux-x64/bin/node" ]]; then
  download_verified "$TOOLS/node.tar.xz" \
    "fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6" \
    "${MASIHA_NODE_ARCHIVE:-}" "${MASIHA_NODE_URL:-}" \
    "https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz" \
    "https://nodejs.org/download/release/v24.21.0/node-v24.21.0-linux-x64.tar.xz" \
    "https://mirrors.aliyun.com/nodejs-release/v24.21.0/node-v24.21.0-linux-x64.tar.xz" \
    "https://npmmirror.com/mirrors/node/v24.21.0/node-v24.21.0-linux-x64.tar.xz" \
    "https://mirrors.cloud.tencent.com/nodejs-release/v24.21.0/node-v24.21.0-linux-x64.tar.xz" \
    "https://mirrors.huaweicloud.com/nodejs/v24.21.0/node-v24.21.0-linux-x64.tar.xz"
  tar -xJf "$TOOLS/node.tar.xz" -C "$TOOLS"
fi
export PATH="$TOOLS/go/bin:$TOOLS/node-v24.21.0-linux-x64/bin:$PATH"
[[ "$(go version)" == *"go1.27.1"* ]] || { echo "Go version mismatch" >&2; exit 1; }
[[ "$(node -v)" == "v24.21.0" ]] || { echo "Node version mismatch" >&2; exit 1; }
echo "=== Stage 1/5: Verify and build patched Masiha 3x-ui ==="
bash "$REPO_ROOT/deploy/build-3x-ui.sh"
BUILD_ROOT="${MASIHA_BUILD_ROOT:-/opt/masiha-vpn-build-20261011}"
[[ -s "$BUILD_ROOT/x-ui" ]] || exit 1
# Official pinned installer supplies Xray, geo data and native systemd helpers.
# The official downloaded 3x-ui binary is IMMEDIATELY replaced by the verified Masiha build.
echo "=== Stage 2/5: Install pinned upstream 3x-ui runtime and Xray ==="
XUI_NONINTERACTIVE=1 XUI_SSL_MODE=none XUI_DB_TYPE=sqlite \
  XUI_PANEL_PORT=62053 XUI_WEB_BASE_PATH="$PANEL_PATH" \
  bash "$BUILD_ROOT/src/install.sh"
[[ -d /usr/local/x-ui/bin && -f /etc/systemd/system/x-ui.service ]] ||
  { echo "3x-ui dependencies not installed; stop without starting Masiha extension" >&2; exit 1; }
systemctl stop x-ui.service 2>/dev/null || true
install -m 0755 "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui
install -d -m 0700 /etc/x-ui /etc/masiha-vpn-extension /opt/masiha-vpn-extension
# Web-based, admin-authenticated backup/restore worker runs outside x-ui
# via systemd-run so final recovery survives panel service restarts.
install -m 0644 "$REPO_ROOT/deploy/full-backup.py" /opt/masiha-vpn-extension/full_backup.py
install -m 0644 "$REPO_ROOT/deploy/web-backup-worker.py" /opt/masiha-vpn-extension/web_backup_worker.py
for file in manager.py unified.py worker.py sales.py sales_worker.py sales_bot_admin.py gamification.py entertainment.py media_library.py runtime_config.py; do
  install -m 0644 "$REPO_ROOT/runtime/$file" "/opt/masiha-vpn-extension/$file"
done
for file in test_unified.py test_sales.py test_media_library.py smoke_panel.py test_tunnels.py; do
  install -m 0644 "$REPO_ROOT/runtime/$file" "/opt/masiha-vpn-extension/$file"
done
# OpenConnect executes this route helper as a subprocess during QA. Installing
# it without +x makes the tunnel appear connected but prevents default routing.
install -m 0755 "$REPO_ROOT/runtime/test-oc-route.sh" /opt/masiha-vpn-extension/test-oc-route.sh
install -m 0755 "$REPO_ROOT/runtime/vpn-account-hook.sh" /opt/masiha-vpn-extension/vpn-account-hook.sh
# Root-only runtime configuration; account DBs, bot tokens and passwords stay OFF Git.
python3 - "$DOMAIN" "$PANEL_PATH" <<'PY'
import json,sys,pathlib
p=pathlib.Path('/etc/masiha-vpn-extension/config.json')
p.write_text(json.dumps({'domain':sys.argv[1],'panel_path':sys.argv[2]},indent=2)+'\n')
p.chmod(0o600)
PY
# ACME HTTP-01 needs public TCP/80 and the DNS A record to target this host.
# Domain was validated before performing any installation changes.
systemctl enable --now nginx
echo "=== Stage 3/5: Obtain Let's Encrypt certificate for $DOMAIN ==="
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
  location ^~ /.well-known/acme-challenge/ { root /var/www/html; }
  location / { return 301 https://\$host\$request_uri; }
}
server {
  listen 443 ssl http2;
  server_name $DOMAIN;
  ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
  client_max_body_size 16m;
  # Restrict large uploads to the authenticated backup-import endpoint.
  location = ${PANEL_PATH}panel/api/masiha-backup/import {
    client_max_body_size 512m;
    proxy_pass http://127.0.0.1:62053;
    proxy_http_version 1.1;
    proxy_request_buffering on;
    proxy_read_timeout 360s;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-Proto https;
  }
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
echo "=== Stage 4/5: Validate HTTPS reverse proxy ==="
nginx -t
systemctl reload nginx
# Renewed Let's Encrypt certificates must reload both TLS consumers.
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/masiha-reload.sh <<'HOOK'
#!/usr/bin/env bash
set -e
systemctl reload nginx.service
systemctl reload ocserv.service 2>/dev/null || systemctl restart ocserv.service
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/masiha-reload.sh
# The final step is shared with the safe recovery path. It preserves existing
# ACME certs, credentials and an already-generated CA on interrupted installs.
echo "=== Stage 5/5: Configure VPN services, Xray presets and Telegram extension ==="
bash "$REPO_ROOT/deploy/finalize-ubuntu-24.04.sh" --from-installer
# The separately hosted Masiha web modules survive future native SANAei updates.
echo "=== Stage 6/6: Install upgrade-safe Masiha independent web controls ==="
bash "$REPO_ROOT/deploy/install-sidecar.sh"
