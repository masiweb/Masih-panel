#!/usr/bin/env bash
# Read-only operational verification. Avoid printing passwords/tokens.
set -Eeuo pipefail
echo "=== Service health ==="
for service in x-ui.service openvpn-server@masiha.service ocserv.service masiha-vpn-extension.service nginx.service; do
  printf '%s: ' "$service"
  systemctl is-active "$service" || exit 1
done
echo "=== Listener health ==="
ss -lntup | grep -E ':(443|1194|4443|62053)\b' || true
echo "=== Native inbound and subscription configuration (no client secrets) ==="
python3 - <<'PY'
import sqlite3,os,json
db=sqlite3.connect('file:/etc/x-ui/x-ui.db?mode=ro',uri=True)
rows=list(db.execute('SELECT port,protocol,enable FROM inbounds'))
print("Xray inbounds:",len(rows))
if os.environ.get('MASIHA_EXPECT_DEFAULT_INBOUNDS')=='1':
 expected={8443:'vless',8444:'vless',8445:'vmess',8446:'trojan',8447:'vless',8448:'vless'}
 ports={int(port):proto for port,proto,enabled in rows if enabled}
 assert all(ports.get(port)==proto for port,proto in expected.items()),"Missing enabled default inbound(s)"
 print("Six preset inbounds: PASS")
settings=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('subURI','subPath','subPort','subListen','subEnable')"))
print("Subscription enabled:",settings.get('subEnable'))
if os.environ.get('MASIHA_EXPECT_DEFAULT_INBOUNDS')=='1':
 from pathlib import Path
 cfg=json.loads(Path('/etc/masiha-vpn-extension/config.json').read_text())
 assert settings.get('subURI')=='https://'+cfg['domain']+'/sub/',"Public subscription URL not configured"
 assert settings.get('subListen')=='127.0.0.1',"Subscription should listen only on loopback"
 assert settings.get('subPort')=='2096',"Subscription service port mismatch"
 assert settings.get('subEnable')=='true',"Subscription server is disabled"
 print("HTTPS subscription URL: PASS")
db.close()
PY
echo "=== Extension Python syntax ==="
python3 -m py_compile /opt/masiha-vpn-extension/{manager,unified,worker,sales,sales_worker,runtime_config}.py
echo "=== Shared quota and Telegram commerce tests (isolated temporary SQLite) ==="
(cd /opt/masiha-vpn-extension && python3 -m unittest -q test_unified.py test_sales.py)
echo "=== OpenConnect syntax ==="
ocserv -t -c /etc/ocserv/ocserv.conf
echo "=== Nginx syntax ==="
nginx -t
echo "Verification completed; tunnel connectivity, Telegram delivery and real-client flows need separate end-to-end tests."
