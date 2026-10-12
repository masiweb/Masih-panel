#!/usr/bin/env python3
"""Opt-in additive Xray inbound presets. Never edits/deletes existing inbounds.

Can run on a new server during finalization or on an existing installation
while x-ui is stopped. Each transport is tested by the installed Xray binary
before any database write; transactions roll back on any failure.
"""
import argparse
import base64
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path

PRESETS = (
    # Do not provision SS2022 by default: native global client passwords
    # may decode to 12/24 instead of the mandatory 16-byte PSK. This caused
    # Xray's entire startup to fail on exon. SS2022 requires an independent
    # validated per-inbound client-key implementation before reenabling.
    ("08-VLESS-XHTTP-STREAMUP-TLS-H2", "vless", 8450, "xhttp", "tls", "stream-up"),
    ("09-VLESS-XHTTP-PACKETUP-TLS-H2", "vless", 8451, "xhttp", "tls", "packet-up"),
)


def make_preset(domain, preset):
    title, protocol, port, transport, security, mode = preset
    tag = "masiha-extra-" + str(port)
    if protocol == "shadowsocks":
        settings = {
            "method": "2022-blake3-aes-128-gcm",
            "password": base64.b64encode(secrets.token_bytes(16)).decode("ascii"),
            "network": "tcp,udp",
            "clients": [],
        }
        stream = {"network": "tcp", "security": "none"}
    else:
        settings = {"clients": [], "decryption": "none", "encryption": "none"}
        stream = {
            "network": "xhttp", "security": "tls",
            "xhttpSettings": {
                "path": "/xhttp-" + secrets.token_hex(12),
                "host": domain, "mode": mode,
            },
            "tlsSettings": {
                "minVersion": "1.2", "maxVersion": "1.3",
                "serverName": domain, "alpn": ["h2", "http/1.1"],
                "certificates": [{
                    "certificateFile": "/etc/letsencrypt/live/" + domain + "/fullchain.pem",
                    "keyFile": "/etc/letsencrypt/live/" + domain + "/privkey.pem",
                    "usage": "encipherment"
                }]
            }
        }
    return {
        "remark": title, "tag": tag, "protocol": protocol,
        "port": port, "listen": "", "enable": 1,
        "up": 0, "down": 0, "total": 0, "expiry_time": 0,
        "traffic_reset": "never", "traffic_reset_day": 1,
        "settings": json.dumps(settings, separators=(",", ":")),
        "stream_settings": json.dumps(stream, separators=(",", ":")),
        "sniffing": json.dumps({"enabled":True,"destOverride":["http","tls","quic"]}),
    }


def validate_xray(binary, row):
    config = {
        "log": {"loglevel":"warning"},
        "inbounds": [{
            "port":row["port"], "listen":"127.0.0.1",
            "protocol":row["protocol"],"settings":json.loads(row["settings"]),
            "streamSettings":json.loads(row["stream_settings"])
        }],
        "outbounds":[{"protocol":"freedom"}],
    }
    with tempfile.TemporaryDirectory(prefix="masiha-inbound-test-") as directory:
        p=Path(directory)/"config.json"
        p.write_text(json.dumps(config))
        run=subprocess.run([binary,"run","-test","-c",str(p)],
                           capture_output=True,text=True,timeout=20)
        if run.returncode:
            raise RuntimeError("Xray config validation failed for port "+str(row["port"])+
                               ": "+(run.stderr+run.stdout)[-300:])
        if "Configuration OK" not in run.stdout+run.stderr:
            raise RuntimeError("Xray did not confirm configuration validity")


def extend(db_path,domain,binary):
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}",domain):
        raise ValueError("Invalid domain")
    if not Path(binary).is_file():raise FileNotFoundError("Xray executable unavailable")
    for item in ("fullchain.pem","privkey.pem"):
        if not (Path("/etc/letsencrypt/live")/domain/item).is_file():
            raise FileNotFoundError("TLS certificate not found: "+item)
    with sqlite3.connect(db_path,timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        owner=db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if not owner:raise RuntimeError("x-ui admin user missing")
        existing=list(db.execute("SELECT port,tag FROM inbounds"))
        taken_ports={p for p,_ in existing}
        taken_tags={t for _,t in existing}
        pending=[]
        for preset in PRESETS:
            title,proto,port,transport,security,mode=preset
            tag="masiha-extra-"+str(port)
            if tag in taken_tags:
                if port not in taken_ports:raise RuntimeError("Inbound tag conflict: "+tag)
                print("Existing preset retained: "+tag)
                continue
            if port in taken_ports:raise RuntimeError("Port already used by another inbound: "+str(port))
            # This script is intended to be executed with x-ui stopped; check
            # for other local listeners to avoid collision on restart.
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1",port))==0:
                    raise RuntimeError("Port in use by another service: "+str(port))
            row=make_preset(domain,preset)
            validate_xray(binary,row)
            row["user_id"]=owner[0]
            pending.append(row)
        for row in pending:
            keys=list(row)
            db.execute("INSERT INTO inbounds("+",".join(keys)+") VALUES("+
                       ",".join("?" for _ in keys)+")",[row[k] for k in keys])
        db.commit()
    print("Additional tested Xray inbound presets inserted:",len(pending))
    print("SS2022 is deliberately excluded until per-client PSK validation is implemented.")
    return len(pending)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",default="/etc/x-ui/x-ui.db")
    parser.add_argument("--domain",required=True)
    parser.add_argument("--xray",default="/usr/local/x-ui/bin/xray-linux-amd64")
    a=parser.parse_args()
    extend(a.db,a.domain,a.xray)
