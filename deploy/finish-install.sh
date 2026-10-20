#!/usr/bin/env bash
# Finalize a fresh deployment; safely resumable after independent web-UI failure.
# Never recreates server CA, client data, panel DB or TLS identity.
set -Eeuo pipefail
trap 'rc=$?; echo "ERROR: Masiha web finalization failed (exit $rc); credentials were not printed." >&2' ERR
umask 077
[[ "$(id -u)" == 0 ]] || { echo "Run with sudo/root" >&2;exit 1; }
[[ "$#" -ge 1 && ( "$1" == "--from-finalizer" || "$1" == "--resume" ) ]] ||
 { echo "Usage: sudo bash deploy/finish-install.sh --resume" >&2;exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CREDS=/root/masiha-vpn-initial-login.env
[[ -f "$CREDS" && "$(stat -c '%u:%a' "$CREDS")" == "0:600" ]] ||
 { echo "Missing root-only verified installer credentials. Refusing." >&2;exit 1; }
[[ -s /etc/masiha-vpn-extension/config.json &&
   -s /etc/x-ui/x-ui.db && -s /usr/local/x-ui/x-ui &&
   ! -f /etc/masiha-vpn-extension/.finalize-in-progress ]] ||
 { echo "First complete deploy/finalize-ubuntu-24.04.sh --resume." >&2;exit 1; }
# This is a FRESH-install completion tool, not a general native x-ui upgrader.
# It must refuse changed x-ui binaries rather than give misleading success.
BUILD_ROOT="/opt/masiha-vpn-build-20261011"
if [[ -v MASIHA_BUILD_ROOT && -n "$MASIHA_BUILD_ROOT" ]]; then BUILD_ROOT="$MASIHA_BUILD_ROOT"; fi
[[ -s "$BUILD_ROOT/x-ui" ]] || { echo "Missing compiled Masiha x-ui source build" >&2;exit 1; }
cmp -s "$BUILD_ROOT/x-ui" /usr/local/x-ui/x-ui ||
 { echo "Native x-ui was changed since fresh install. Refusing to re-finalize." >&2;exit 1; }

echo "=== Preparing independent Masiha sales/VPN/backup web interface ==="
bash "$ROOT/deploy/install-sidecar.sh"
# Optional media worker remains idle until the API key and model access are configured.
bash "$ROOT/deploy/install-ai-media.sh"
echo "=== Verifying native panel, OpenVPN, OpenConnect, shared quotas and sales worker ==="
MASIHA_EXPECT_DEFAULT_INBOUNDS=1 MASIHA_EXPECT_EXTRA_INBOUNDS=1 MASIHA_EXPECT_BENCHMARK_INBOUNDS=1 \
 bash "$ROOT/deploy/verify-install.sh"
echo "=== Verifying update-safe menu, separate admin site and authentication route ==="
bash "$ROOT/deploy/verify-sidecar-menu.sh"

# Parse root-only first-login settings without evaluating a shell source file.
# Both check-panel-login and verify-install have verified real HTTP admin auth.
python3 - "$CREDS" <<'PY'
from pathlib import Path
import json, shlex, sys
creds=Path(sys.argv[1])
data={}
for line in creds.read_text().splitlines():
    if '=' not in line or line.startswith('#'): continue
    k,v=line.split('=',1)
    words=shlex.split(v)
    if words: data[k]=words[0]
cfg=json.loads(Path('/etc/masiha-vpn-extension/config.json').read_text())
assert data.get('XUI_WEB_BASE_PATH')=='/xui/'
assert len(data.get('XUI_PASSWORD',''))>=24
assert data.get('XUI_USERNAME')
domain=cfg['domain']
assert domain and cfg['panel_path']=='/xui/'
print('\n===========================================================')
print(' MASIHA VPN MANAGER — COMPLETE INSTALLATION VERIFIED')
print('===========================================================')
print(' Main panel        : https://'+domain+'/xui/')
print(' Masiha management : https://'+domain+'/masiha-ui/')
print(' VPN subscription  : https://'+domain+'/sub/')
print(' Admin username    : '+data['XUI_USERNAME'])
print(' Admin password    : '+data['XUI_PASSWORD'])
print(' OpenVPN endpoint  : '+domain+':1194/udp')
print(' OpenConnect       : '+domain+':4443/tcp+udp')
print(" TLS               : Let\'s Encrypt")
print(' Xray              : 14 default/preset inbounds')
print(' Telegram bot      : configure BotFather token and admin ID in manager')
print(' Recovery file     : /root/masiha-vpn-initial-login.env (root only)')
print('===========================================================')
print(' IMPORTANT: Protect this terminal output and change the admin password.')
PY
