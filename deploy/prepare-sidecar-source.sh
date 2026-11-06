#!/usr/bin/env bash
# Applies the exact versioned frontend-sidecar source overlays to an isolated
# 3x-ui build checkout, without modifying webroot, nginx or systemd.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:-}"
[[ -n "$SRC" && -d "$SRC/src/pages/vpn" ]] || {
 echo 'Usage: prepare-sidecar-source.sh /path/to/frontend' >&2;exit 2;
}
SIDE="$ROOT/sidecar"
INSTALL="$ROOT/deploy/install-sidecar.sh"
[[ -s "$INSTALL" ]] || exit 1
TMP="$(mktemp /tmp/masiha-source-overlay-XXXXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
python3 - "$INSTALL" "$TMP" <<'PY'
from pathlib import Path
import sys
raw=Path(sys.argv[1]).read_text()
start='install -m 0644 "$SIDE/masiha-standalone.html"'
end='\n(\n export PATH="$NODE:$PATH"'
if raw.count(start)!=1 or raw.count(end)!=1:raise SystemExit('Source-overlay boundary changed; refuse unsafe extraction.')
code=raw[raw.index(start):raw.index(end)]
# The extracted installer segment is source-only, not a service installer.
for forbidden in ('/etc/', '/var/www/', 'systemctl', 'nginx ', 'rm -rf', 'rm -f', 'curl '):
 if forbidden in code:
  raise SystemExit('Unsafe command in source-only overlay segment.')
Path(sys.argv[2]).write_text('#!/usr/bin/env bash\nset -Eeuo pipefail\n'+code+'\n')
PY
export ROOT SIDE SRC
bash "$TMP"
echo 'MASIHA_SIDECAR_SOURCE_READY'
