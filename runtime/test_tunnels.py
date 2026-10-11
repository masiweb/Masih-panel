import subprocess,sys,json,secrets,time,os,tempfile
from pathlib import Path
def run(*a,check=True):
 return subprocess.run(a,text=True,capture_output=True,check=check,timeout=20)
def call(proto,act,**kw):
 p=run("python3","/opt/masiha-vpn-extension/manager.py") if False else subprocess.run(["python3","/opt/masiha-vpn-extension/manager.py"],input=json.dumps(dict(protocol=proto,action=act,**kw)),text=True,capture_output=True,timeout=60)
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
     cfg=call(proto,"config",username=name)["content"].replace("remote xu.chanelchat.ir 1194","remote 91.107.136.84 1194")
     path=Path(tmp)/"test.ovpn";path.write_text(cfg);path.chmod(0o600)
     args=["openvpn","--config",str(path),"--auth-nocache"]
    else:
     args=["openconnect","--protocol=anyconnect","--resolve=xu.chanelchat.ir:91.107.136.84","--user="+name,"--passwd-on-stdin","--non-inter","--script=/opt/masiha-vpn-extension/test-oc-route.sh","https://xu.chanelchat.ir:4443"]
    proc=subprocess.Popen(["ip","netns","exec",ns,*args],stdin=subprocess.PIPE,stdout=log,stderr=log,text=True)
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
    assert "ip=91.107.136.84" in p.stdout,p.stdout
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
