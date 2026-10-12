#!/usr/bin/env python3
"""Additive performance comparison profiles for Masiha native x-ui.

Only creates new inbound rows, never directly writes customer credentials,
shares or balances. Note: the native x-ui reconciler may automatically link
existing clients to each new inbound after insertion; plan rollout accordingly.
SS2022 is omitted because malformed global per-user keys can crash Xray.
"""
import argparse
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path

PRESETS = (
    ("10-SS-AES128-GCM-TCP-UDP", "shadowsocks", 8452, "aes-128-gcm"),
    ("11-SS-AES256-GCM-TCP-UDP", "shadowsocks", 8453, "aes-256-gcm"),
    ("12-SS-XCHACHA20-TCP-UDP", "shadowsocks", 8454, "xchacha20-ietf-poly1305"),
    ("13-SS-CHACHA20-TCP-UDP", "shadowsocks", 8455, "chacha20-ietf-poly1305"),
    ("14-VLESS-GRPC-H2-TLS", "vless", 8456, "grpc"),
    ("15-VLESS-HTTPUPGRADE-TLS", "vless", 8457, "httpupgrade"),
)


def make_preset(domain, preset):
    remark, proto, port, kind = preset
    if proto == "shadowsocks":
        # Classic AEAD with independent per-user keys; unlike SS2022, these
        # algorithms accept the legacy arbitrary client passwords from x-ui.
        settings = {
            "method": kind,
            "password": secrets.token_urlsafe(32),
            "network": "tcp,udp",
            "clients": [],
        }
        stream = {"network": "tcp", "security": "none"}
    else:
        settings = {"clients": [], "decryption": "none", "encryption": "none"}
        stream = {
            "network": kind, "security": "tls",
            "tlsSettings": {
                "serverName": domain, "minVersion": "1.2", "maxVersion": "1.3",
                "alpn": ["h2"] if kind == "grpc" else ["http/1.1"],
                "certificates": [{
                    "certificateFile": "/etc/letsencrypt/live/" + domain + "/fullchain.pem",
                    "keyFile": "/etc/letsencrypt/live/" + domain + "/privkey.pem",
                    "usage": "encipherment",
                }],
            },
        }
        if kind == "grpc":
            stream["grpcSettings"] = {"serviceName": "g-" + secrets.token_hex(9), "multiMode": False}
        else:
            stream["httpupgradeSettings"] = {
                "path": "/upgrade-" + secrets.token_hex(9), "host": domain,
            }
    return {
        "remark": remark, "tag": "masiha-benchmark-" + str(port),
        "protocol": proto, "port": port, "listen": "", "enable": 1,
        "up": 0, "down": 0, "total": 0, "expiry_time": 0,
        "traffic_reset": "never", "traffic_reset_day": 1,
        "settings": json.dumps(settings, separators=(",", ":")),
        "stream_settings": json.dumps(stream, separators=(",", ":")),
        "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic"]}),
    }


def xray_test(binary, rows):
    conf = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": r["port"], "protocol": r["protocol"],
            "settings": json.loads(r["settings"]),
            "streamSettings": json.loads(r["stream_settings"]),
        } for r in rows],
        "outbounds": [{"protocol": "freedom"}],
    }
    with tempfile.TemporaryDirectory(prefix="masiha-benchmark-qa-") as directory:
        config = Path(directory) / "test.json"
        config.write_text(json.dumps(conf), encoding="utf-8")
        p = subprocess.run(
            [binary, "run", "-test", "-c", str(config)],
            capture_output=True, text=True, timeout=25,
        )
    if p.returncode:
        raise RuntimeError("Xray refused preset: " + (p.stderr + p.stdout)[-450:])


def extend(db_path, domain, xray_binary, check_listeners=True, check_tls=True):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", domain):
        raise ValueError("Invalid domain")
    if not Path(xray_binary).is_file():
        raise FileNotFoundError("Xray unavailable")
    if check_tls:
        for part in ("fullchain.pem", "privkey.pem"):
            if not (Path("/etc/letsencrypt/live") / domain / part).is_file():
                raise FileNotFoundError("TLS certificate unavailable: " + part)
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if not owner:
            raise RuntimeError("Native panel admin missing")
        old_rows = list(db.execute("SELECT port,tag FROM inbounds"))
        known_ports = {int(r[0]) for r in old_rows}
        known_tags = {str(r[1]) for r in old_rows}
        pending = []
        for preset in PRESETS:
            label, proto, port, mode = preset
            tag = "masiha-benchmark-" + str(port)
            if tag in known_tags:
                if port not in known_ports:
                    raise RuntimeError("Tag/port mismatch: " + tag)
                print("Existing preset preserved:", label)
                continue
            if port in known_ports:
                raise RuntimeError("Port already registered: " + str(port))
            if check_listeners:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.4)
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        raise RuntimeError("TCP port is already listening: " + str(port))
            record = make_preset(domain, preset)
            record["user_id"] = owner[0]
            pending.append(record)
        if pending:
            xray_test(xray_binary, pending)
            for row in pending:
                fields = list(row)
                db.execute(
                    "INSERT INTO inbounds (" + ",".join(fields) + ") VALUES (" +
                    ",".join("?" for _ in fields) + ")",
                    [row[field] for field in fields],
                )
        db.commit()
    print("Created", len(pending), "isolated benchmark inbounds.")
    print("Native x-ui may auto-link existing clients; verify mappings and regenerate the Xray config.")
    return len(pending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/etc/x-ui/x-ui.db")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--xray", default="/usr/local/x-ui/bin/xray-linux-amd64")
    args = parser.parse_args()
    extend(args.db, args.domain, args.xray)
