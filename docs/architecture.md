# Architecture

1. **Central Control Plane**: FastAPI, PostgreSQL, Redis.
2. **Node Agent**: installed per VPN node; manages Xray, WireGuard, OpenVPN and OpenConnect adapters.
3. **Admin Panel**: users, plans, nodes, countries, capacity, sales and reports.
4. **Telegram Bot**: purchase, delivery, renewal, usage and support flows.
5. **Security**: TLS, scoped node credentials, audited administrative actions, secrets outside Git.

Nodes are added independently. Adding a country or node never requires reinstalling the central panel.
