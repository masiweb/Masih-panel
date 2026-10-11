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
echo "=== Extension Python syntax ==="
python3 -m py_compile /opt/masiha-vpn-extension/{manager,unified,worker,sales,sales_worker,runtime_config}.py
echo "=== Native sharing tests (isolated temporary SQLite) ==="
(cd /opt/masiha-vpn-extension && python3 -m unittest -q test_unified.py)
echo "=== OpenConnect syntax ==="
ocserv -t -c /etc/ocserv/ocserv.conf
echo "=== Nginx syntax ==="
nginx -t
echo "Verification completed; tunnel connectivity, Telegram delivery and real-client flows need separate end-to-end tests."
