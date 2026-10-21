#!/bin/sh
# pppd invokes hooks by filename in /etc/ppp/ip-up.d and /etc/ppp/ip-down.d
set -eu
IFNAME="$1"
export IFNAME
case "$0" in
  */ip-up.d/*) exec /usr/bin/python3 /opt/masiha-vpn-extension/ipsec_accounting.py ppp-up ;;
  */ip-down.d/*) exec /usr/bin/python3 /opt/masiha-vpn-extension/ipsec_accounting.py ppp-down ;;
esac