#!/usr/bin/env bash
# Single entrypoint for both fresh installations and in-place upgrades.
# Always fetches the latest fast-forward release before executing it.
set -Eeuo pipefail
umask 077
MODE="${1:-}"
BRANCH='upgrade/3x-ui-v3.11.0-migration'
REPO='/root/Masih-panel'
URL='https://github.com/masiweb/Masih-panel.git'

[[ "$EUID" == 0 ]] || { echo 'Use sudo/root to install or update Masiha VPN Manager.' >&2; exit 2; }
[[ "$MODE" == install || "$MODE" == update ]] || {
  echo 'Usage: bootstrap.sh {install|update}' >&2; exit 2;
}
if ! command -v git >/dev/null;then
  if [[ "$MODE" == install ]];then
    echo 'Installing Git prerequisite for fresh host ...'
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y git ca-certificates
  else
    echo 'Git is missing on this installed host; refusing to modify its services.' >&2
    exit 1
  fi
fi
if [[ "$MODE" == install ]];then
  # A fresh install must never become a destructive reinstall.
  for marker in /usr/local/x-ui/x-ui /etc/x-ui/x-ui.db /etc/masiha-vpn-extension/config.json;do
    [[ ! -e "$marker" ]] || { echo 'Existing installation detected; use the UPDATE command instead.' >&2;exit 3; }
  done
  if [[ -z "${DOMAIN:-}" ]];then
    [[ -r /dev/tty ]] || { echo 'Set DOMAIN and EMAIL in the environment (no interactive TTY).' >&2;exit 2; }
    read -r -p 'VPN domain (e.g. vpn.example.com): ' DOMAIN </dev/tty
    export DOMAIN
  fi
  if [[ -z "${EMAIL:-}" ]];then
    [[ -r /dev/tty ]] || { echo 'Set EMAIL in the environment (no interactive TTY).' >&2;exit 2; }
    read -r -p 'Email for SSL certificate: ' EMAIL </dev/tty
    export EMAIL
  fi
fi
if [[ -e "$REPO/.git" ]];then
  EXISTING="$(git -C "$REPO" remote get-url origin)"
  case "$EXISTING" in
    https://github.com/masiweb/Masih-panel|https://github.com/masiweb/Masih-panel.git|git@github.com:masiweb/Masih-panel.git) ;;
    *) echo 'Unexpected Git origin; refusing to update another project.' >&2;exit 1;;
  esac
  git -C "$REPO" diff --quiet &&
    git -C "$REPO" diff --cached --quiet || {
      echo 'Tracked local edits exist. Refusing to overwrite them; save or commit first.' >&2;exit 1;
    }
  git -C "$REPO" fetch --no-tags origin "$BRANCH"
  if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH";then
    git -C "$REPO" switch "$BRANCH"
  else
    git -C "$REPO" switch -c "$BRANCH" --track "origin/$BRANCH"
  fi
  git -C "$REPO" merge --ff-only FETCH_HEAD
elif [[ ! -e "$REPO" ]];then
  git clone --single-branch --branch "$BRANCH" "$URL" "$REPO"
else
  echo "Cannot overwrite existing non-Git directory $REPO." >&2
  exit 1
fi
echo "MASIHA_RELEASE=$(git -C "$REPO" rev-parse --short HEAD)"
if [[ "$MODE" == install ]];then
  echo 'Running the fresh Ubuntu 24.04 installer.'
  exec bash "$REPO/deploy/install-ubuntu-24.04.sh"
fi
echo 'Running in-place UPDATE (no backup restore, no database replacement).'
exec bash "$REPO/deploy/update-existing.sh"
