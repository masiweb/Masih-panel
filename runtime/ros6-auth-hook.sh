#!/bin/sh
set -eu
exec /usr/bin/python3 /opt/masiha-vpn-extension/routeros6.py auth "$1"
