#!/usr/bin/env python3
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import time
import uuid
import copy
import re

import httpx

API = os.environ["MASIHA_API_URL"].rstrip("/")
NODE_ID = os.environ["MASIHA_NODE_ID"]
TOKEN = os.environ["MASIHA_NODE_TOKEN"]
INTERVAL = int(os.getenv("MASIHA_HEARTBEAT_INTERVAL", "20"))
PROTOCOLS = [x.strip() for x in os.getenv("MASIHA_PROTOCOLS", "xray,wireguard").split(",") if x.strip()]
STATE = pathlib.Path(os.getenv("MASIHA_STATE_DIR", "/var/lib/masiha-node-agent"))
PUBLIC_HOST = os.getenv("MASIHA_PUBLIC_HOST", "xu.chanelchat.ir")
STATE.mkdir(parents=True, exist_ok=True)
HEAD = {"X-Node-Token": TOKEN}


def run(args, *, input_text=None, env=None, cwd=None):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, env=env, cwd=cwd, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def load_state(service_id):
    path = STATE / f"{service_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_state(payload, config, metadata):
    path = STATE / f"{payload['service_id']}.json"
    data = {"payload": payload, "client_config": config, "metadata": metadata}
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def env_file(path):
    values = {}
    for raw in pathlib.Path(path).read_text().splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def revoke_xray(state):
    if not state:
        return
    client_id = state.get("metadata", {}).get("client_id")
    config_path = pathlib.Path("/usr/local/etc/xray/config.json")
    data = json.loads(config_path.read_text())
    changed = False
    for inbound in data.get("inbounds", []):
        clients = inbound.get("settings", {}).get("clients", [])
        kept = [item for item in clients if item.get("id") != client_id]
        if len(kept) != len(clients):
            inbound["settings"]["clients"] = kept
            changed = True
    if changed:
        config_path.write_text(json.dumps(data, indent=2))
        run(["xray", "run", "-test", "-config", str(config_path)])
        run(["systemctl", "restart", "xray"])


def provision_xray(payload, old):
    if old:
        revoke_xray(old)
    values = env_file("/etc/masiha-vpn-node/xray.env")
    client_id = str(uuid.uuid4())
    config_path = pathlib.Path("/usr/local/etc/xray/config.json")
    data = json.loads(config_path.read_text())
    target = None
    inbound_spec = payload.get("inbound") or {}
    wanted_tag = "masiha-" + str(inbound_spec.get("id", ""))[:8]
    for inbound in data.get("inbounds", []):
        if (inbound.get("tag") == wanted_tag or
            (inbound_spec and inbound.get("protocol") == "vless" and inbound.get("port") == inbound_spec.get("port")) or
            (not inbound_spec and inbound.get("protocol") == "vless")):
            target = inbound
            break
    if target is None:
        raise RuntimeError("VLESS inbound not found")
    target.setdefault("settings", {}).setdefault("clients", []).append({
        "id": client_id, "flow": "xtls-rprx-vision", "email": payload["external_id"]
    })
    config_path.write_text(json.dumps(data, indent=2))
    run(["xray", "run", "-test", "-config", str(config_path)])
    run(["systemctl", "restart", "xray"])
    port = str(inbound_spec.get("port") or values.get("XRAY_PORT", "8443"))
    public_key = values.get("XRAY_PUBLIC_KEY") or values.get("PUBLIC_KEY")
    short_id = values.get("XRAY_SHORT_ID") or values.get("SHORT_ID")
    server_name = (inbound_spec.get("settings") or {}).get("server_name") or values.get("XRAY_SERVER_NAME") or values.get("SERVER_NAME", "www.cloudflare.com")
    if not public_key or not short_id:
        raise RuntimeError("Xray public key or short id is missing")
    label = payload["external_id"]
    uri = (f"vless://{client_id}@{PUBLIC_HOST}:{port}?encryption=none&flow=xtls-rprx-vision"
           f"&security=reality&sni={server_name}&fp=chrome&pbk={public_key}&sid={short_id}&type=tcp#{label}")
    return uri, {"client_id": client_id}


def wg_server_public_key():
    pub = pathlib.Path("/etc/wireguard/server.pub")
    if pub.exists():
        return pub.read_text().strip()
    return run(["wg", "show", "wg0", "public-key"])


def next_wg_ip(service_id):
    used = set()
    for path in STATE.glob("*.json"):
        try:
            item = json.loads(path.read_text())
            ip = item.get("metadata", {}).get("client_ip")
            if ip:
                used.add(ip)
        except Exception:
            pass
    seed = int(service_id.replace("-", "")[:8], 16)
    for step in range(253):
        host = 2 + ((seed + step) % 253)
        ip = f"10.70.0.{host}"
        if ip not in used:
            return ip
    raise RuntimeError("WireGuard address pool is exhausted")


def rewrite_wg_peer(service_id, block=None):
    path = pathlib.Path("/etc/wireguard/wg0.conf")
    start = f"# MASIHA-BEGIN {service_id}"
    end = f"# MASIHA-END {service_id}"
    lines = path.read_text().splitlines()
    out, skipping = [], False
    for line in lines:
        if line.strip() == start:
            skipping = True
            continue
        if skipping and line.strip() == end:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    if block:
        out.extend(["", start, block, end])
    path.write_text("\n".join(out).rstrip() + "\n")
    os.chmod(path, 0o600)


def revoke_wireguard(state):
    if not state:
        return
    meta = state.get("metadata", {})
    public_key = meta.get("client_public_key")
    service_id = state.get("payload", {}).get("service_id")
    if public_key:
        subprocess.run(["wg", "set", "wg0", "peer", public_key, "remove"], check=False)
    if service_id:
        rewrite_wg_peer(service_id)


def provision_wireguard(payload, old):
    if old:
        revoke_wireguard(old)
    private_key = run(["wg", "genkey"])
    public_key = run(["wg", "pubkey"], input_text=private_key + "\n")
    client_ip = next_wg_ip(payload["service_id"])
    peer = f"[Peer]\nPublicKey = {public_key}\nAllowedIPs = {client_ip}/32"
    rewrite_wg_peer(payload["service_id"], peer)
    run(["wg", "set", "wg0", "peer", public_key, "allowed-ips", f"{client_ip}/32"])
    inbound_spec = payload.get("inbound") or {}
    endpoint_port = inbound_spec.get("port", 51820)
    public_host = inbound_spec.get("public_host") or PUBLIC_HOST
    config = ("[Interface]\n"
              f"PrivateKey = {private_key}\nAddress = {client_ip}/32\nDNS = 1.1.1.1, 1.0.0.1\n\n"
              "[Peer]\n"
              f"PublicKey = {wg_server_public_key()}\nEndpoint = {public_host}:{endpoint_port}\n"
              "AllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n")
    return config, {"client_public_key": public_key, "client_ip": client_ip}


def ovpn_name(payload):
    return payload["external_id"].replace("_", "-")


def revoke_openvpn(state):
    if not state:
        return
    name = state.get("metadata", {}).get("common_name")
    if not name:
        return
    root = pathlib.Path("/etc/openvpn/easy-rsa")
    cert = root / "pki" / "issued" / f"{name}.crt"
    if cert.exists():
        env = dict(os.environ, EASYRSA_BATCH="1")
        subprocess.run([str(root / "easyrsa"), "revoke", name], cwd=root, env=env, capture_output=True, text=True)
        run([str(root / "easyrsa"), "gen-crl"], env=env, cwd=root)
        shutil.copy2(root / "pki" / "crl.pem", "/etc/openvpn/server/crl.pem")


def provision_openvpn(payload, old):
    if old:
        revoke_openvpn(old)
    name = ovpn_name(payload)
    root = pathlib.Path("/etc/openvpn/easy-rsa")
    env = dict(os.environ, EASYRSA_BATCH="1")
    run([str(root / "easyrsa"), "build-client-full", name, "nopass"], env=env, cwd=root)
    pki = root / "pki"
    ca = (pki / "ca.crt").read_text().strip()
    cert = (pki / "issued" / f"{name}.crt").read_text().strip()
    key = (pki / "private" / f"{name}.key").read_text().strip()
    ta_path = pathlib.Path("/etc/openvpn/server/ta.key")
    if not ta_path.exists():
        ta_path = pathlib.Path("/etc/openvpn/ta.key")
    ta = ta_path.read_text().strip()
    inbound_spec = payload.get("inbound") or {}
    settings = inbound_spec.get("settings") or {}
    public_host = inbound_spec.get("public_host") or PUBLIC_HOST
    port = inbound_spec.get("port", 1194)
    transport = settings.get("transport", "udp")
    device = settings.get("device", "tun")
    config = (f"client\ndev {device}\nproto {transport}\nremote {public_host} {port}\nresolv-retry infinite\n"
              "nobind\npersist-key\npersist-tun\nremote-cert-tls server\nverb 3\nkey-direction 1\n"
              f"<ca>\n{ca}\n</ca>\n<cert>\n{cert}\n</cert>\n<key>\n{key}\n</key>\n<tls-auth>\n{ta}\n</tls-auth>\n")
    return config, {"common_name": name}


def revoke_openconnect(state):
    if not state:
        return
    username = state.get("metadata", {}).get("username")
    if username:
        subprocess.run(["ocpasswd", "-c", "/etc/ocserv/ocpasswd", "-d", username], check=False, capture_output=True)


def provision_openconnect(payload, old):
    if old:
        revoke_openconnect(old)
    username = payload["external_id"].replace("_", "-")
    password = secrets.token_urlsafe(18)
    run(["ocpasswd", "-c", "/etc/ocserv/ocpasswd", username], input_text=f"{password}\n{password}\n")
    inbound_spec = payload.get("inbound") or {}
    public_host = inbound_spec.get("public_host") or PUBLIC_HOST
    port = inbound_spec.get("port", 4443)
    config = ("Masiha VPN - OpenConnect\n"
              f"Server: https://{public_host}:{port}\nUsername: {username}\nPassword: {password}\n"
              "Client: OpenConnect / Cisco AnyConnect compatible\n")
    return config, {"username": username}


PROVISIONERS = {
    "xray": provision_xray,
    "wireguard": provision_wireguard,
    "openvpn": provision_openvpn,
    "openconnect": provision_openconnect,
}
REVOKERS = {
    "xray": revoke_xray,
    "wireguard": revoke_wireguard,
    "openvpn": revoke_openvpn,
    "openconnect": revoke_openconnect,
}


def replace_setting(path, key, value):
    text = path.read_text()
    pattern = rf"(?m)^\s*{re.escape(key)}\s+.*$"
    line = f"{key} {value}"
    text, count = re.subn(pattern, line, text, count=1)
    if not count:
        text += "\n" + line + "\n"
    path.write_text(text)


def apply_inbound(payload, deleting=False):
    inbound_id = payload["inbound_id"]
    protocol = payload["protocol"]
    registry = STATE / f"inbound-{inbound_id}.json"
    if deleting:
        if protocol == "xray":
            config_path = pathlib.Path("/usr/local/etc/xray/config.json")
            data = json.loads(config_path.read_text())
            tag = "masiha-" + inbound_id[:8]
            original = data.get("inbounds", [])
            data["inbounds"] = [item for item in original if item.get("tag") != tag]
            if len(data["inbounds"]) != len(original):
                config_path.write_text(json.dumps(data, indent=2))
                run(["xray", "run", "-test", "-config", str(config_path)])
                run(["systemctl", "restart", "xray"])
        registry.unlink(missing_ok=True)
        return
    if protocol == "xray":
        settings = payload.get("settings") or {}
        if settings.get("variant", "vless") != "vless":
            raise RuntimeError("This node release currently provisions Xray VLESS inbounds; other Xray variants are reserved for the next adapter")
        config_path = pathlib.Path("/usr/local/etc/xray/config.json")
        data = json.loads(config_path.read_text())
        tag = "masiha-" + inbound_id[:8]
        target = next((x for x in data.get("inbounds", []) if x.get("tag") == tag), None)
        if target is None:
            target = next((x for x in data.get("inbounds", []) if x.get("protocol") == "vless" and x.get("port") == int(payload["port"])), None)
            if target is not None: target["tag"] = tag
        if target is None:
            template = next((x for x in data.get("inbounds", []) if x.get("protocol") == "vless"), None)
            if template is None: raise RuntimeError("VLESS template inbound not found")
            target = copy.deepcopy(template)
            target["tag"] = tag
            target.setdefault("settings", {})["clients"] = []
            data.setdefault("inbounds", []).append(target)
        target["listen"] = payload.get("listen", "0.0.0.0")
        target["port"] = int(payload["port"])
        target.setdefault("streamSettings", {})["network"] = settings.get("transport", "tcp")
        target["streamSettings"]["security"] = settings.get("security", "reality")
        config_path.write_text(json.dumps(data, indent=2))
        run(["xray", "run", "-test", "-config", str(config_path)])
        run(["systemctl", "restart", "xray"])
    elif protocol == "wireguard":
        path = pathlib.Path("/etc/wireguard/wg0.conf")
        text = path.read_text()
        text, count = re.subn(r"(?m)^ListenPort\s*=\s*\d+\s*$", f"ListenPort = {int(payload['port'])}", text, count=1)
        if not count: text = text.replace("[Interface]", f"[Interface]\nListenPort = {int(payload['port'])}", 1)
        path.write_text(text); run(["systemctl", "restart", "wg-quick@wg0"])
    elif protocol == "openvpn":
        path = pathlib.Path("/etc/openvpn/server/server.conf")
        replace_setting(path, "port", int(payload["port"]))
        replace_setting(path, "proto", (payload.get("settings") or {}).get("transport", "udp"))
        run(["systemctl", "restart", "openvpn-server@server"])
        run(["systemctl", "is-active", "openvpn-server@server"])
    elif protocol == "openconnect":
        path = pathlib.Path("/etc/ocserv/ocserv.conf")
        replace_setting(path, "tcp-port", int(payload["port"]))
        replace_setting(path, "udp-port", int(payload["port"]))
        run(["ocserv", "-t", "-c", str(path)])
        run(["systemctl", "restart", "ocserv"])
    registry.write_text(json.dumps(payload, indent=2)); os.chmod(registry, 0o600)


def execute(job):
    payload = job["payload"]
    kind = job["type"]
    if kind in ("create_inbound", "update_inbound"):
        apply_inbound(payload)
        return {"ok": True}
    if kind == "delete_inbound":
        apply_inbound(payload, deleting=True)
        return {"ok": True}
    service_id = payload["service_id"]
    protocol = payload["protocol"]
    old = load_state(service_id)
    if kind in ("provision", "update"):
        if protocol not in PROVISIONERS:
            raise RuntimeError(f"unsupported protocol: {protocol}")
        config, metadata = PROVISIONERS[protocol](payload, old)
        save_state(payload, config, metadata)
        return {"ok": True, "client_config": config}
    if kind == "revoke":
        REVOKERS.get(protocol, lambda state: None)(old)
        (STATE / f"{service_id}.json").unlink(missing_ok=True)
        return {"ok": True}
    raise RuntimeError(f"unsupported job type: {kind}")


with httpx.Client(timeout=30) as client:
    next_heartbeat = 0
    while True:
        try:
            now = time.monotonic()
            if now >= next_heartbeat:
                client.post(f"{API}/api/v1/agent/{NODE_ID}/heartbeat", headers=HEAD,
                            json={"agent_version": "0.3.0", "protocols": PROTOCOLS}).raise_for_status()
                next_heartbeat = now + INTERVAL
            processed = False
            while True:
                job = client.get(f"{API}/api/v1/agent/{NODE_ID}/jobs/next", headers=HEAD).json().get("job")
                if not job:
                    break
                processed = True
                try:
                    result = execute(job)
                except Exception as exc:
                    result = {"ok": False, "details": {"error": str(exc)}}
                client.post(f"{API}/api/v1/agent/{NODE_ID}/jobs/{job['id']}/result",
                            headers=HEAD, json=result).raise_for_status()
            if not processed:
                time.sleep(2)
        except Exception as exc:
            print(json.dumps({"agent": "error", "message": str(exc)}), flush=True)
            time.sleep(5)
