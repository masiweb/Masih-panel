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
for item in film_importer.py film2media_discovery.py test_film_importer.py test_film2media_discovery.py; do
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
install -m 0644 "$ROOT/runtime/media_library.py" "$DEST/media_library.py"
install -m 0644 "$ROOT/runtime/test_film_importer.py" "$DEST/test_film_importer.py"
install -m 0644 "$ROOT/runtime/test_film2media_discovery.py" "$DEST/test_film2media_discovery.py"
if [[ ! -e "$CFG" ]];then
  install -m 0600 "$ROOT/runtime/film_import.example.json" "$CFG"
fi
chmod 0600 "$CFG"
install -m 0644 "$ROOT/deploy/masiha-film-import.service" /etc/systemd/system/masiha-film-import.service
install -m 0644 "$ROOT/deploy/masiha-film-import.timer" /etc/systemd/system/masiha-film-import.timer
python3 -m py_compile "$DEST/media_library.py" "$DEST/film_importer.py" "$DEST/film2media_discovery.py"
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
systemctl enable --now masiha-film-import.timer
systemctl restart masiha-vpn-extension.service
systemctl is-active --quiet masiha-vpn-extension.service
systemctl is-active --quiet masiha-film-import.timer
echo "DEPLOY_FILM_PIPELINE_OK. BACKUP=$BACKUP"
echo "FILM_IMPORT_CONFIGURATION=$CFG"