# Masiha VPN Manager

Masiha VPN Manager is deployed as a **patched SANAei 3x-ui** with native Xray inbounds, OpenVPN, OpenConnect and a Telegram commerce manager. New deployments are generated from a pinned upstream commit plus our auditable patch and Python runtime sources.

**[راهنمای نصب کامل روی سرور جدید (فارسی)](docs/INSTALL_NEW_SERVER_FA.md)**

## Deployment source of truth (2026-10-11)

- `deploy/3x-ui.patch`: patch for 29 files over upstream 3x-ui commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`.
- `runtime/`: live Python extension, shared quota/expiry accounting, OpenVPN and OpenConnect provisioning, Telegram sales worker, safe test scripts.
- `deploy/build-3x-ui.sh`: reproduces patched React and Go build.
- `deploy/install-ubuntu-24.04.sh`: fresh Ubuntu 24.04 amd64 provisioning; **refuses pre-existing x-ui installations**.
- `deploy/bootstrap-inbounds.py`: generate six fresh-server Xray presets with unique Reality keys and domain TLS certificates.
- `deploy/finalize-ubuntu-24.04.sh`: safely recover partially completed installations and provision HTTPS subscriptions/default Xray inbounds.
- `deploy/verify-install.sh`: post-install service sanity, preset/subscription checks and isolated shared-quota/Telegram commerce tests.
- `docs/RELEASE_2026-10-11.md`: verification and known limits.

The older `app/` FastAPI control-plane and `node_agent/` prototype are retained for historical reference **but are not the implementation currently running on the VPN server**. Do not deploy that prototype as a replacement for the native extension.

## Supported flows

- Native 3x-ui Xray service with VLESS, VMess, Trojan and other supported inbounds.
- Shared per-client expiry and traffic across Xray, OpenVPN and OpenConnect, including automatically provisioned VPN credentials and downloadable profiles.
- Telegram bot sales, plan management, receipt-based top-ups and admin approval, plus a visible failed-delivery queue and admin retry. Bot activation requires an actual token and admin Telegram IDs.
- Automatic HTTPS subscription URI and per-protocol OpenVPN/OpenConnect profiles, with TLS termination by Nginx. Fresh installs include VLESS Reality, VLESS TLS, VMess WS TLS, Trojan TLS, VLESS WS TLS and VLESS XHTTP TLS.

Never commit customer profiles, live databases, private keys, passwords, bot tokens or production configuration. The public repository contains only source code, templates and instructions. See installation guide for the complete list of backup-only paths.
