#!/bin/sh
set -eu
if [ "$reason" = connect ]; then
 ip address add "$INTERNAL_IP4_ADDRESS/32" dev "$TUNDEV"
 ip link set "$TUNDEV" up mtu 1300
 : "${MASIHA_QA_SERVER_IP:?Test server IPv4 address not provided}"
 ip route replace "$MASIHA_QA_SERVER_IP/32" via 10.203.71.1 dev mvqatest
 ip route replace default dev "$TUNDEV"
fi
