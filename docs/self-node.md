# Central Self-Node

The control-plane host is also enrolled as a VPN node named `Central-DE-01`.

## Protocol listeners

| Protocol | Implementation | Port |
|---|---|---|
| Xray | VLESS + REALITY | TCP 8443 |
| WireGuard | wg0 | UDP 51820 |
| OpenVPN | openvpn-server@server | UDP 1194 |
| OpenConnect | ocserv | TCP/UDP 4443 |

The web panel keeps TCP 80/443 through Nginx. VPN listeners use separate ports to avoid interrupting HTTPS.

## Runtime services

- `xray.service`
- `wg-quick@wg0.service`
- `openvpn-server@server.service`
- `ocserv.service`
- `masiha-vpn-nat.service`
- `masiha-node-agent.service`

All services are enabled at boot. IPv4 forwarding and NAT are active for:

- `10.70.0.0/24` — WireGuard
- `10.71.0.0/24` — OpenVPN
- `10.72.0.0/24` — OpenConnect

Private keys, node credentials, certificate keys and generated client credentials are stored only on the server and must never be committed.
