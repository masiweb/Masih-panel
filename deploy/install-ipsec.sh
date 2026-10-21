#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo "Need root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="$(python3 -c 'import json;print(json.load(open("/etc/masiha-vpn-extension/config.json"))["domain"])')"
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || exit 2
CERT="/etc/letsencrypt/live/$DOMAIN"
[[ -r "$CERT/fullchain.pem" && -r "$CERT/privkey.pem" ]] || { echo "Missing TLS certificate" >&2; exit 1; }
[[ ! -s /etc/ipsec.conf && ! -s /etc/ipsec.secrets ]] || { echo "Existing IPsec configuration; refusing overwrite" >&2; exit 1; }
[[ ! -s /etc/ppp/chap-secrets ]] || { echo "Existing PPP credentials; refusing overwrite" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get install -q -y --no-install-recommends strongswan strongswan-swanctl libcharon-extra-plugins libcharon-extauth-plugins libstrongswan-extra-plugins xl2tpd ppp
install -d -m 0700 /etc/masiha-vpn-extension/ipsec /run/masiha-ipsec/ppp
install -m 0644 "$ROOT/runtime/ipsec_backend.py" /opt/masiha-vpn-extension/ipsec_backend.py
install -m 0644 "$ROOT/runtime/ipsec_accounting.py" /opt/masiha-vpn-extension/ipsec_accounting.py
python3 -m py_compile /opt/masiha-vpn-extension/ipsec_backend.py /opt/masiha-vpn-extension/ipsec_accounting.py
python3 - <<'PY'
from pathlib import Path
import secrets
path=Path("/etc/masiha-vpn-extension/ipsec/psk")
if not path.exists():
 path.write_text(secrets.token_urlsafe(48)+"\n")
 path.chmod(0o600)
PY
sed "s/MASIHA_DOMAIN/$DOMAIN/g" "$ROOT/deploy/ipsec.conf.masiha" > /etc/ipsec.conf
cat > /etc/ipsec.secrets <<EOF
: ECDSA $CERT/privkey.pem
include /etc/masiha-vpn-extension/ipsec/masiha.secrets
EOF
install -m 0600 "$ROOT/deploy/xl2tpd.conf.masiha" /etc/xl2tpd/xl2tpd.conf
install -m 0600 "$ROOT/deploy/options.masiha-l2tp" /etc/ppp/options.masiha-l2tp
chmod 0600 /etc/ipsec.conf /etc/ipsec.secrets
python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0,"/opt/masiha-vpn-extension")
import ipsec_backend
with ipsec_backend.db() as c:
 secret,chap=ipsec_backend.render_secrets(c,(ipsec_backend.ROOT/"psk").read_text().strip())
 ipsec_backend.atomic(ipsec_backend.ROOT/"masiha.secrets",secret)
 ipsec_backend.atomic(ipsec_backend.ROOT/"chap-secrets",chap)
p=Path("/etc/ppp/chap-secrets")
if p.exists():
 if p.is_symlink():p.unlink()
 elif any(line.strip() and not line.lstrip().startswith("#") for line in p.read_text().splitlines()):
  raise RuntimeError("Existing PPP secrets")
 else:p.unlink()
p.symlink_to(ipsec_backend.ROOT/"chap-secrets")
PY
install -m 0755 "$ROOT/runtime/masiha-ppp-hook.sh" /etc/ppp/ip-up.d/90-masiha-ipsec
install -m 0755 "$ROOT/runtime/masiha-ppp-hook.sh" /etc/ppp/ip-down.d/90-masiha-ipsec
install -m 0644 "$ROOT/deploy/masiha-ipsec-accounting.service" /etc/systemd/system/masiha-ipsec-accounting.service
install -m 0644 "$ROOT/deploy/masiha-ipsec-accounting.timer" /etc/systemd/system/masiha-ipsec-accounting.timer
systemctl daemon-reload
systemctl disable --now xl2tpd.service strongswan-starter.service masiha-ipsec-accounting.timer >/dev/null 2>&1 || true
echo "Staged IPsec services. Login and live accounting must be verified before enabling."