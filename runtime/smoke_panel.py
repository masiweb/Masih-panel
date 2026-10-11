import os,sys,json,subprocess,time,tempfile,sqlite3,secrets,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
binary=os.environ.get("MASIHA_TEST_BINARY","/opt/masiha-vpn-extension/x-ui-candidate")
with tempfile.TemporaryDirectory(prefix="smoke-",dir="/opt/masiha-vpn-extension") as tmp:
 env=os.environ.copy()
 env.update(XUI_DB_FOLDER=tmp,XUI_BIN_FOLDER=tmp+"/bin",XUI_LOG_FOLDER=tmp+"/logs")
 Path(tmp+"/bin").mkdir()
 password=secrets.token_urlsafe(24)
 subprocess.run([binary,"setting","-username","qatest","-password",password,"-port","62054","-listenIP","127.0.0.1","-webBasePath","/qa/"],env=env,capture_output=True,check=True)
 with sqlite3.connect(tmp+"/x-ui.db") as c:
  for k,v in {"subEnable":"false","subJsonEnable":"false","subClashEnable":"false"}.items():
   c.execute("DELETE FROM settings WHERE key=?",(k,))
   c.execute("INSERT INTO settings(key,value) VALUES(?,?)",(k,v))
 with open(tmp+"/panel.log","w") as log:
  p=subprocess.Popen([binary],env=env,cwd=tmp,stdout=log,stderr=log)
  try:
   base="http://127.0.0.1:62054/qa"
   jar=http.cookiejar.CookieJar()
   client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
   def req(path,body=None,token=None,opener=client):
    headers={"X-Requested-With":"XMLHttpRequest"}
    if body is not None:headers["Content-Type"]="application/json"
    if token:headers["X-CSRF-Token"]=token
    r=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:
     with opener.open(r,timeout=15) as response:return response.status,response.read()
    except urllib.error.HTTPError as e:return e.code,e.read()
   for _ in range(30):
    try:
     code,raw=req("/csrf-token")
     if code==200:break
    except OSError:pass
    time.sleep(1)
   token=json.loads(raw)["obj"]
   code,raw=req("/login",{"username":"qatest","password":password},token)
   assert json.loads(raw)["success"],raw
   print("Admin login PASS")
   _,raw=req("/panel/csrf-token");token=json.loads(raw)["obj"]
   payload={"protocol":"openvpn","action":"list"}
   code,raw=req("/panel/api/masiha-vpn",payload,opener=urllib.request.build_opener())
   assert code in (401,403,404),(code,raw)
   print("Unauthenticated rejected PASS")
   code,raw=req("/panel/api/masiha-vpn",payload)
   assert code==403,(code,raw)
   print("Missing CSRF rejected PASS")
   for proto in ("openvpn","openconnect"):
    code,raw=req("/panel/api/masiha-vpn",{"protocol":proto,"action":"list"},token)
    assert code==200 and json.loads(raw)["success"],(code,raw)
    assert json.loads(raw)["obj"]["status"]["service"]=="active"
    code,raw=req("/panel/"+proto)
    assert code==200 and b"<html" in raw
    print(proto,"authenticated API and page PASS")
   code,raw=req("/panel/api/sales-bot",{"action":"get"},token)
   assert code==200 and json.loads(raw)["success"]
   assert "token" not in json.loads(raw)["obj"]["settings"]
   code,raw=req("/panel/api/sales-bot",{"action":"get"})
   assert code==403
   code,raw=req("/panel/api/sales-bot",{"action":"get"},opener=urllib.request.build_opener())
   assert code in (401,403,404)
   code,raw=req("/panel/sales-bot")
   assert code==200 and b"<html" in raw
   print("Sales API authentication, CSRF, token masking and page PASS")
  finally:
   p.terminate()
   try:p.wait(timeout=15)
   except subprocess.TimeoutExpired:p.kill();p.wait()
