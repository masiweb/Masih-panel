#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Root is required' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
install -m 0644 "$ROOT/runtime/ai_commerce.py" /opt/masiha-vpn-extension/ai_commerce.py
install -m 0644 "$ROOT/runtime/ai_media.py" /opt/masiha-vpn-extension/ai_media.py
install -m 0644 "$ROOT/runtime/ai_models.py" /opt/masiha-vpn-extension/ai_models.py
install -m 0644 "$ROOT/runtime/ai_fal_video.py" /opt/masiha-vpn-extension/ai_fal_video.py
install -m 0644 "$ROOT/runtime/ai_conversation.py" /opt/masiha-vpn-extension/ai_conversation.py
install -m 0644 "$ROOT/deploy/masiha-ai-media.service" /etc/systemd/system/
install -m 0644 "$ROOT/deploy/masiha-ai-media.timer" /etc/systemd/system/
python3 -m py_compile /opt/masiha-vpn-extension/ai_commerce.py /opt/masiha-vpn-extension/ai_media.py /opt/masiha-vpn-extension/ai_models.py /opt/masiha-vpn-extension/ai_fal_video.py
systemctl daemon-reload
systemctl enable --now masiha-ai-media.timer
echo 'AI media queue installed.'
