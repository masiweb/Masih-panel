#!/usr/bin/env python3
"""IPsec and L2TP accounting using per-CHILD_SA counters and PPP interfaces.

Session identity includes boot-ID and an IKE or PPP instance so reconnects
cannot double-count; samples are cumulative and idempotent in unified.credit.
"""
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import unified

SESSIONS=Path("/run/masiha-ipsec/ppp")
IKE_HEAD=re.compile(r"^\S+:\s+#(\d+),\s+ESTABLISHED.*IKEv2",re.I)
CHILD=re.compile(r"^\s{2}\S+:\s+#(\d+),\s+reqid\b",re.I)
IDENTITY=re.compile(r"\bEAP:\s+'([^']{1,80})'")
BYTES_IN=re.compile(r"^\s+in\s+\S+,\s+(\d+)\s+bytes")
BYTES_OUT=re.compile(r"^\s+out\s+\S+,\s+(\d+)\s+bytes")
IFACE=re.compile(r"^ppp[0-9]{1,4}$")

def parse_ike_sas(raw):
    """Accept only EAP identities with both directional kernel counters."""
    current=None
    child=None
    output=[]
    def emit():
        if current and current["username"] and child and child["up"] is not None and child["down"] is not None:
            output.append({**current,**child})
    for line in raw.splitlines():
        header=IKE_HEAD.match(line)
        if header:
            emit()
            current={"username":"","ike_id":header.group(1)}
            child=None
            continue
        if current is None:continue
        m=IDENTITY.search(line)
        if m:current["username"]=m.group(1)
        m=CHILD.match(line)
        if m:
            emit()
            child={"child_id":m.group(1),"up":None,"down":None}
            continue
        if child:
            i=BYTES_IN.match(line)
            if i:child["up"]=int(i.group(1))
            o=BYTES_OUT.match(line)
            if o:child["down"]=int(o.group(1))
    emit()
    return output

def _one_account(name):
    with unified.connection() as c:
        a=unified.account_for_username(c,name)
        if not a:return True  # Standalone accounts have their own expiry policy.
        rows=[r for r in unified.clients(c) if r["id"]==a["client_id"] and r["created_at"]==a["generation"]]
        return bool(rows and unified.permitted(rows[0]))

def sample_ike(raw=None):
    live = raw is None
    if raw is None:
        p=subprocess.run(["/usr/sbin/swanctl","--list-sas"],capture_output=True,text=True,timeout=14)
        if p.returncode:raise RuntimeError("StrongSwan VICI accounting unavailable")
        raw=p.stdout
    total=0
    for entry in parse_ike_sas(raw):
        username=entry["username"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}",username):
            continue
        session=unified.boot()+":ike:"+entry["ike_id"]+":"+entry["child_id"]
        unified.credit(username,"ikev2",session,entry["up"],entry["down"])
        total+=1
        if not _one_account(username) and live:
            # Stop an exhausted EAP session by its numeric IKE identifier.
            subprocess.run(["/usr/sbin/swanctl","--terminate","--ike-id",entry["ike_id"]],
                           timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return total

def session_path(iface):
    if not IFACE.fullmatch(iface):raise ValueError("Invalid PPP interface")
    return SESSIONS/(iface+".json")

def ppp_start():
    iface=os.environ.get("IFNAME") or (sys.argv[2] if len(sys.argv)>2 else "")
    name=os.environ.get("PEERNAME","")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}",name):return
    path=session_path(iface)
    if not _one_account(name):
        subprocess.run(["/usr/sbin/ip","link","set",iface,"down"],timeout=5)
        return
    SESSIONS.mkdir(parents=True,exist_ok=True,mode=0o700)
    payload={"name":name,"session":unified.boot()+":"+secrets.token_hex(12)}
    path.write_text(json.dumps(payload))
    path.chmod(0o600)

def ppp_end():
    iface=os.environ.get("IFNAME") or (sys.argv[2] if len(sys.argv)>2 else "")
    path=session_path(iface)
    try:
        payload=json.loads(path.read_text())
        # pppd exports final bytes after disconnect; end hook must be idempotent.
        sent=int(os.environ.get("BYTES_SENT","0"))
        received=int(os.environ.get("BYTES_RCVD","0"))
        unified.credit(payload["name"],"l2tp",payload["session"],max(0,sent),max(0,received))
    finally:path.unlink(missing_ok=True)

def sample_ppp():
    if not SESSIONS.is_dir():return 0
    total=0
    for path in SESSIONS.glob("ppp*.json"):
        iface=path.stem
        if not IFACE.fullmatch(iface):continue
        try:
            payload=json.loads(path.read_text())
            root=Path("/sys/class/net")/iface/"statistics"
            in_bytes=int((root/"rx_bytes").read_text())
            out_bytes=int((root/"tx_bytes").read_text())
            unified.credit(payload["name"],"l2tp",payload["session"],out_bytes,in_bytes)
            if not _one_account(payload["name"]):
                subprocess.run(["/usr/sbin/ip","link","set",iface,"down"],timeout=5)
            total+=1
        except (ValueError,KeyError,OSError):continue
    return total

def main():
    action=sys.argv[1] if len(sys.argv)>1 else "collect"
    if action=="ppp-up":ppp_start()
    elif action=="ppp-down":ppp_end()
    elif action=="collect":
        import ipsec_backend
        if not ipsec_backend.ACTIVE.exists():return
        sample_ppp()
        if ipsec_backend.configured("ikev2"):sample_ike()
    else:raise ValueError("Invalid action")
if __name__=="__main__":main()