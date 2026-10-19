#!/usr/bin/env bash
# Verify the updater-resistant Masiha navigation bridge.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="$(python3 -c 'import json;print(json.load(open("/etc/masiha-vpn-extension/config.json"))["domain"])')"
NODE=/opt/masiha-vpn-toolchains/node-v24.21.0-linux-x64/bin/node

test -s /var/www/masiha-ui/nav.js
test -s /var/www/masiha-ui/nav.css
test -s /var/www/masiha-ui/masiha-standalone.html
test -s /opt/masiha-vpn-extension/sidecar_server.py
test -x "$NODE"
nginx -t
systemctl is-active --quiet nginx x-ui masiha-vpn-sidecar.service
"$NODE" --check /var/www/masiha-ui/nav.js
"$NODE" "$ROOT/sidecar/test_nav.cjs"

curl -fsSk --noproxy '*' --max-time 10 \
 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/xui/" |
 grep -F -q '/masiha-ui/nav.js'
for p in /masiha-ui/ /masiha-ui/nav.js /masiha-ui/nav.css; do
 curl -fsSk --noproxy '*' --max-time 10 \
  --resolve "$DOMAIN:443:127.0.0.1" -o /dev/null "https://$DOMAIN$p"
done
grep -q "MASIHA-SIDECAR BEGIN" /etc/nginx/sites-available/masiha-vpn
grep -q "location ^~ /xui/panel/api/masiha-backup/" /etc/nginx/sites-available/masiha-vpn
echo "UPGRADE_SAFE_MENU=PASS"
echo "NATIVE_XUI=$(/usr/local/x-ui/x-ui -v)"
echo "MANAGER_URL=https://$DOMAIN/masiha-ui/"
