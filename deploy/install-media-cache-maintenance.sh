#!/usr/bin/env bash
# Safe additive media-cache lifecycle setup for existing Masiha installations.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/masiha-vpn-extension
DB=/etc/masiha-vpn-extension/sales/sales.db
[[ -f "$DB" && -f "$DEST/sales.py" ]] || {
  echo "Existing Masiha bot installation not found" >&2; exit 1;
}
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="/var/backups/masiha-media-cache-$STAMP"
install -d -m 0700 "$BACKUP"
for file in roz_telegram_upload.py rozmusic_importer.py film_importer.py media_cache_maintenance.py; do
  [[ ! -f "$DEST/$file" ]] || cp -a "$DEST/$file" "$BACKUP/$file"
  install -m 0644 "$ROOT/runtime/$file" "$DEST/$file"
done
install -m 0644 "$ROOT/deploy/masiha-media-cache-maintenance.service" /etc/systemd/system/masiha-media-cache-maintenance.service
install -m 0644 "$ROOT/deploy/masiha-media-cache-maintenance.timer" /etc/systemd/system/masiha-media-cache-maintenance.timer
python3 -m py_compile "$DEST/roz_telegram_upload.py" "$DEST/rozmusic_importer.py" "$DEST/film_importer.py" "$DEST/media_cache_maintenance.py"
python3 "$DEST/media_cache_maintenance.py" --dry-run
systemctl daemon-reload
systemctl enable --now masiha-media-cache-maintenance.timer
systemctl is-active --quiet masiha-media-cache-maintenance.timer
echo "MASIHA_MEDIA_CACHE_MAINTENANCE_INSTALLED BACKUP=$BACKUP"
