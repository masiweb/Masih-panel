#!/usr/bin/env bash
# Read-only release verification. Never touches live services or customer DBs.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PIN='7ef22f94c950ff09f0870e2295fa65ad5968742c'
echo "=== Masiha release preflight ==="
for f in deploy/install-ubuntu-24.04.sh deploy/finalize-ubuntu-24.04.sh deploy/finish-install.sh deploy/install-sidecar.sh deploy/verify-sidecar-menu.sh deploy/build-3x-ui.sh deploy/verify-install.sh deploy/repair-panel-login.sh; do
  bash -n "$f"
  echo "Bash syntax OK: $f"
done
for f in runtime/sales.py runtime/sales_worker.py runtime/sales_bot_admin.py runtime/gamification.py runtime/entertainment.py runtime/media_library.py runtime/social.py runtime/test_social.py runtime/test_media_library.py runtime/test_sales.py runtime/unified.py runtime/test_unified.py runtime/test_tunnels.py deploy/bootstrap-inbounds.py deploy/extend-inbounds.py deploy/benchmark-inbounds.py deploy/full-backup.py deploy/web-backup-worker.py deploy/test_full_backup.py deploy/test_extend_inbounds.py deploy/test_benchmark_inbounds.py deploy/check-panel-login.py deploy/test_check_panel_login.py; do
  python3 - "$f" <<'PY'
import ast,sys
with open(sys.argv[1],encoding="utf-8") as handle:
 ast.parse(handle.read(),filename=sys.argv[1])
PY
done
sh -n runtime/test-oc-route.sh
echo "Python syntax and portable tunnel route shell syntax OK"
echo "=== Isolated Xray presets test (no server changes) ==="
python3 -m unittest -q deploy/test_bootstrap_inbounds.py deploy/test_extend_inbounds.py deploy/test_benchmark_inbounds.py deploy/test_full_backup.py deploy/test_check_panel_login.py
echo "=== Complete fresh-install pipeline regression tests ==="
python3 -m unittest -q deploy/test_install_pipeline.py
git apply --stat deploy/3x-ui.patch >/dev/null
test -s deploy/3x-ui.patch
grep -Fq 'frontend/src/pages/vpn/SalesBotPage.css' deploy/3x-ui.patch
grep -Fq 'frontend/src/pages/vpn/SalesBotPage.tsx' deploy/3x-ui.patch
grep -Fq 'frontend/src/pages/vpn/BackupRestorePanel.tsx' deploy/3x-ui.patch
grep -Fq 'internal/web/controller/masiha_backup.go' deploy/3x-ui.patch
test -s deploy/web-backup-worker.py
[[ -s sidecar/SocialAdminPage.tsx ]] ||
 { echo "Missing social management UI" >&2; exit 1; }
grep -Fq "social.py" deploy/install-ubuntu-24.04.sh
grep -Fq "SocialAdminPage.tsx" deploy/install-sidecar.sh
[[ -s sidecar/MediaBroadcastSettings.tsx ]] ||
 { echo "Missing media announcement web controls" >&2;exit 1; }
grep -Fq "MediaBroadcastSettings.tsx" deploy/install-sidecar.sh
grep -Fq "broadcast_series" sidecar/MediaBroadcastSettings.tsx
echo "Native 3x-ui release patch parses (includes styled Telegram sales UI)"
if [[ -n "${MASIHA_CHECK_SOURCE:-}" ]]; then
  [[ -d "$MASIHA_CHECK_SOURCE/.git" ]] || {
    echo "Not a git checkout: $MASIHA_CHECK_SOURCE" >&2;exit 2;
  }
  actual="$(git -C "$MASIHA_CHECK_SOURCE" rev-parse HEAD)"
  [[ "$actual" == "$PIN" ]] || {
    echo "Source upstream commit mismatch: $actual vs $PIN" >&2;exit 2;
  }
  git -C "$MASIHA_CHECK_SOURCE" apply --reverse --check "$ROOT/deploy/3x-ui.patch"
  echo "Pinned upstream source and full applied patch match"
fi
(cd runtime && python3 -m unittest -q test_sales.py test_unified.py test_media_library.py test_social.py)
grep -Fq "contact_onboarding_rollout_v2" runtime/sales_worker.py
grep -Fq "request_missing_phones" runtime/sales.py
grep -Fq "دریافت اجباری شماره حساب تلگرام" deploy/install-sidecar.sh

(cd runtime && python3 -m unittest -q test_rozmusic_importer.py test_rozmusic_upload.py)
for f in runtime/social.py runtime/test_social.py sidecar/SocialAdminPage.tsx docs/MASIHA_SOCIAL_FA.md; do
 [[ -s "$f" ]] || { echo "Missing social feature: $f" >&2; exit 1; }
done
for f in runtime/roz_telegram_upload.py runtime/rozmusic_importer.py runtime/test_rozmusic_upload.py runtime/test_rozmusic_importer.py deploy/masiha-rozmusic-import.service; do
  [[ -s "$f" ]] || { echo "Missing Telegram audio import file: $f" >&2; exit 2; }
done
! grep -q "VALUES(0,'music','link'" runtime/rozmusic_importer.py ||
  { echo "Unsafe URL-only public import detected" >&2; exit 2; }
echo "PASS: sales, named checkout, trials, Telegram and quota tests"
# Check exact key shell scripts and source files required at deployment time.
for f in runtime/manager.py runtime/unified.py runtime/worker.py runtime/sales.py \
         runtime/sales_worker.py runtime/sales_bot_admin.py runtime/gamification.py runtime/entertainment.py runtime/media_library.py runtime/runtime_config.py \
         runtime/vpn-account-hook.sh runtime/smoke_panel.py \
         runtime/test_sales.py runtime/test_media_library.py runtime/test_unified.py \
         deploy/bootstrap-inbounds.py deploy/extend-inbounds.py deploy/benchmark-inbounds.py deploy/full-backup.py deploy/check-panel-login.py \
         deploy/repair-panel-login.sh deploy/finalize-ubuntu-24.04.sh \
         deploy/3x-ui.patch; do
  [[ -s "$f" ]] || { echo "Missing essential release file: $f" >&2;exit 2; }
done
echo "=== Release source preflight PASS ==="
echo "NOTE: This is not a fresh-VM install or real VPN tunnel test."
