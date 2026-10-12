#!/usr/bin/env bash
# Read-only release verification. Never touches live services or customer DBs.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PIN='7ef22f94c950ff09f0870e2295fa65ad5968742c'
echo "=== Masiha release preflight ==="
for f in deploy/install-ubuntu-24.04.sh deploy/finalize-ubuntu-24.04.sh deploy/build-3x-ui.sh deploy/verify-install.sh deploy/repair-panel-login.sh; do
  bash -n "$f"
  echo "Bash syntax OK: $f"
done
for f in runtime/sales.py runtime/sales_worker.py runtime/sales_bot_admin.py runtime/gamification.py runtime/entertainment.py runtime/media_library.py runtime/test_media_library.py runtime/test_sales.py runtime/unified.py runtime/test_unified.py runtime/test_tunnels.py deploy/bootstrap-inbounds.py deploy/extend-inbounds.py deploy/test_extend_inbounds.py deploy/check-panel-login.py deploy/test_check_panel_login.py; do
  python3 - "$f" <<'PY'
import ast,sys
with open(sys.argv[1],encoding="utf-8") as handle:
 ast.parse(handle.read(),filename=sys.argv[1])
PY
done
sh -n runtime/test-oc-route.sh
echo "Python syntax and portable tunnel route shell syntax OK"
echo "=== Isolated Xray presets test (no server changes) ==="
python3 -m unittest -q deploy/test_bootstrap_inbounds.py deploy/test_extend_inbounds.py deploy/test_check_panel_login.py
git apply --stat deploy/3x-ui.patch >/dev/null
test -s deploy/3x-ui.patch
grep -Fq 'frontend/src/pages/vpn/SalesBotPage.css' deploy/3x-ui.patch
grep -Fq 'frontend/src/pages/vpn/SalesBotPage.tsx' deploy/3x-ui.patch
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
(cd runtime && python3 -m unittest -q test_sales.py test_unified.py test_media_library.py)
echo "PASS: sales, named checkout, trials, Telegram and quota tests"
# Check exact key shell scripts and source files required at deployment time.
for f in runtime/manager.py runtime/unified.py runtime/worker.py runtime/sales.py \
         runtime/sales_worker.py runtime/sales_bot_admin.py runtime/gamification.py runtime/entertainment.py runtime/media_library.py runtime/runtime_config.py \
         runtime/vpn-account-hook.sh runtime/smoke_panel.py \
         runtime/test_sales.py runtime/test_media_library.py runtime/test_unified.py \
         deploy/bootstrap-inbounds.py deploy/extend-inbounds.py deploy/check-panel-login.py \
         deploy/repair-panel-login.sh deploy/finalize-ubuntu-24.04.sh \
         deploy/3x-ui.patch; do
  [[ -s "$f" ]] || { echo "Missing essential release file: $f" >&2;exit 2; }
done
echo "=== Release source preflight PASS ==="
echo "NOTE: This is not a fresh-VM install or real VPN tunnel test."
