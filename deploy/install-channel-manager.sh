#!/usr/bin/env bash
# Approval-only Telegram channel manager deployment; no token values in repo.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/install-channel-manager.sh' >&2; exit 1; fi
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$PROJECT/runtime"
TARGET="/opt/masiha-vpn-extension"
BACKUP="/var/backups/masiha-vpn-extension/channel-manager-$(date -u +%Y%m%dT%H%M%SZ)"
for name in sales.py sales_worker.py sales_bot_admin.py channel_manager.py channel_manager_worker.py; do
 test -f "$RUNTIME/$name" || { echo "Missing source $name"; exit 1; }
done
test -f "$TARGET/sales.py" || { echo "Existing Masiha runtime not found"; exit 1; }
for name in sales.py sales_worker.py sales_bot_admin.py channel_manager.py channel_manager_worker.py; do
 python3 -m py_compile "$RUNTIME/$name"
done
cd "$RUNTIME"
python3 -m unittest -v test_channel_manager
mkdir -p "$BACKUP"
chmod 0700 "$BACKUP"
for name in sales.py sales_worker.py sales_bot_admin.py channel_manager.py channel_manager_worker.py; do
 if [[ -f "$TARGET/$name" ]]; then cp -a "$TARGET/$name" "$BACKUP/$name"; fi
done
for name in sales.py sales_worker.py sales_bot_admin.py channel_manager.py channel_manager_worker.py; do
 install -m 0644 -o root -g root "$RUNTIME/$name" "$TARGET/$name"
done
install -m 0644 "$PROJECT/deploy/masiha-channel-manager.service" /etc/systemd/system/
install -m 0644 "$PROJECT/deploy/masiha-channel-manager.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now masiha-channel-manager.timer
if ! systemctl restart masiha-vpn-extension.service || ! systemctl is-active --quiet masiha-vpn-extension.service; then
 echo "Bot restart failed; restoring original Python files from $BACKUP" >&2
 for name in sales.py sales_worker.py sales_bot_admin.py channel_manager.py channel_manager_worker.py; do
  if [[ -f "$BACKUP/$name" ]]; then cp -a "$BACKUP/$name" "$TARGET/$name"; else rm -f "$TARGET/$name"; fi
 done
 systemctl restart masiha-vpn-extension.service || true
 exit 1
fi
echo "Channel manager installed. Rollback files in: $BACKUP"
echo "Bot: $(systemctl is-active masiha-vpn-extension.service)"
echo "Scheduler: $(systemctl is-active masiha-channel-manager.timer)"
echo "Channel publication remains OFF until admin verification and activation."
