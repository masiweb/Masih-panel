#!/usr/bin/env python3
"""RouterOS 6 OpenVPN TCP adapter using the existing Masiha x-ui subscription.

This listener is independent of the default UDP/tls-crypt OpenVPN service.
No new subscription, password database or quota is created.
"""
import hmac
import os
import re
import socket
import sys
import time
from pathlib import Path

BASE = Path("/etc/openvpn/masiha-ros6")
CONFIG = Path("/etc/openvpn/server/masiha-ros6.conf")
SOCKET = BASE / "management.sock"
PORT = 1195
USER_RE = re.compile(r"^mc-[0-9a-f]{24}$")


def installed():
    return CONFIG.is_file() and (BASE / "ca.crt").is_file()


def identity(c, username):
    import unified
    if not isinstance(username, str) or not USER_RE.fullmatch(username):
        return None
    account = unified.account_for_username(c, username)
    if not account or not account["ready"]:
        return None
    native = c.execute(
        """SELECT c.*, t.up AS traffic_up, t.down AS traffic_down,
           t.total AS traffic_total, t.expiry_time AS traffic_expiry,
           t.enable AS traffic_enable
           FROM clients c LEFT JOIN client_traffics t ON t.email=c.email
           WHERE c.id=? AND c.created_at=?""",
        (account["client_id"], account["generation"]),
    ).fetchone()
    if native is None or not unified.permitted(native):
        return None
    return account


def auth(password_file):
    import unified
    try:
        if Path(password_file).stat().st_size > 400:
            return 1
        lines = Path(password_file).read_text(encoding="utf-8").splitlines()
        if len(lines) != 2 or len(lines[1]) > 160:
            return 1
        username, password = lines
        with unified.connection() as conn:
            account = identity(conn, username)
            if account and hmac.compare_digest(password, account["password"]):
                return 0
    except (OSError, UnicodeError, ValueError):
        pass
    return 1


def session(username, real_address, started):
    import unified
    return unified.ovpn_session("ros6-" + username, real_address, started)


def hook():
    import unified
    env = os.environ
    username = env.get("username") or env.get("common_name", "")
    if not USER_RE.fullmatch(username):
        return 1
    is_disconnect = env.get("script_type") == "client-disconnect"
    remote = env.get("trusted_ip", "") + ":" + env.get("trusted_port", "")
    ident = session(username, remote, env.get("time_unix", ""))
    if not is_disconnect:
        with unified.connection() as conn:
            if identity(conn, username) is None:
                return 1
    # Disconnect always settles traffic, including for disabled accounts.
    up = int(env.get("bytes_received", "0"))
    down = int(env.get("bytes_sent", "0"))
    unified.credit(username, "openvpn", ident, up, down)
    return 0


def management(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(4)
        sock.connect(str(SOCKET))
        sock.recv(4096)
        sock.sendall((command + "\n").encode("ascii"))
        chunks = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
            joined = b"".join(chunks)
            if len(joined) > 1024 * 1024:
                raise ValueError("OpenVPN management response too large")
            if b"END\n" in joined or b"END\r\n" in joined or b"ERROR:" in joined:
                break
        return b"".join(chunks).decode("utf-8", errors="replace")


def collect():
    """Sample RouterOS TCP sessions into native x-ui client_traffics."""
    import unified
    if not installed():
        return
    for line in management("status 3").splitlines():
        parts = line.split("\t")
        if len(parts) < 10 or parts[0] != "CLIENT_LIST":
            continue
        username = parts[9] if USER_RE.fullmatch(parts[9]) else parts[1]
        if not USER_RE.fullmatch(username):
            continue
        received, sent = int(parts[5]), int(parts[6])
        session_id = session(username, parts[2], parts[8])
        unified.credit(username, "openvpn", session_id, received, sent)
        with unified.connection() as conn:
            active = identity(conn, username) is not None
        if not active:
            # The listener's management interface can drop an exhausted login.
            management("kill " + username)


def profiles(account, domain):
    """Return the CA and RouterOS 6 .rsc script as two downloadable files."""
    if not installed():
        return []
    username = account["username"]
    password = account["password"]
    if not USER_RE.fullmatch(username) or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", password):
        return []
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,255}", domain):
        return []
    ca = (BASE / "ca.crt").read_text(encoding="ascii")
    script = "\n".join((
        "# MikroTik RouterOS 6.48.6; upload masiha-ros6-ca.crt via WinBox Files first.",
        '/certificate import file-name=masiha-ros6-ca.crt passphrase=""',
        "# IMPORTANT: set add-default-route=yes only if you want ALL router traffic tunneled.",
        '/interface ovpn-client add name=masiha-ros6 connect-to="' + domain +
        '" port=' + str(PORT) + ' mode=ip user="' + username +
        '" password="' + password +
        '" certificate=none verify-server-certificate=yes tls-version=only-1.2' +
        ' auth=sha1 cipher=aes256 add-default-route=no disabled=no',
        "",
    ))
    return [
        {"protocol": "openvpn-routeros6-ca",
         "filename": "masiha-ros6-ca.crt", "content": ca},
        {"protocol": "openvpn-routeros6",
         "filename": "mikrotik-" + username + ".rsc", "content": script},
    ]


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "auth":
        raise SystemExit(auth(sys.argv[2]))
    if len(sys.argv) == 2 and sys.argv[1] == "hook":
        raise SystemExit(hook())
    raise SystemExit(2)


if __name__ == "__main__":
    main()
