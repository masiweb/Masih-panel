#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo "Use root" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q --no-install-recommends ffmpeg python3-numpy python3-scipy python3-docopt python3-joblib python3-psutil
mkdir -p /opt/masiha-audio-fp /var/lib/masiha-audio-fp
chmod 0700 /var/lib/masiha-audio-fp
if [[ ! -d /opt/masiha-audio-fp/audfprint/.git ]]; then
 git clone https://github.com/dpwe/audfprint.git /opt/masiha-audio-fp/audfprint
fi
# Reviewed upstream commit: reproducible reinstallation, no floating master.
git -C /opt/masiha-audio-fp/audfprint fetch --quiet origin cb03ba99feafd41b8874307f0f4e808a6ce34362
git -C /opt/masiha-audio-fp/audfprint checkout --quiet cb03ba99feafd41b8874307f0f4e808a6ce34362
[[ "$(git -C /opt/masiha-audio-fp/audfprint rev-parse HEAD)" == cb03ba99feafd41b8874307f0f4e808a6ce34362 ]]
install -m 0644 "$ROOT/runtime/audio_fingerprint.py" /opt/masiha-vpn-extension/audio_fingerprint.py
install -m 0644 "$ROOT/deploy/masiha-audio-fingerprint.service" /etc/systemd/system/
install -m 0644 "$ROOT/deploy/masiha-audio-fingerprint.timer" /etc/systemd/system/
python3 -m py_compile /opt/masiha-vpn-extension/audio_fingerprint.py
systemctl daemon-reload
systemctl enable --now masiha-audio-fingerprint.timer
echo "Audio fingerprint worker and timer enabled; Telegram original MP3s are transient only."
