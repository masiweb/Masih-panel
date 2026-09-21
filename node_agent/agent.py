#!/usr/bin/env python3
import json, os, pathlib, subprocess, time
import httpx

API=os.environ["MASIHA_API_URL"].rstrip("/")
NODE_ID=os.environ["MASIHA_NODE_ID"]
TOKEN=os.environ["MASIHA_NODE_TOKEN"]
INTERVAL=int(os.getenv("MASIHA_HEARTBEAT_INTERVAL","20"))
PROTOCOLS=[x.strip() for x in os.getenv("MASIHA_PROTOCOLS","xray,wireguard").split(",") if x.strip()]
STATE=pathlib.Path(os.getenv("MASIHA_STATE_DIR","/var/lib/masiha-node-agent"))
STATE.mkdir(parents=True,exist_ok=True)
HEAD={"X-Node-Token":TOKEN}

def execute(job):
    payload=job["payload"]; service_id=payload["service_id"]; kind=job["type"]
    path=STATE/f"{service_id}.json"
    if kind in ("provision","update"):
        path.write_text(json.dumps(payload,indent=2))
        return {"ok":True,"client_config":json.dumps({"service_id":service_id,"protocol":payload["protocol"],"node":NODE_ID})}
    if kind=="revoke":
        path.unlink(missing_ok=True)
        return {"ok":True}
    return {"ok":False,"details":{"error":"unsupported job type"}}

with httpx.Client(timeout=20) as client:
    while True:
        try:
            client.post(f"{API}/api/v1/agent/{NODE_ID}/heartbeat",headers=HEAD,json={"agent_version":"0.2.0","protocols":PROTOCOLS}).raise_for_status()
            job=client.get(f"{API}/api/v1/agent/{NODE_ID}/jobs/next",headers=HEAD).json().get("job")
            if job:
                result=execute(job)
                client.post(f"{API}/api/v1/agent/{NODE_ID}/jobs/{job['id']}/result",headers=HEAD,json=result).raise_for_status()
        except Exception as exc:
            print(json.dumps({"agent":"error","message":str(exc)}),flush=True)
        time.sleep(INTERVAL)
