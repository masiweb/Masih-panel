#!/usr/bin/env python3
"""Create five safe Xray inbounds on *new* Masiha installs only.

Idempotent: if the database already has any inbound, it makes no changes.
No customers, credentials or private keys are stored in Git.
"""
import argparse
import json
import re
import secrets
import socket
import sqlite3
import subprocess
from pathlib import Path

INBOUNDS = (
    ("01-VLESS-REALITY-TCP", "vless", 8443, "tcp", "reality"),
    ("02-VLESS-TCP-TLS", "vless", 8444, "tcp", "tls"),
    ("03-VMESS-WEBSOCKET-TLS", "vmess", 8445, "ws", "tls"),
    ("04-TROJAN-TCP-TLS", "trojan", 8446, "tcp", "tls"),
    ("05-VLESS-WEBSOCKET-TLS", "vless", 8447, "ws", "tls"),
)


def reality_keypair(xray_binary):
    proc = subprocess.run([xray_binary, "x25519"], check=True, capture_output=True, text=True, timeout=12)
    private = re.search(r"^PrivateKey:\s*([^\s]+)\s*$", proc.stdout, re.M)
    public = re.search(r"^(?:Password \(PublicKey\)|PublicKey|Public key):\s*([^\s]+)\s*$", proc.stdout, re.M)
    if not private or not public:
        raise RuntimeError("Xray x25519 returned an unrecognized key-pair format")
    return private.group(1), public.group(1)


def tls(domain):
    root = "/etc/letsencrypt/live/" + domain
    return {
        "serverName": domain,
        "minVersion": "1.2",
        "maxVersion": "1.3",
        "rejectUnknownSni": True,
        "certificates": [{
            "certificateFile": root + "/fullchain.pem",
            "keyFile": root + "/privkey.pem",
            "usage": "encipherment"
        }],
        "alpn": ["http/1.1"],
    }


def make_inbound(domain, tag, proto, port, network, security, private=None, public=None):
    if proto == "vless":
        settings = {"clients": [], "decryption": "none", "encryption": "none", "fallbacks": []}
    else:
        settings = {"clients": []}
    stream = {"network": network, "security": security}
    if network == "ws":
        stream["wsSettings"] = {"path": "/ws-" + secrets.token_hex(8), "host": domain}
    else:
        stream["tcpSettings"] = {"acceptProxyProtocol": False, "header": {"type": "none"}}
    if security == "tls":
        stream["tlsSettings"] = tls(domain)
    else:
        stream["realitySettings"] = {
            "show": False, "xver": 0, "target": "www.cloudflare.com:443",
            "serverNames": ["www.cloudflare.com"], "privateKey": private,
            "minClientVer": "", "maxClientVer": "", "maxTimediff": 0,
            "shortIds": [secrets.token_hex(8)], "mldsa65Seed": "",
            "settings": {"publicKey": public, "fingerprint": "chrome",
                         "serverName": "www.cloudflare.com", "spiderX": "/"},
        }
    sniffing = {"enabled": True, "destOverride": ["http", "tls", "quic"]}
    return {
        "remark": tag, "tag": "in-" + str(port) + "-" + network,
        "protocol": proto, "port": port, "listen": "", "enable": 1,
        "up": 0, "down": 0, "total": 0, "expiry_time": 0,
        "traffic_reset": "never", "traffic_reset_day": 1,
        "settings": json.dumps(settings, separators=(",", ":")),
        "stream_settings": json.dumps(stream, separators=(",", ":")),
        "sniffing": json.dumps(sniffing, separators=(",", ":")),
    }


def seed(db_path, domain, xray_binary, require_tls=True):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", domain):
        raise ValueError("Invalid domain")
    if require_tls:
        for filename in ("fullchain.pem", "privkey.pem"):
            if not (Path("/etc/letsencrypt/live") / domain / filename).is_file():
                raise FileNotFoundError("Expected Let's Encrypt certificate/key missing: " + filename)
    db = sqlite3.connect(db_path, timeout=30)
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0]
        if existing:
            print("Existing inbounds found; keeping all existing configuration unchanged.")
            db.rollback()
            return 0
        user = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if not user:
            raise RuntimeError("No native panel user found. Initialize the panel first.")
        cols = {row[1] for row in db.execute("PRAGMA table_info(inbounds)")}
        required = {"user_id", "port", "protocol", "settings", "stream_settings", "sniffing", "tag", "remark"}
        if not required.issubset(cols):
            raise RuntimeError("Unknown inbounds database schema; refusing to create presets")
        used = {x[0] for x in db.execute("SELECT port FROM inbounds")}
        # Fail rather than silently generate a profile with an occupied local port.
        for _, _, port, _, _ in INBOUNDS:
            if port in used:
                raise RuntimeError("Inbound port already assigned: " + str(port))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Port already listening: " + str(port))
        private, public = reality_keypair(xray_binary)
        for tag, proto, port, network, security in INBOUNDS:
            data = make_inbound(domain, tag, proto, port, network, security, private, public)
            data["user_id"] = user[0]
            keys = list(data)
            db.execute(
                "INSERT INTO inbounds (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")",
                [data[k] for k in keys]
            )
        db.commit()
        print("Created 5 empty default inbounds: VLESS Reality, VLESS TCP TLS, "
              "VMess WS TLS, Trojan TCP TLS, VLESS WS TLS.")
        print("Xray ports: 8443,8444,8445,8446,8447 (TCP).")
        return 5
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/etc/x-ui/x-ui.db")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--xray", default="/usr/local/x-ui/bin/xray-linux-amd64")
    parser.add_argument("--no-tls-check", action="store_true",
                        help="For isolated test databases only; never for real installation.")
    args = parser.parse_args()
    seed(args.db, args.domain, args.xray, require_tls=not args.no_tls_check)
