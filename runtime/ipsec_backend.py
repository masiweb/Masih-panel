#!/usr/bin/env python3
"""Managed IPsec account adapter for the Masiha shared-subscription system.

Provisioning is intentionally unavailable until audited native strongSwan,
xl2tpd and session-accounting hooks are installed. Never report a protocol as
connected or safely quota-enforced solely because credentials exist.
"""
import fcntl
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

ROOT=Path("/etc/masiha-vpn-extension/ipsec")
ACTIVE=ROOT/"enabled"
UNITS={"ikev2":"strongswan-starter.service","l2tp":"xl2tpd.service"}
from runtime_config import DOMAIN
HOST=DOMAIN
ALLOWED=("ikev2","l2tp")
USERNAME=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,39}$")
SAFE_PASSWORD=re.compile(r"^[A-Za-z0-9!@#$%^*_=+:.?-]{12,128}$")

def configured(protocol):
    if protocol not in ALLOWED:return False
    # The service isn't usable unless both the native daemon and verified
    # accounting hooks are in place.
    return (ACTIVE.is_file() and (ROOT/"accounting-verified").is_file()
            and Path("/usr/sbin/ipsec").is_file() and Path("/usr/sbin/swanctl").is_file()
            and (protocol!="l2tp" or Path("/usr/sbin/xl2tpd").is_file()))

def run(*argv,check=True):
    p=subprocess.run(list(argv),text=True,capture_output=True,timeout=25)
    if check and p.returncode:
        raise RuntimeError("IPsec service command failed")
    return p.stdout

def db():
    import manager
    c=manager.db()
    c.execute("""CREATE TABLE IF NOT EXISTS ipsec_credentials(
      username TEXT PRIMARY KEY, password TEXT NOT NULL,
      created INTEGER NOT NULL DEFAULT 0)""")
    return c

def credentials(c,user):
    row=c.execute("SELECT password FROM ipsec_credentials WHERE username=?",(user,)).fetchone()
    return row[0] if row else ""

def render_secrets(c,psk):
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}",psk):
        raise ValueError("Invalid shared IPsec PSK")
    users=c.execute("""SELECT DISTINCT username,password FROM ipsec_credentials
      WHERE username IN (SELECT username FROM users WHERE protocol IN ('ikev2','l2tp') AND enabled=1)
      ORDER BY username""").fetchall()
    for username,password in users:
        if not USERNAME.fullmatch(username) or not SAFE_PASSWORD.fullmatch(password):
            raise ValueError("Unsafe credential")
    ike=["# Generated Masiha IPsec credentials. Never edit by hand.",
         ": PSK "+'"'+psk+'"']
    chap=["# Managed Masiha L2TP usernames, not public credentials."]
    for name,password in users:
        ike.append(name+' : EAP "'+password+'"')
        chap.append('"'+name+'" xl2tpd "'+password+'" *')
    return "\n".join(ike)+"\n", "\n".join(chap)+"\n"

def atomic(dest,content):
    dest.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    with tempfile.NamedTemporaryFile("w",dir=dest.parent,prefix=".tmp-",delete=False) as f:
        os.fchmod(f.fileno(),0o600)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
        tmp=Path(f.name)
    os.replace(tmp,dest)

def apply(c):
    if not ACTIVE.exists():return
    pskfile=ROOT/"psk"
    if not pskfile.exists():raise RuntimeError("IPsec PSK unavailable")
    psk=pskfile.read_text().strip()
    ike,chap=render_secrets(c,psk)
    atomic(ROOT/"masiha.secrets",ike)
    atomic(ROOT/"chap-secrets",chap)
    # Native ipsec.secrets includes managed secrets via explicit include.
    if configured("ikev2"):
        run("/usr/sbin/ipsec","rereadsecrets")

def validate(username,password):
    if not isinstance(username,str) or not USERNAME.fullmatch(username):
        raise ValueError("Invalid IPsec username")
    if not isinstance(password,str) or not SAFE_PASSWORD.fullmatch(password):
        raise ValueError("IPsec password must be 12–128 safe characters")

def provision(protocol,action,username,enabled=True,expires=0,password=""):
    if protocol not in ALLOWED:raise ValueError("Invalid IPsec protocol")
    if not configured(protocol):raise RuntimeError("IPsec is not yet configured and accounting verified")
    if action not in ("create","update","delete"):raise ValueError("Invalid action")
    if not USERNAME.fullmatch(username):raise ValueError("Invalid username")
    if action=="create" and not password:raise ValueError("Password required")
    if password:validate(username,password)
    with db() as c:
        row=c.execute("SELECT * FROM users WHERE protocol=? AND username=?",(protocol,username)).fetchone()
        if action=="create" and row:raise ValueError("User already exists")
        if action in ("update","delete") and not row:raise ValueError("User not found")
        if action=="delete":
            c.execute("DELETE FROM users WHERE protocol=? AND username=?",(protocol,username))
            if not c.execute("SELECT 1 FROM users WHERE username=? AND protocol IN ('ikev2','l2tp')",(username,)).fetchone():
                c.execute("DELETE FROM ipsec_credentials WHERE username=?",(username,))
        else:
            if type(enabled)!=bool or type(expires)!=int or not 0<=expires<=4102444800:
                raise ValueError("Invalid limits")
            if enabled and expires and expires<int(time.time()):
                raise ValueError("Expired")
            if password:
                c.execute("""INSERT INTO ipsec_credentials(username,password,created)
                   VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password""",
                   (username,password,int(time.time())))
            elif not credentials(c,username):
                raise ValueError("Missing password")
            if action=="create":
                c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                          (protocol,username,int(enabled),expires,"",int(time.time())))
            else:
                c.execute("UPDATE users SET enabled=?,expires=? WHERE protocol=? AND username=?",
                          (int(enabled),expires,protocol,username))
        apply(c)
        c.execute("INSERT INTO audit VALUES(?,?,?,?)",(int(time.time()),action,protocol,username))
    return {"username":username}

def profile(protocol,username):
    if protocol not in ALLOWED:raise ValueError("Invalid protocol")
    with db() as c:
        r=c.execute("SELECT * FROM users WHERE username=? AND protocol=?",(username,protocol)).fetchone()
        if not r or not r["enabled"] or (r["expires"] and r["expires"]<=time.time()):
            raise ValueError("IPsec account not active")
        password=credentials(c,username)
    if not password:raise RuntimeError("Missing credentials")
    if protocol=="ikev2":
        description=("Protocol: IKEv2 / IPsec\nServer: "+HOST+
          "\nRemote ID: "+HOST+"\nAuthentication: EAP-MSCHAPv2\n"+
          "Username: "+username+"\nPassword: "+password+"\n")
    else:
        psk=(ROOT/"psk").read_text().strip()
        description=("Protocol: L2TP / IPsec PSK\nServer: "+HOST+
          "\nUsername: "+username+"\nPassword: "+password+
          "\nIPsec PSK: "+psk+"\n")
    return {"filename":username+"-"+protocol+".txt","content":description}

def status(protocol):
    if protocol not in ALLOWED:raise ValueError("Invalid protocol")
    state=run("systemctl","is-active",UNITS[protocol],check=False).strip()
    return {"service":state,"endpoint":HOST+":500/4500 UDP",
            "online":[],"configured":configured(protocol)}

def handle(req):
    protocol=req.get("protocol")
    if protocol not in ALLOWED:raise ValueError("Invalid IPsec protocol")
    action=req.get("action")
    if action=="list":
        with db() as c:
            users=[{"username":x["username"],"enabled":bool(x["enabled"]),
                    "expires":x["expires"],"expired":bool(x["expires"] and x["expires"]<time.time()),
                    "created":x["created"]} for x in
                   c.execute("SELECT * FROM users WHERE protocol=? ORDER BY created DESC",(protocol,))]
        return {"users":users,"status":status(protocol)}
    if action=="service":
        if not configured(protocol):raise RuntimeError("IPsec service not enabled")
        command=req.get("operation")
        if command not in ("start","stop","restart"):raise ValueError("Invalid service operation")
        run("systemctl",command,UNITS[protocol])
        return status(protocol)
    if action=="config":return profile(protocol,req.get("username"))
    return provision(protocol,action,req.get("username"),req.get("enabled",True),req.get("expires",0),req.get("password",""))