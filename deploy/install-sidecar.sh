#!/usr/bin/env bash
# Masiha web modules are separated from SANAei; never changes the native x-ui binary.
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT=/opt/masiha-vpn-build-20261011
if [[ -v MASIHA_BUILD_ROOT && -n "$MASIHA_BUILD_ROOT" ]]; then BUILD_ROOT="$MASIHA_BUILD_ROOT"; fi
SRC="$BUILD_ROOT/src/frontend"
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
install -m 0644 "$SIDE/MediaBroadcastSettings.tsx" "$SRC/src/pages/vpn/MediaBroadcastSettings.tsx"
install -m 0644 "$SIDE/SocialAdminPage.tsx" "$SRC/src/pages/vpn/SocialAdminPage.tsx"
install -m 0644 "$SIDE/ClubUserProfileModal.tsx" "$SRC/src/pages/vpn/ClubUserProfileModal.tsx"
install -m 0644 "$SIDE/AiSalesAdminPage.tsx" "$SRC/src/pages/vpn/AiSalesAdminPage.tsx"
install -m 0644 "$SIDE/BotTextsAdminPage.tsx" "$SRC/src/pages/vpn/BotTextsAdminPage.tsx"
# The official 3x-ui frontend is rebuilt from a pinned source revision.
# Inject media settings as an independent addon, never a native binary patch.
python3 - "$SRC/src/pages/vpn/SalesBotPage.tsx" <<'PYMEDIA'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
anchor='  <Card title="میزان امتیاز برای اطلاعات رسانه و محبوبیت">'
import_anchor="import BackupRestorePanel from './BackupRestorePanel';"
if '<MediaBroadcastSettings/>' not in s:
 if s.count(anchor)!=1 or s.count(import_anchor)!=1:
  raise SystemExit('Unsupported SalesBotPage version for media broadcast controls')
 s=s.replace(anchor,'  <MediaBroadcastSettings/>\n'+anchor,1)
 s=s.replace(import_anchor,import_anchor+"\nimport MediaBroadcastSettings from './MediaBroadcastSettings';",1)
old="require_contact:'دریافت شماره متعلق به حساب تلگرام'"
new="require_contact:'دریافت اجباری شماره حساب تلگرام (برای همه اعضای قدیمی و جدید)'"
if old in s:s=s.replace(old,new)
elif new not in s:raise SystemExit("Could not patch mandatory Telegram contact label")
p.write_text(s)
PYMEDIA
python3 - "$SRC/src/pages/vpn/SalesBotPage.tsx" <<'PYSOC'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
anchor="{key:'media_library',label:'کتابخانه موزیک و فیلم',children:"
imp="import MediaBroadcastSettings from './MediaBroadcastSettings';"
if "<SocialAdminPage/>" not in s:
 if s.count(anchor)!=1 or s.count(imp)!=1:
  raise SystemExit("Cannot mount SocialAdminPage on unsupported frontend")
 s=s.replace(anchor,"{key:'social_manager',label:'🌐 مدیریت کلاب ناشناس',children:<SocialAdminPage/>},\n "+anchor,1)
 s=s.replace(imp,imp+"\nimport SocialAdminPage from './SocialAdminPage';",1)
s=s.replace("مدیریت مسیحا سوشال","مدیریت کلاب ناشناس")
p.write_text(s)
PYSOC
python3 "$SIDE/patch_users_tab.py" "$SRC/src/pages/vpn/SalesBotPage.tsx"
python3 "$SIDE/patch_ai_sales_tab.py" "$SRC/src/pages/vpn/SalesBotPage.tsx"
python3 "$SIDE/patch_bot_texts_tab.py" "$SRC/src/pages/vpn/SalesBotPage.tsx"
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
install -m 0644 "$SIDE/nav.css" /var/www/masiha-ui/nav.css
chmod -R a+rX /var/www/masiha-ui
python3 -m py_compile /opt/masiha-vpn-extension/sidecar_server.py
install -m 0644 "$SIDE/masiha-vpn-sidecar.service" /etc/systemd/system/masiha-vpn-sidecar.service
# Licensed RozMusic importer. Do not auto-start without explicit opt-in.
if [[ -s "$ROOT/runtime/rozmusic_importer.py" &&
      -s "$ROOT/deploy/masiha-rozmusic-import.service" ]]; then
  # Both pieces are required: catalog crawler + bounded multipart uploader.
  [[ -s "$ROOT/runtime/roz_telegram_upload.py" ]] ||
    { echo "Missing Telegram MP3 uploader" >&2; exit 1; }
  install -m 0644 "$ROOT/runtime/roz_telegram_upload.py" /opt/masiha-vpn-extension/roz_telegram_upload.py
  install -m 0644 "$ROOT/runtime/rozmusic_importer.py" /opt/masiha-vpn-extension/rozmusic_importer.py
  install -m 0644 "$ROOT/runtime/roz_cover_backfill.py" /opt/masiha-vpn-extension/roz_cover_backfill.py
  install -m 0644 "$ROOT/runtime/audio_fingerprint.py" /opt/masiha-vpn-extension/audio_fingerprint.py
  install -m 0644 "$ROOT/runtime/ai_commerce.py" /opt/masiha-vpn-extension/ai_commerce.py
  install -m 0644 "$ROOT/runtime/ai_media.py" /opt/masiha-vpn-extension/ai_media.py
  install -m 0644 "$ROOT/runtime/ai_models.py" /opt/masiha-vpn-extension/ai_models.py
  install -m 0644 "$ROOT/runtime/ai_fal_video.py" /opt/masiha-vpn-extension/ai_fal_video.py
  install -m 0644 "$ROOT/runtime/ai_conversation.py" /opt/masiha-vpn-extension/ai_conversation.py
  install -m 0644 "$ROOT/deploy/masiha-rozmusic-import.service" /etc/systemd/system/masiha-rozmusic-import.service
fi

NGINX=/etc/nginx/sites-available/masiha-vpn
# Upgrade existing installations; keep all unrelated routing and SSL untouched.
python3 - "$NGINX" <<'PYSESSION'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
needle='  location = /masiha-ui/api/session {'
if needle not in s:
    location="""  # Native-path-independent authenticated Masiha session
  location = /masiha-ui/api/session {
    proxy_pass http://127.0.0.1:61794;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 12s;
  }
"""
    anchor='  location = /xui/sales-payment {'
    if s.count(anchor)!=1:raise SystemExit('Could not find stable sidecar location')
    s=s.replace(anchor,location+anchor,1)
    p.write_text(s)
PYSESSION
# Native x-ui authentication cookies are path-scoped. Serve the bootstrap
# and the shared APIs on the actual native path, not /masiha-ui/api/*.
# Regex allows future webBasePath changes without leaking the secret path;
# the Python sidecar checks it against the LIVE x-ui DB on every request.
python3 - "$NGINX" <<'PYNATIVE'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
needle='  # MASIHA-NATIVE-AUTH-ROUTES BEGIN'
if needle not in s:
    routes="""  # MASIHA-NATIVE-AUTH-ROUTES BEGIN
  # Retire the duplicate native sales screen; keep its backend API.
  location = /xui/panel/sales-bot { return 302 "/masiha-ui/#bot"; }
  location = /xui/panel/sales-bot/ { return 302 "/masiha-ui/#bot"; }
  location ~ "^/(?:[A-Za-z0-9._~-]+/){1,6}panel/sales-bot/?$" {
    return 302 "/masiha-ui/#bot";
  }
  location ~ "^/(?:[A-Za-z0-9._~-]+/){1,6}panel/api/(?:masiha-session|masiha-vpn|sales-bot|masiha-backup/(?:status|create|import|restore|download/.+))$" {
    client_max_body_size 512m;
    proxy_pass http://127.0.0.1:61794;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_request_buffering on;
    proxy_read_timeout 360s;
    proxy_send_timeout 360s;
  }
  # MASIHA-NATIVE-AUTH-ROUTES END
"""
    anchor='  # MASIHA-SIDECAR BEGIN'
    if s.count(anchor)!=1:raise SystemExit('Missing Masiha sidecar location marker')
    s=s.replace(anchor,routes+anchor,1)
    p.write_text(s)
PYNATIVE
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
# Older fresh installers left an exact backup import that shadowed the
# sidecar's authenticated prefix location. Correct that legacy proxy safely.
python3 - "$NGINX" <<'PYN'
from pathlib import Path
import sys,re
p=Path(sys.argv[1]);s=p.read_text()
pattern=r'(location\s*=\s*/xui/panel/api/masiha-backup/import\s*\{[^{}]*?proxy_pass\s+http://127\.0\.0\.1:)62053(;[^{}]*\})'
new,n=re.subn(pattern,lambda m:m.group(1)+'61794'+m.group(2),s)
if n>1: raise SystemExit('Duplicate native backup import location; review Nginx')
if n: p.write_text(new)
if 'MASIHA-SIDECAR BEGIN' not in new or 'sub_filter "</body>"' not in new:
    raise SystemExit('Missing independent Masiha route or navigation injection')
PYN
nginx -t
systemctl daemon-reload
systemctl enable --now masiha-vpn-sidecar.service
systemctl restart masiha-vpn-sidecar.service
if [[ -v MASIHA_ENABLE_ROZMUSIC && "$MASIHA_ENABLE_ROZMUSIC" == "1" ]]; then
  systemctl enable --now masiha-rozmusic-import.service
fi
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