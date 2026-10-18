#!/usr/bin/env bash
# Masiha web modules are separated from SANAei; never changes the native x-ui binary.
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC=/opt/masiha-vpn-build-20261011/src/frontend
SIDE="$ROOT/sidecar"
NODE=/opt/masiha-vpn-toolchains/node-v24.21.0-linux-x64/bin
[[ -d "$SRC/node_modules" && -x "$SRC/node_modules/.bin/vite" ]] || {
  echo "Missing prebuilt frontend/dependencies at $SRC" >&2;exit 1;
}
[[ -x "$NODE/node" ]] || { echo "Missing pinned Node.js" >&2;exit 1; }
[[ -f /etc/masiha-vpn-extension/config.json ]] || { echo "Missing Masiha config" >&2;exit 1; }
[[ -f /etc/nginx/sites-available/masiha-vpn ]] || { echo "Missing Masiha nginx site" >&2;exit 1; }
install -d -m 0755 /var/www/masiha-ui /opt/masiha-vpn-extension
install -m 0644 "$SIDE/server.py" /opt/masiha-vpn-extension/sidecar_server.py
install -m 0644 "$SIDE/masiha-standalone.html" "$SRC/masiha-standalone.html"
install -m 0644 "$SIDE/masiha-standalone.tsx" "$SRC/src/masiha-standalone.tsx"
install -m 0644 "$SIDE/standalone.css" "$SRC/src/masiha-standalone.css"
install -m 0644 "$SIDE/masiha-vite.config.mjs" "$SRC/masiha-vite.config.mjs"
python3 - "$SRC/src/pages/vpn/VPNPage.tsx" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]);s=p.read_text()
old="'آدرس سرور https://xu.chanelchat.ir:4443 است. نام کاربری و رمز تعیین‌شده را در برنامه وارد کنید. رمز ذخیره‌شده نمایش داده نمی‌شود؛ برای تغییر آن از ویرایش استفاده کنید.'"
new="'آدرس سرور https://' + (data?.status.endpoint || window.location.hostname + ':4443') + ' است. نام کاربری و رمز تعیین‌شده را در برنامه وارد کنید. رمز ذخیره‌شده نمایش داده نمی‌شود؛ برای تغییر آن از ویرایش استفاده کنید.'"
if old in s:p.write_text(s.replace(old,new))
elif new not in s:raise SystemExit("Unsupported VPNPage source revision")
PY
(
 export PATH="$NODE:$PATH"
 cd "$SRC"
 ./node_modules/.bin/vite build --config masiha-vite.config.mjs
)
install -m 0644 "$SIDE/nav.js" /var/www/masiha-ui/nav.js
chmod -R a+rX /var/www/masiha-ui
python3 -m py_compile /opt/masiha-vpn-extension/sidecar_server.py
install -m 0644 "$SIDE/masiha-vpn-sidecar.service" /etc/systemd/system/masiha-vpn-sidecar.service
NGINX=/etc/nginx/sites-available/masiha-vpn
if ! grep -q 'MASIHA-SIDECAR BEGIN' "$NGINX"; then
  cp -a "$NGINX" "$NGINX.before-masiha-sidecar.$(date -u +%Y%m%dT%H%M%SZ).bak"
  python3 - "$NGINX" "$SIDE/nginx-locations.conf" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text();extra=Path(sys.argv[2]).read_text()
assert s.count('  location /sub/ {')==1
s=s.replace('  location /sub/ {',extra+'  location /sub/ {',1)
if 'sub_filter "</body>"' not in s:
    assert s.count('    proxy_set_header Upgrade $http_upgrade;')==1
    s=s.replace('    proxy_set_header Upgrade $http_upgrade;',
    '    proxy_set_header Accept-Encoding "";\n'
    '    sub_filter_once on;\n'
    '    sub_filter "</body>" \'<script src="/masiha-ui/nav.js" data-cfasync="false"></script></body>\';\n'
    '    proxy_set_header Upgrade $http_upgrade;',1)
p.write_text(s)
PY
fi
nginx -t
systemctl daemon-reload
systemctl enable --now masiha-vpn-sidecar.service
systemctl restart masiha-vpn-sidecar.service
systemctl reload nginx
ready=0
for count in 1 2 3 4 5 6 7 8 9 10; do
  if curl --fail --silent --max-time 3 http://127.0.0.1:61794/healthz >/dev/null; then
    ready=1;break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { systemctl status masiha-vpn-sidecar --no-pager >&2;exit 1; }
echo "PASS: Independent Masiha web UI installed; SANAei binary untouched."
