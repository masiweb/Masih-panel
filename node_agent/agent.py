#!/usr/bin/env python3
import json, os, subprocess, time
import httpx

API_URL=os.environ["MASIHA_API_URL"].rstrip("/")
NODE_ID=os.environ["MASIHA_NODE_ID"]
NODE_TOKEN=os.environ["MASIHA_NODE_TOKEN"]
INTERVAL=int(os.getenv("MASIHA_HEARTBEAT_INTERVAL","30"))
PROTOCOLS=[x.strip() for x in os.getenv("MASIHA_PROTOCOLS","xray,wireguard,openvpn,openconnect").split(",") if x.strip()]

def ensure_services():
    for name in PROTOCOLS:
        service={"xray":"xray","wireguard":"wg-quick@wg0","openvpn":"openvpn-server@server","openconnect":"ocserv"}.get(name)
        if service: subprocess.run(["systemctl","start",service],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

while True:
    try:
        ensure_services()
        r=httpx.post(f"{API_URL}/api/v1/nodes/{NODE_ID}/heartbeat",headers={"X-Node-Token":NODE_TOKEN},json={"agent_version":"0.1.0","protocols":PROTOCOLS},timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(json.dumps({"heartbeat":"failed","error":str(exc)}),flush=True)
    time.sleep(INTERVAL)
