#!/usr/bin/python3
"""Local protocol adapters sharing the native 3x-ui identity, quota and expiry.
Usage is credited atomically to client_traffics with a durable cumulative-session
ledger in the SAME SQLite transaction. Duplicate and out-of-order samples are safe.
"""
import contextlib, hashlib, json, os, secrets, sqlite3, sys, time
from pathlib import Path
import manager
DB=os.environ.get("MASIHA_PANEL_DB","/etc/x-ui/x-ui.db")
PROTOCOLS=("openvpn","openconnect","ikev2","l2tp")
def active_protocols():
 import ipsec_backend
 return tuple(p for p in PROTOCOLS if p in ("openvpn","openconnect") or ipsec_backend.configured(p))

def connection():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row
 return c
def initialize():
 with connection() as c:
  c.executescript("""
  CREATE TABLE IF NOT EXISTS masiha_accounts(client_id INTEGER PRIMARY KEY,generation INTEGER NOT NULL,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,ready INTEGER DEFAULT 0,error TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS masiha_sessions(session TEXT PRIMARY KEY,client_id INTEGER NOT NULL,generation INTEGER NOT NULL,protocol TEXT NOT NULL,up INTEGER NOT NULL,down INTEGER NOT NULL,updated INTEGER NOT NULL);
  """)
def clients(c):
 return c.execute("""SELECT c.*, t.up AS traffic_up,t.down AS traffic_down,t.total AS traffic_total,
 t.expiry_time AS traffic_expiry,t.enable AS traffic_enable
 FROM clients c LEFT JOIN client_traffics t ON t.email=c.email""").fetchall()
def expiry(r):
 return int(r["traffic_expiry"] if r["traffic_expiry"] is not None else r["expiry_time"])
def permitted(r):
 deadline=expiry(r)
 return bool(r["enable"] and r["traffic_enable"] is not None and r["traffic_enable"]
  and (deadline<=0 or deadline>int(time.time()*1000))
  and (not r["total_gb"] or (r["traffic_up"] or 0)+(r["traffic_down"] or 0)<r["total_gb"]))
def account_for_username(c,name):
 return c.execute("SELECT * FROM masiha_accounts WHERE username=?",(name,)).fetchone()
def credit(name,protocol,session,up,down):
 if protocol not in PROTOCOLS or min(up,down)<0 or max(up,down)>2**60:raise ValueError("Invalid counters")
 key=hashlib.sha256((protocol+":"+session).encode()).hexdigest()
 with connection() as c:
  c.execute("BEGIN IMMEDIATE")
  a=account_for_username(c,name)
  if not a:return
  r=c.execute("SELECT * FROM clients WHERE id=? AND created_at=?",(a["client_id"],a["generation"])).fetchone()
  if not r:return
  old=c.execute("SELECT * FROM masiha_sessions WHERE session=?",(key,)).fetchone()
  if old and (old["client_id"]!=r["id"] or old["generation"]!=r["created_at"]):raise ValueError("Session identity mismatch")
  prevup,prevdown=(old["up"],old["down"]) if old else (0,0)
  up,down=max(up,prevup),max(down,prevdown)
  updated=c.execute("UPDATE client_traffics SET up=up+?,down=down+?,last_online=MAX(last_online,?) WHERE email=?",
   (up-prevup,down-prevdown,int(time.time()*1000),r["email"]))
  if updated.rowcount!=1:raise ValueError("Missing native traffic row")
  c.execute("INSERT INTO masiha_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET up=excluded.up,down=excluded.down,updated=excluded.updated",
   (key,r["id"],r["created_at"],protocol,up,down,int(time.time())))
def boot():
 return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
def ovpn_session(cn,address,started):
 return boot()+":"+cn+":"+address+":"+str(started)
def oc_session(ident):
 return boot()+":"+str(ident)
def collect():
 with manager.db() as m:
  names={r["cert"]:r["username"] for r in m.execute("SELECT * FROM users WHERE protocol='openvpn'")}
 errors=[]
 try:
  for line in manager.management("status 3").splitlines():
   a=line.split("\t")
   if a[0]=="CLIENT_LIST" and len(a)>8 and a[1] in names:
    credit(names[a[1]],"openvpn",ovpn_session(a[1],a[2],a[8]),int(a[5]),int(a[6]))
 except Exception as e:errors.append("openvpn: "+str(e))
 try:
  entries=json.loads(manager.run(["occtl","--json","show","users"]))
  if not isinstance(entries,list):raise ValueError("Unexpected occtl schema")
  for item in entries:
   name=item.get("Username","")
   # show users omits byte counters on some releases; show id includes raw RX/TX.
   detail=json.loads(manager.run(["occtl","--json","show","id",str(int(item["ID"]))]))
   if isinstance(detail,dict):
    detail=[detail]
   if not isinstance(detail,list):
    raise ValueError("Unexpected occtl detail schema")
   for d in detail:
    if not isinstance(d,dict):
     continue
    # occtl releases differ: some show-id records have no traffic counters.
    # Disconnect hooks still account for these sessions. Missing values are
    # unknown, not zero; never invent usage or abort all reconciliation.
    if d.get("RX") is None or d.get("TX") is None:
     continue
    credit(name,"openconnect",oc_session(d.get("ID",item["ID"])),int(d["RX"]),int(d["TX"]))
 except Exception as e:errors.append("openconnect: "+str(e))
 if errors:raise RuntimeError("; ".join(errors))
def reconcile():
 initialize()
 # Sampling precedes lifecycle decisions, so traffic on either VPN can exhaust
 # the SAME quota used by Xray and shown in the native subscription page.
 collect()
 # Official SANAei updates replace the old custom Go API endpoint.
 # Client traffic has already been committed to the native x-ui SQLite DB
 # by collect(). The official x-ui traffic job continuously enforces shared
 # quota/expiry from those same client_traffics counters. Do not call the
 # now-absent custom /masiha-reconcile endpoint.
 # Read this DB below to reconcile OpenVPN/OpenConnect credentials directly.
 with connection() as c:
  rows=[dict(r) for r in clients(c)]
  accounts={a["client_id"]:dict(a) for a in c.execute("SELECT * FROM masiha_accounts")}
 ids={r["id"] for r in rows}
 for ident,a in list(accounts.items()):
  if ident not in ids or next(r["created_at"] for r in rows if r["id"]==ident)!=a["generation"]:
   for proto in active_protocols():
    with manager.db() as m:exists=m.execute("SELECT 1 FROM users WHERE protocol=? AND username=?",(proto,a["username"])).fetchone()
    if exists:manager.handle({"action":"delete","protocol":proto,"username":a["username"]})
   with connection() as c:c.execute("DELETE FROM masiha_accounts WHERE client_id=?",(ident,))
   accounts.pop(ident,None)
 for r in rows:
  a=accounts.get(r["id"])
  if not a:
   a={"client_id":r["id"],"generation":r["created_at"],"username":"mc-"+secrets.token_hex(12),"password":secrets.token_urlsafe(24)}
   with connection() as c:c.execute("INSERT INTO masiha_accounts(client_id,generation,username,password) VALUES(?,?,?,?)",(a["client_id"],a["generation"],a["username"],a["password"]))
  try:
   enabled=permitted(r);deadline=max(0,expiry(r)//1000)
   for proto in active_protocols():
    with manager.db() as m:existing=m.execute("SELECT * FROM users WHERE protocol=? AND username=?",(proto,a["username"])).fetchone()
    if existing and bool(existing["enabled"])==enabled and existing["expires"]==deadline:continue
    req={"protocol":proto,"action":"update" if existing else "create","username":a["username"],"enabled":enabled,"expires":deadline}
    if proto in ("openconnect","ikev2","l2tp") and not existing:
     req["password"]=a["password"]
    manager.handle(req)
   with connection() as c:c.execute("UPDATE masiha_accounts SET ready=1,error='' WHERE client_id=?",(r["id"],))
  except Exception:
   with connection() as c:c.execute("UPDATE masiha_accounts SET ready=0,error='Provisioning failed; inspect extension logs' WHERE client_id=?",(r["id"],))
   raise
def profiles(subid):
 if not isinstance(subid,str) or not 1<=len(subid)<=128:raise ValueError("Invalid subscription")
 with connection() as c:
  rows=[dict(r) for r in clients(c) if r["sub_id"]==subid]
  accounts={a["client_id"]:dict(a) for a in c.execute("SELECT * FROM masiha_accounts")}
 if not rows:raise ValueError("Subscription not found")
 result=[]
 for r in rows:
  if not permitted(r):continue
  a=accounts.get(r["id"])
  if not a or not a["ready"] or a["generation"]!=r["created_at"]:continue
  for proto in active_protocols():
   with manager.db() as m:row=m.execute("SELECT * FROM users WHERE protocol=? AND username=?",(proto,a["username"])).fetchone()
   if not row:continue
   cfg=manager.profile(row)
   if proto=="openconnect":
    cfg["content"]="Server: https://"+manager.HOST+":4443\nUsername: "+a["username"]+"\nPassword: "+a["password"]+"\nProtocol: AnyConnect / OpenConnect\n"
   result.append({**cfg,"protocol":proto,"clientId":r["id"],"label":r["email"],"expires":max(0,expiry(r)//1000)})
 return result
def hook():
 env=os.environ
 if env.get("REASON") in ("connect","disconnect"):
  proto="openconnect";name=env["USERNAME"];session=oc_session(env["ID"])
  disconnect=env["REASON"]=="disconnect"
  up=int(env.get("STATS_BYTES_IN","0"));down=int(env.get("STATS_BYTES_OUT","0"))
 else:
  proto="openvpn";cn=env.get("common_name","")
  with manager.db() as m:row=m.execute("SELECT username FROM users WHERE protocol='openvpn' AND cert=?",(cn,)).fetchone()
  if not row:return
  name=row["username"];session=ovpn_session(cn,env.get("trusted_ip","")+":"+env.get("trusted_port",""),env.get("time_unix",""))
  disconnect=env.get("script_type")=="client-disconnect"
  up=int(env.get("bytes_received","0"));down=int(env.get("bytes_sent","0"))
 if disconnect:
  credit(name,proto,session,up,down);return
 with connection() as c:
  a=account_for_username(c,name)
  if not a:return # Independent legacy VPN accounts retain their existing policy.
  r=next((r for r in clients(c) if r["id"]==a["client_id"] and r["created_at"]==a["generation"]),None)
 if r is None or not permitted(r):raise ValueError("Subscription disabled, expired or exhausted")
 # Native traffic job converts delayed-start expiry on first observed traffic.
 # A minimal first-connect sample triggers the same lifecycle, without changing
 # credential/inbound fields or minting a second expiration policy.
 credit(name,proto,session,up,down)
if __name__=="__main__":
 os.umask(0o077)
 try:
  if len(sys.argv)>1 and sys.argv[1]=="hook":hook()
  else:
   req=json.loads(sys.stdin.read(4096))
   print(json.dumps({"success":True,"obj":profiles(req["subId"])}))
 except Exception as e:
  if len(sys.argv)>1:print("Unified VPN hook rejected: "+str(e),file=sys.stderr);sys.exit(1)
  print(json.dumps({"success":False,"msg":"Subscription profiles unavailable"}))