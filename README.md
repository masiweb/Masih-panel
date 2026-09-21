# Masiha VPN Platform

Central multi-node VPN management platform.

## Initial scope
- Central API and management core
- Dynamic multi-country node registry
- Xray, WireGuard, OpenVPN, and OpenConnect node capabilities
- Node heartbeat and health state
- Telegram bot integration in the first product phase
- HTTPS-first deployment
- Git-based delivery without committed secrets

## Current milestone
Version 0.7.0 adds real protocol provisioning plus admin config view/copy, downloads, QR codes and per-service subscription links. The central host is also an online self-node for all four protocols.

## Version 0.7.0 — Inbound / Client architecture

- First-class, node-owned VPN inbounds with protocol-specific settings
- Independent VPN clients attached to multiple inbounds
- Shared quota and expiry per client
- One subscription URL and HTML profile per client
- Per-inbound config, QR, copy and download actions
- Backward-compatible migration from legacy VPN services
- Node Agent jobs for inbound create/update/delete and client reprovision

