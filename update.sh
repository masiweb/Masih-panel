#!/usr/bin/env bash
set -Eeuo pipefail
URL='https://raw.githubusercontent.com/masiweb/Masih-panel/upgrade/3x-ui-v3.11.0-migration/deploy/bootstrap.sh'
if [[ "${1:-}" == '--help' ]];then echo 'curl -fsSL https://raw.githubusercontent.com/masiweb/Masih-panel/upgrade/3x-ui-v3.11.0-migration/update.sh | sudo bash';exit 0;fi
TMP="$(mktemp /tmp/masiha-update-XXXXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL --retry 2 --connect-timeout 12 --max-time 75 -o "$TMP" "$URL"
bash "$TMP" update
