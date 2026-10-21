#!/usr/bin/python3
"""Local-only VPN administration helper. Called by the authenticated 3x-ui API."""
import base64, contextlib, datetime, fcntl, json, os, re, secrets, socket, sqlite3, subprocess, sys, time
from pathlib import Path
BASE=Path("/etc/masiha-vpn-extension")
PKI=Path("/etc/openvpn/masiha/pki")
EASY="/usr/share/easy-rsa/easyrsa"
UNITS={"openvpn":"openvpn-server@masiha.service","openconnect":"ocserv.service"}
from runtime_config import DOMAIN
HOST=DOMAIN
def run(args, data=None, check=True):
    p=subprocess.run(args,input=data,text=True,capture_output=True,timeout=45)
    if check and p.returncode: raise RuntimeError("Command failed: "+Path(args[0]).name+" "+p.stderr[-700:])
    return p.stdout
def easy(*args):
    env=os.environ.copy()
    env.update(EASYRSA_PKI=str(PKI),EASYRSA_BATCH="1",EASYRSA_ALGO="ec",EASYRSA_CURVE="prime256v1",EASYRSA_CERT_EXPIRE="3650",EASYRSA_CRL_DAYS="3650")
    p=subprocess.run([EASY,*args],env=env,text=True,capture_output=True,timeout=45)
    if p.returncode: raise RuntimeError("Certificate operation failed: "+p.stderr[-500:])
def db():
    c=sqlite3.connect(BASE/"users.db",timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS users (protocol TEXT, username TEXT, enabled INTEGER, expires INTEGER, cert TEXT, created INTEGER, PRIMARY KEY(protocol,username))")
    c.execute("CREATE TABLE IF NOT EXISTS audit (at INTEGER, action TEXT, protocol TEXT, username TEXT)")
    return c
def valid_user(s):
    if not isinstance(s,str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,39}",s): raise ValueError("نام کاربری باید ۳ تا ۴۰ حرف انگلیسی، عدد، خط تیره یا زیرخط باشد")
    return s
def management(command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(3); s.connect("/etc/openvpn/masiha/management.sock")
        s.recv(4096); s.sendall((command+"\n").encode())
        chunks=[]
        while True:
            b=s.recv(65536)
            if not b: break
            chunks.append(b)
            joined=b"".join(chunks)
            if b"END\r\n" in joined or b"END\n" in joined or b"SUCCESS:" in joined or b"ERROR:" in joined:break
            if len(joined)>1048576:raise ValueError("Management response too large")
        return b"".join(chunks).decode(errors="replace")
def disconnect(protocol,row):
    if protocol=="openvpn":
        with contextlib.suppress(Exception): management("kill "+row["cert"])
    else:
        run(["occtl","disconnect","user",row["username"]],check=False)
def cert_new(name):
    cn="mv-"+name+"-"+secrets.token_hex(5)
    easy("build-client-full",cn,"nopass")
    return cn
def revoke(cn):
    if cn:
        index=(PKI/"index.txt").read_text()
        if any(line.startswith("V\t") and ("/CN="+cn) in line for line in index.splitlines()):
            easy("revoke",cn); easy("gen-crl")
            os.chmod(PKI/"crl.pem",0o644)
def public(row):
    d=dict(row); d.pop("cert",None)
    d["enabled"]=bool(d["enabled"])
    d["expired"]=bool(d["expires"] and d["expires"]<=int(time.time()))
    return d
def status(protocol):
    p=subprocess.run(["systemctl","is-active",UNITS[protocol]],text=True,capture_output=True)
    online=[]
    try:
        if protocol=="openvpn":
            for l in management("status 3").splitlines():
                a=l.split("\t")
                if a[0]=="CLIENT_LIST" and len(a)>5: online.append({"certificate":a[1],"address":a[2],"bytes_in":a[5],"bytes_out":a[6]})
        else:
            raw=run(["occtl","--json","show","users"])
            data=json.loads(raw)
            online=data if isinstance(data,list) else data.get("Users",[])
    except Exception: pass
    return {"service":p.stdout.strip(),"online":online,"endpoint":HOST+(":1194" if protocol=="openvpn" else ":4443")}
def expire(c):
    for row in c.execute("SELECT * FROM users WHERE enabled=1 AND expires>0 AND expires<=?",(int(time.time()),)).fetchall():
        disable(c,row)
def disable(c,row):
    if row["protocol"]=="openvpn": revoke(row["cert"])
    else: run(["ocpasswd","-c","/etc/ocserv/masiha.passwd","-l",row["username"]])
    c.execute("UPDATE users SET enabled=0 WHERE protocol=? AND username=?",(row["protocol"],row["username"]))
    c.commit()
    disconnect(row["protocol"],row)
def profile(row):
    if row["protocol"] in ("ikev2","l2tp"):
        import ipsec_backend
        return ipsec_backend.profile(row["protocol"],row["username"])
    if not row["enabled"] or row["expires"] and row["expires"]<=int(time.time()): raise ValueError("کاربر غیرفعال یا منقضی است")
    if row["protocol"]=="openconnect":
        return {"filename":row["username"]+"-openconnect.txt","content":"Server: https://"+HOST+":4443\nUsername: "+row["username"]+"\nProtocol: AnyConnect / OpenConnect\nPassword: use the password set by the administrator\n"}
    cn=row["cert"]
    cert=(PKI/"issued"/(cn+".crt")).read_text()
    cert=cert[cert.index("-----BEGIN CERTIFICATE-----"):]
    parts=["client","dev tun","proto udp","remote "+HOST+" 1194","resolv-retry infinite","nobind","persist-key","persist-tun","remote-cert-tls server","verify-x509-name masiha-openvpn name","tls-version-min 1.2","data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305","auth SHA256","verb 3"]
    for tag,path in [("ca",PKI/"ca.crt"),("key",PKI/"private"/(cn+".key")),("tls-crypt",Path("/etc/openvpn/masiha/tls-crypt.key"))]:
        parts.extend(["<"+tag+">",path.read_text().strip(),"</"+tag+">"])
    parts.extend(["<cert>",cert.strip(),"</cert>"])
    return {"filename":row["username"]+".ovpn","content":"\n".join(parts)+"\n"}
def handle(req):
    protocol=req.get("protocol")
    if protocol in ("ikev2","l2tp"):
        import ipsec_backend
        return ipsec_backend.handle(req)
    if protocol not in UNITS: raise ValueError("Invalid protocol")
    action=req.get("action")
    with db() as c:
        expire(c)
        if action=="list":
            return {"users":[public(x) for x in c.execute("SELECT * FROM users WHERE protocol=? ORDER BY created DESC",(protocol,))],"status":status(protocol)}
        if action=="service":
            op=req.get("operation")
            if op not in ("start","stop","restart"): raise ValueError("Invalid service operation")
            run(["systemctl",op,UNITS[protocol]])
            return status(protocol)
        name=valid_user(req.get("username"))
        row=c.execute("SELECT * FROM users WHERE protocol=? AND username=?",(protocol,name)).fetchone()
        if action=="config":
            if row is None: raise ValueError("کاربر پیدا نشد")
            return profile(row)
        if action not in ("create","update","delete"): raise ValueError("Invalid action")
        if action=="create" and row is not None: raise ValueError("این نام کاربری قبلاً ثبت شده")
        if action!="create" and row is None: raise ValueError("کاربر پیدا نشد")
        if action=="delete":
            disable(c,row)
            if protocol=="openconnect": run(["ocpasswd","-c","/etc/ocserv/masiha.passwd","-d",name])
            c.execute("DELETE FROM users WHERE protocol=? AND username=?",(protocol,name))
        else:
            expires=req.get("expires",0)
            if type(expires) is not int or expires<0 or expires>4102444800: raise ValueError("تاریخ انقضا نامعتبر است")
            enabled=req.get("enabled",True)
            if type(enabled) is not bool: raise ValueError("وضعیت نامعتبر است")
            if enabled and expires and expires<=int(time.time()): raise ValueError("تاریخ انقضا باید در آینده باشد")
            password=req.get("password","")
            if not isinstance(password,str): raise ValueError("رمز نامعتبر است")
            if protocol=="openconnect" and (action=="create" or password):
                if len(password)<12 or len(password)>128 or any(x in password for x in "\r\n\x00"):
                    raise ValueError("رمز باید ۱۲ تا ۱۲۸ کاراکتر و بدون خط جدید باشد")
            cn=row["cert"] if row else ""
            if protocol=="openvpn" and enabled and (not row or not row["enabled"]):
                cn=cert_new(name)
            if protocol=="openconnect":
                if action=="create" or password: run(["ocpasswd","-c","/etc/ocserv/masiha.passwd",name],password+"\n"+password+"\n")
                run(["ocpasswd","-c","/etc/ocserv/masiha.passwd","-u" if enabled else "-l",name])
            if action=="create":
                c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",(protocol,name,int(enabled),expires,cn,int(time.time())))
            else:
                c.execute("UPDATE users SET enabled=?,expires=?,cert=? WHERE protocol=? AND username=?",(int(enabled),expires,cn,protocol,name))
            c.commit()
            if not enabled:
                current=c.execute("SELECT * FROM users WHERE protocol=? AND username=?",(protocol,name)).fetchone()
                disable(c,current)
        c.execute("INSERT INTO audit VALUES(?,?,?,?)",(int(time.time()),action,protocol,name))
        c.commit()
        return {"username":name}
def main():
    BASE.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.umask(0o077)
    with open(BASE/"manager.lock","a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if len(sys.argv)>1 and sys.argv[1]=="expire":
            with db() as c: expire(c)
            return
        req=json.loads(sys.stdin.read(65537))
        try: print(json.dumps({"success":True,"obj":handle(req)},ensure_ascii=False))
        except Exception as e: print(json.dumps({"success":False,"msg":str(e)},ensure_ascii=False))
if __name__=="__main__": main()