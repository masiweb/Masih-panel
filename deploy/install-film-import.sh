#!/usr/bin/env bash
# Additive deployment; preserves existing VPN, bot settings and music importer.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/masiha-vpn-extension
DB=/etc/masiha-vpn-extension/sales/sales.db
CFG=/etc/masiha-vpn-extension/sales/film_import.json
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="/var/backups/masiha-film-import-$STAMP"
install -d -m 0700 "$BACKUP"
[[ -f "$DB" && -f "$DEST/media_library.py" ]] || { echo "Existing Masiha installation not found" >&2; exit 1; }
cp -a "$DEST/media_library.py" "$BACKUP/media_library.py"
for item in film_importer.py film2media_discovery.py film2media_feed.py film_admin.py film_catalog.py film_catalog_pipeline.py test_film_importer.py test_film2media_discovery.py test_film2media_feed.py test_film_admin.py; do
  [[ ! -f "$DEST/$item" ]] || cp -a "$DEST/$item" "$BACKUP/$item"
done
# Copy a consistent online snapshot, never a raw SQLite file mid-transaction.
python3 - "$DB" "$BACKUP/sales.db" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1],timeout=35) as src:
 with sqlite3.connect(sys.argv[2]) as out:
  src.backup(out,pages=256,sleep=0.05)
PY
install -d -m 0700 /var/tmp/masiha-films
install -m 0644 "$ROOT/runtime/film_importer.py" "$DEST/film_importer.py"
install -m 0644 "$ROOT/runtime/film2media_discovery.py" "$DEST/film2media_discovery.py"
install -m 0644 "$ROOT/runtime/film2media_feed.py" "$DEST/film2media_feed.py"
for file in film_admin.py film_catalog.py film_catalog_pipeline.py test_film_admin.py; do
  install -m 0644 "$ROOT/runtime/$file" "$DEST/$file"
done
install -m 0644 "$ROOT/runtime/media_library.py" "$DEST/media_library.py"
install -m 0644 "$ROOT/runtime/test_film_importer.py" "$DEST/test_film_importer.py"
install -m 0644 "$ROOT/runtime/test_film2media_discovery.py" "$DEST/test_film2media_discovery.py"
install -m 0644 "$ROOT/runtime/test_film2media_feed.py" "$DEST/test_film2media_feed.py"
if [[ ! -e "$CFG" ]];then
  install -m 0600 "$ROOT/runtime/film_import.example.json" "$CFG"
fi
chmod 0600 "$CFG"
APPROVED=/etc/masiha-vpn-extension/sales/film_approved_ids.json
if [[ ! -e "$APPROVED" ]]; then
  printf '[]\n' > "$APPROVED"
fi
chmod 0600 "$APPROVED"
install -m 0644 "$ROOT/deploy/masiha-film-import.service" /etc/systemd/system/masiha-film-import.service
install -m 0644 "$ROOT/deploy/masiha-film-import.timer" /etc/systemd/system/masiha-film-import.timer
install -m 0644 "$ROOT/deploy/masiha-film-discover.service" /etc/systemd/system/masiha-film-discover.service
install -m 0644 "$ROOT/deploy/masiha-film-discover.timer" /etc/systemd/system/masiha-film-discover.timer
python3 -m py_compile "$DEST/media_library.py" "$DEST/film_importer.py" "$DEST/film2media_discovery.py" "$DEST/film2media_feed.py" "$DEST/film_admin.py" "$DEST/film_catalog.py" "$DEST/film_catalog_pipeline.py"
# No Telegram messages emitted by setup/setting toggle; notifications only
# when a NEW authorized video actually publishes.
python3 - <<'PY'
import sys
sys.path.insert(0,"/opt/masiha-vpn-extension")
import film_importer
with film_importer.connect() as c:
 c.execute("UPDATE media_settings SET v=1 WHERE k IN ('broadcast_movie','broadcast_series')")
 c.execute("INSERT OR IGNORE INTO media_settings(k,v) VALUES('broadcast_movie',1),('broadcast_series',1)")
 c.commit()
print("FILM_SCHEMA_OK, MOVIE_AND_SERIES_NOTICES_ENABLED")
PY
systemctl daemon-reload
if [[ "${MASIHA_MIGRATION_STAGING:-0}" == "1" ]]; then
  systemctl disable --now masiha-film-import.timer masiha-film-discover.timer
  if systemctl list-unit-files --no-legend masiha-film2media-discovery.timer | grep -q "^masiha-film2media-discovery.timer "; then
    systemctl disable --now masiha-film2media-discovery.timer
  fi
else
  systemctl enable --now masiha-film-import.timer
  systemctl enable --now masiha-film-discover.timer
fi
bash "$ROOT/deploy/install-media-cache-maintenance.sh"
if [[ "${MASIHA_MIGRATION_STAGING:-0}" != "1" ]]; then
  systemctl restart masiha-vpn-extension.service
  systemctl is-active --quiet masiha-vpn-extension.service
  systemctl is-active --quiet masiha-film-import.timer
  systemctl is-active --quiet masiha-film-discover.timer
else
  echo "MIGRATION_STAGING: upload timers and bot activation intentionally deferred"
fi
echo "DEPLOY_FILM_PIPELINE_OK. BACKUP=$BACKUP"
echo "FILM_IMPORT_CONFIGURATION=$CFG"