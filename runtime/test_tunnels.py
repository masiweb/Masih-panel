import subprocess,sys,json,secrets,time,os,tempfile,re,socket
from pathlib import Path
# Tests intentionally create disposable users/network namespaces. NEVER reuse a
# production host's fixed IP: the actual TLS domain is read from this server.
DOMAIN=json.loads(Path("/etc/masiha-vpn-extension/config.json").read_text())["domain"]
SERVER_IP=socket.gethostbyname(DOMAIN)
assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+",DOMAIN)
assert re.fullmatch(r"(?:[0-9]{1,3}\\.){3}[0-9]{1,3}",SERVER_IP)
def run(*a,check=True):
 return subprocess.run(a,text=True,capture_output=True,check=check,timeout=20)
def call(proto,act,**kw):
 p=subprocess.run(["python3","/opt/masiha-vpn-extension/manager.py"],input=json.dumps(dict(protocol=proto,action=act,**kw)),text=True,capture_output=True,timeout=60)
 if p.returncode:raise RuntimeError("VPN manager command failed for "+proto+"/"+act+" (exit "+str(p.returncode)+")")
 r=json.loads(p.stdout)
 assert r["success"],r
 return r["obj"]
for proto in ("openvpn","openconnect"):
 ns="mvqa-"+secrets.token_hex(3); name="qatunnel-"+secrets.token_hex(3); password=secrets.token_urlsafe(20)
 proc=None;created=False
 with tempfile.TemporaryDirectory(prefix="test-",dir="/opt/masiha-vpn-extension") as tmp:
  try:
   run("ip","netns","add",ns)
   run("ip","link","add","mvqahost","type","veth","peer","name","mvqatest")
   run("ip","link","set","mvqatest","netns",ns)
   run("ip","addr","add","10.203.71.1/30","dev","mvqahost")
   run("ip","link","set","mvqahost","up")
   run("ip","netns","exec",ns,"ip","addr","add","10.203.71.2/30","dev","mvqatest")
   run("ip","netns","exec",ns,"ip","link","set","mvqatest","up")
   run("ip","netns","exec",ns,"ip","link","set","lo","up")
   run("ip","netns","exec",ns,"ip","route","add","default","via","10.203.71.1")
   call(proto,"create",username=name,password=password,enabled=True,expires=int(time.time())+600);created=True
   logfile=Path(tmp)/"client.log"
   with logfile.open("w") as log:
    if proto=="openvpn":
     cfg=call(proto,"config",username=name)["content"]
     # Bypass namespace DNS but retain the real PKI/server certificate checks.
     expected="remote "+DOMAIN+" 1194"
     assert expected in cfg,"OpenVPN profile does not use this server's configured domain"
     cfg=cfg.replace(expected,"remote "+SERVER_IP+" 1194")
     path=Path(tmp)/"test.ovpn";path.write_text(cfg);path.chmod(0o600)
     args=["openvpn","--config",str(path),"--auth-nocache"]
    else:
     args=["openconnect","--protocol=anyconnect","--resolve="+DOMAIN+":"+SERVER_IP,"--user="+name,"--passwd-on-stdin","--non-inter","--script=/opt/masiha-vpn-extension/test-oc-route.sh","https://"+DOMAIN+":4443"]
    proc=subprocess.Popen(["ip","netns","exec",ns,*args],stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,env={**os.environ,"MASIHA_QA_SERVER_IP":SERVER_IP})
    if proto=="openconnect": proc.stdin.write(password+"\n");proc.stdin.flush()
    connected=False
    for _ in range(25):
     time.sleep(1)
     txt=logfile.read_text()
     if ("Initialization Sequence Completed" in txt if proto=="openvpn" else "Configured as" in txt):
      connected=True;break
     if proc.poll() is not None: break
    if not connected:
     print(txt[-3000:]);raise RuntimeError(proto+" tunnel did not connect")
    print(proto,"TLS/auth/tunnel PASS",flush=True)
    p=run("ip","netns","exec",ns,"curl","--max-time","15","-fsS","https://1.1.1.1/cdn-cgi/trace")
    assert "ip="+SERVER_IP in p.stdout,"Tunnel public egress IP differs from this server"
    print(proto,"Internet through VPN PASS",flush=True)
    call(proto,"update",username=name,enabled=False,expires=0)
    time.sleep(2)
    print(proto,"disabled after active connection; online count",len(call(proto,"list")["status"]["online"]),flush=True)
  finally:
   if proc and proc.poll() is None:
    proc.terminate()
    try:proc.wait(timeout=8)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
   if created:call(proto,"delete",username=name)
   run("ip","link","delete","mvqahost",check=False)
   run("ip","netns","delete",ns,check=False)
