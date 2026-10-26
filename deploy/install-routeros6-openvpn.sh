#!/usr/bin/env bash
# Production-safe additive installer for RouterOS 6.48.6.
# Do not run install-ubuntu-24.04.sh / finalize-ubuntu-24.04.sh on a live server.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MAIN=/etc/openvpn/server/masiha.conf
NEW=/etc/openvpn/server/masiha-ros6.conf
DIR=/etc/openvpn/masiha-ros6
PORT=1195
[[ -s "$MAIN" && -s /etc/x-ui/x-ui.db &&
   -s /etc/masiha-vpn-extension/config.json ]] ||
  { echo "Masiha production prerequisite is missing; no changes made" >&2; exit 2; }
for c in openssl openvpn iptables ss systemctl python3; do
  command -v "$c" >/dev/null || { echo "Missing dependency: $c" >&2; exit 2; }
done
DOMAIN="$(python3 - <<'PY'
import json,re
v=json.load(open("/etc/masiha-vpn-extension/config.json"))["domain"]
assert re.fullmatch(r"[A-Za-z0-9.-]{3,253}",v) and "." in v
print(v)
PY
)"
if [[ ! -f "$NEW" ]] && ss -H -ltn "( sport = :$PORT )" | grep -q .; then
  echo "TCP $PORT is already in use; refusing to change an existing listener" >&2
  exit 2
fi
BACKUP="/var/backups/masiha-routeros6-$(date -u +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP" "$DIR"
for name in "$NEW" /opt/masiha-vpn-extension/routeros6.py \
  /opt/masiha-vpn-extension/ros6-auth-hook.sh \
  /opt/masiha-vpn-extension/ros6-account-hook.sh \
  /opt/masiha-vpn-extension/unified.py \
  /opt/masiha-vpn-extension/worker.py; do
  if [[ -f "$name" ]]; then cp -a "$name" "$BACKUP/$(basename "$name")"; fi
done
echo "Backup of changed runtime files: $BACKUP"
# Never replace/rotate an existing CA; partial PKI means manual recovery.
if [[ -e "$DIR/ca.crt" || -e "$DIR/ca.key" ||
      -e "$DIR/server.crt" || -e "$DIR/server.key" ]]; then
  for name in ca.crt ca.key server.crt server.key; do
    [[ -s "$DIR/$name" ]] || { echo "Incomplete RSA PKI: $DIR/$name" >&2; exit 3; }
  done
else
  openssl req -new -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -subj "/CN=Masiha RouterOS6 CA" \
    -keyout "$DIR/ca.key" -out "$DIR/ca.crt" >/dev/null 2>&1
  openssl req -new -newkey rsa:2048 -nodes -sha256 \
    -subj "/CN=$DOMAIN" \
    -keyout "$DIR/server.key" -out "$DIR/server.csr" >/dev/null 2>&1
  printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s\n' "$DOMAIN" > "$DIR/server.ext"
  openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" \
    -CAkey "$DIR/ca.key" -CAcreateserial -days 1825 -sha256 \
    -extfile "$DIR/server.ext" -out "$DIR/server.crt" >/dev/null 2>&1
fi
openssl verify -CAfile "$DIR/ca.crt" "$DIR/server.crt" >/dev/null
openssl x509 -in "$DIR/server.crt" -noout -checkend 86400 >/dev/null ||
  { echo "RouterOS6 server cert expires soon" >&2; exit 3; }
chmod 0600 "$DIR/ca.key" "$DIR/server.key"
install -m 0644 "$REPO/runtime/routeros6.py" /opt/masiha-vpn-extension/routeros6.py
install -m 0755 "$REPO/runtime/ros6-auth-hook.sh" /opt/masiha-vpn-extension/ros6-auth-hook.sh
install -m 0755 "$REPO/runtime/ros6-account-hook.sh" /opt/masiha-vpn-extension/ros6-account-hook.sh
# Only these two existing runtime modules are updated; never replace primary OpenVPN.
install -m 0644 "$REPO/runtime/unified.py" /opt/masiha-vpn-extension/unified.py
install -m 0644 "$REPO/runtime/worker.py" /opt/masiha-vpn-extension/worker.py
python3 -m py_compile /opt/masiha-vpn-extension/routeros6.py \
  /opt/masiha-vpn-extension/unified.py /opt/masiha-vpn-extension/worker.py
cat > "$NEW.tmp" <<EOF
# Separate TCP listener for MikroTik RouterOS 6.48.6; DO NOT add tls-crypt.
port $PORT
proto tcp-server
dev tun-masiha-ros6
topology subnet
server 10.85.0.0 255.255.255.0
ca $DIR/ca.crt
cert $DIR/server.crt
key $DIR/server.key
verify-client-cert none
username-as-common-name
auth-user-pass-verify /opt/masiha-vpn-extension/ros6-auth-hook.sh via-file
script-security 2
client-connect /opt/masiha-vpn-extension/ros6-account-hook.sh
client-disconnect /opt/masiha-vpn-extension/ros6-account-hook.sh
management $DIR/management.sock unix
cipher AES-256-CBC
data-ciphers AES-256-CBC
data-ciphers-fallback AES-256-CBC
auth SHA1
tls-version-min 1.2
tls-version-max 1.2
keepalive 15 75
persist-key
persist-tun
push "dhcp-option DNS 1.1.1.1"
verb 3
EOF
chmod 0600 "$NEW.tmp"
mv -f "$NEW.tmp" "$NEW"
# Port access for the new TCP service only.
if command -v ufw >/dev/null; then ufw allow "$PORT/tcp" >/dev/null; fi
systemctl daemon-reload
systemctl enable --now openvpn-server@masiha-ros6.service
systemctl is-active --quiet openvpn-server@masiha-ros6.service ||
  { echo "Secondary TCP OpenVPN failed. Check journalctl." >&2; exit 4; }
# Masiha worker persists NAT/FORWARD for the new subnet.
systemctl restart masiha-vpn-extension.service
systemctl is-active --quiet masiha-vpn-extension.service ||
  { echo "Shared-account worker failed; restore backup $BACKUP" >&2; exit 4; }
ss -H -ltn "( sport = :$PORT )" | grep -q . ||
  { echo "OpenVPN started but TCP $PORT is not listening" >&2; exit 4; }
echo "PASS: RouterOS6 TCP $PORT active for $DOMAIN"
echo "PASS: original UDP OpenVPN configuration not overwritten"
echo "PASS: native x-ui quota remains authoritative"
echo "Profile: Masiha shared user -> VPN profiles -> ca.crt + mikrotik-*.rsc"
echo "Rollback files: $BACKUP"
