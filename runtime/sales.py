#!/usr/bin/python3
"""Masiha sales: private local administration and durable Telegram commerce."""
import contextlib, fcntl, hashlib, http.cookiejar, io, json, os, re, secrets, shlex, sqlite3, subprocess, sys, time, urllib.request, urllib.parse, uuid
from pathlib import Path
from runtime_config import DOMAIN, PUBLIC_PANEL_URL
BASE=Path(os.environ.get("MASIHA_SALES_DIR","/etc/masiha-vpn-extension/sales"))
DEFAULT={"enabled":False,"token":"","admins":[],"welcome":"به فروشگاه مسیحا خوش آمدید","support":"برای پشتیبانی پیام خود را ارسال کنید.","card":"","channel":"","require_contact":False,"buy":True,"renew":True,"wallet":True,"transfer":False,"trial":False,"referrals":False,"commission":5,"discounts":True,"notifications":True,"self_delete":False,"reseller":False,"reseller_discount":10,"trial_plan":0,"warning_days":3,"usage_warning_percent":10,"report_channel":"","bulk":False,"rename":True,"rotate":False,"new_user_notice":True,"zarinpal":False,"merchant_id":""}
def now():return int(time.time())
def conn():
 BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
 c=sqlite3.connect(BASE/"sales.db",timeout=30);c.row_factory=sqlite3.Row
 c.executescript("""
 PRAGMA journal_mode=WAL;
 CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,balance INTEGER DEFAULT 0 CHECK(balance>=0),blocked INTEGER DEFAULT 0,reseller INTEGER DEFAULT 0,referrer INTEGER DEFAULT 0,phone TEXT DEFAULT '',state TEXT DEFAULT '',created INTEGER);
 CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY,name TEXT,category TEXT,protocol TEXT,price INTEGER,days INTEGER,gb INTEGER,inbounds TEXT,capacity INTEGER,active INTEGER);
 CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER,plan TEXT,price INTEGER,status TEXT,service_id TEXT,renew_id TEXT DEFAULT '',created INTEGER,error TEXT DEFAULT '',trial INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS services(id TEXT PRIMARY KEY,user_id INTEGER,protocol TEXT,username TEXT,plan TEXT,expires INTEGER,data TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,reason TEXT,reference TEXT UNIQUE,created INTEGER);
 CREATE TABLE IF NOT EXISTS topups(id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,receipt TEXT,status TEXT DEFAULT 'pending',created INTEGER);
 CREATE TABLE IF NOT EXISTS coupons(code TEXT PRIMARY KEY,percent INTEGER,credit INTEGER,uses INTEGER,expires INTEGER);
 CREATE TABLE IF NOT EXISTS redeemed(code TEXT,user_id INTEGER,PRIMARY KEY(code,user_id));
 CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY,user_id INTEGER,body TEXT,reply TEXT DEFAULT '',created INTEGER);
 CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,dedupe TEXT UNIQUE,chat INTEGER,method TEXT,payload TEXT,done INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,next_try INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY,created INTEGER);
 CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,user_id INTEGER,amount INTEGER,authority TEXT UNIQUE,status TEXT,ref TEXT,created INTEGER);
 CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,user_id INTEGER,plan_id INTEGER,plan TEXT,renew_id TEXT,expires INTEGER);
 CREATE TABLE IF NOT EXISTS receipt_orders(topup_id INTEGER UNIQUE,order_id TEXT UNIQUE);
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,action TEXT,detail TEXT,created INTEGER);
 """)
 # Backward-compatible additive migration; existing plans and orders survive.
 if not any(col[1]=="is_trial" for col in c.execute("PRAGMA table_info(plans)")):
  try:c.execute("ALTER TABLE plans ADD COLUMN is_trial INTEGER NOT NULL DEFAULT 0")
  except sqlite3.OperationalError as exc:
   if "duplicate column name" not in str(exc).lower():raise
 return c
def settings(c):return {**DEFAULT,**{r["k"]:json.loads(r["v"]) for r in c.execute("SELECT * FROM settings")}}
def put(c,k,v):c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",(k,json.dumps(v,ensure_ascii=False)))
def audit(c,a,d):c.execute("INSERT INTO audit(action,detail,created) VALUES(?,?,?)",(a,str(d)[:500],now()))
def integer(x,lo=0,hi=10**12):
 if type(x) is not int or not lo<=x<=hi:raise ValueError("عدد واردشده نامعتبر است")
 return x
def text(x,n=4000):
 if not isinstance(x,str) or len(x)>n:raise ValueError("متن نامعتبر است")
 return x.strip()
def enqueue(c,chat,body,keys=None,key=None):
 p={"chat_id":chat,"text":body[:4000]}
 if keys:p["reply_markup"]={"inline_keyboard":[[{"text":a,"callback_data":b} for a,b in row] for row in keys]}
 c.execute("INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(key or secrets.token_hex(12),chat,"sendMessage",json.dumps(p,ensure_ascii=False)))
def money(c,uid,amount,reason,ref):
 if c.execute("SELECT 1 FROM ledger WHERE reference=?",(ref,)).fetchone():return
 if not c.execute("UPDATE users SET balance=balance+? WHERE id=? AND balance+?>=0",(amount,uid,amount)).rowcount:raise ValueError("موجودی کافی نیست یا کاربر وجود ندارد")
 c.execute("INSERT INTO ledger(user_id,amount,reason,reference,created) VALUES(?,?,?,?,?)",(uid,amount,reason,ref,now()))
def vpn(req):
 p=subprocess.run(["/usr/bin/python3","/opt/masiha-vpn-extension/manager.py"],input=json.dumps(req),text=True,capture_output=True,timeout=55)
 try:r=json.loads(p.stdout)
 except Exception:raise ValueError("ارتباط با سرویس VPN ناموفق بود")
 if not r["success"]:raise ValueError(r.get("msg","خطای VPN"))
 return r["obj"]
class Panel:
 def __init__(self):
  env={}
  for line in Path("/etc/x-ui/install-result.env").read_text().splitlines():
   if "=" in line and not line.startswith("#"):
    k,v=line.split("=",1);env[k]=shlex.split(v)[0] if shlex.split(v) else ""
  self.base="http://127.0.0.1:"+env.get("XUI_PANEL_PORT","62053")+"/"+env.get("XUI_WEB_BASE_PATH","xui").strip("/")
  self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  self.token=self.call("/csrf-token")
  self.call("/login",{"username":env["XUI_USERNAME"],"password":env["XUI_PASSWORD"]})
  self.token=self.call("/panel/csrf-token")
 def call(self,path,data=None):
  headers={"Content-Type":"application/json","X-Requested-With":"XMLHttpRequest","Host":DOMAIN}
  if getattr(self,"token",None):headers["X-CSRF-Token"]=self.token
  try:
   with self.client.open(urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=25) as r:obj=json.load(r)
  except Exception:raise ValueError("ارتباط با پنل برقرار نشد")
  if not obj.get("success"):raise ValueError("پنل عملیات را نپذیرفت")
  return obj.get("obj")
def subscription_url(sub_id):
 with sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True) as db:
  row=db.execute("SELECT value FROM settings WHERE key='subURI'").fetchone()
 if not row:raise ValueError("آدرس HTTPS ساب در پنل تنظیم نشده")
 base=row[0].strip();url=urllib.parse.urlparse(base)
 if url.scheme!="https" or url.hostname!=DOMAIN:raise ValueError("دامنه HTTPS ساب را در پنل بررسی کنید")
 return base.rstrip("/")+"/"+urllib.parse.quote(sub_id,safe="")
def inbound_list():
 return [{"id":r["id"],"name":r.get("remark",""),"protocol":r["protocol"],"enable":r["enable"]} for r in Panel().call("/panel/api/inbounds/list")]
def tg(s,method,payload):
 try:
  req=urllib.request.Request("https://api.telegram.org/bot"+s["token"]+"/"+method,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
  with urllib.request.urlopen(req,timeout=35) as r:obj=json.load(r)
  if not obj.get("ok"):raise ValueError()
  return obj["result"]
 except Exception:raise ValueError("ارتباط با تلگرام ناموفق است؛ توکن، شبکه و وضعیت webhook را بررسی کنید")
def save_settings(c,r):
 s=settings(c)
 for k,v in r.items():
  if k not in DEFAULT:continue
  if k=="token":
   if not v:continue
   if s["token"] and v.split(":")[0]!=s["token"].split(":")[0] and c.execute("SELECT 1 FROM users LIMIT 1").fetchone():raise ValueError("تغییر هویت ربات دارای مشتری مجاز نیست؛ فقط توکن جدید همان ربات را وارد کنید")
   if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}",v):raise ValueError("فرمت توکن نادرست است")
  elif k=="merchant_id":
   if not v:continue
   try:v=str(uuid.UUID(v))
   except Exception:raise ValueError("مرچنت زرین‌پال باید UUID معتبر باشد")
  elif k=="admins":
   if not isinstance(v,list) or not v:raise ValueError("حداقل یک آیدی مدیر لازم است")
   v=list(set(integer(i,1,2**52) for i in v))
  elif type(DEFAULT[k]) is bool:
   if type(v) is not bool:raise ValueError("وضعیت نامعتبر")
  elif type(DEFAULT[k]) is int:v=integer(v,0,100 if k in ("commission","reseller_discount","warning_days","usage_warning_percent") else 10**9)
  else:v=text(v)
  s[k]=v
 if s["zarinpal"] and not s["merchant_id"]:s["zarinpal"]=False
 if s["enabled"] and (not s["token"] or not s["admins"]):raise ValueError("ابتدا توکن و آیدی مدیر را ثبت کنید")
 if s["enabled"]:
  me=tg(s,"getMe",{});tg(s,"deleteWebhook",{"drop_pending_updates":False});put(c,"bot_username",me["username"])
 for k in DEFAULT:put(c,k,s[k])
 audit(c,"settings","updated")
 return True
def order(c,uid,pid,reference,renew="",trial=False,card=False):
 existing=c.execute("SELECT id FROM orders WHERE id=?",(reference,)).fetchone()
 if existing:
  if trial:raise ValueError("شما قبلاً تست رایگان دریافت کرده‌اید")
  return existing["id"]
 s=settings(c);u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if not u or u["blocked"]:raise ValueError("دسترسی مسدود است")
 if (not trial and not s["buy"]) or (renew and not s["renew"]):
  raise ValueError("فروش یا تمدید غیرفعال است")
 p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(pid,)).fetchone()
 if not p:raise ValueError("پلن موجود نیست")
 p=dict(p)
 if bool(p["is_trial"])!=bool(trial):
  raise ValueError("پلن تست فقط از مسیر دریافت رایگان قابل ثبت است")
 if trial:
  if not s["trial"]:raise ValueError("ارائه تست رایگان غیرفعال است")
  if renew or card:raise ValueError("تست رایگان قابل تمدید یا پرداخت نیست")
  if c.execute("SELECT 1 FROM orders WHERE user_id=? AND trial=1",(uid,)).fetchone():raise ValueError("قبلاً تست دریافت کرده‌اید")
 if renew:
  if c.execute("SELECT 1 FROM orders WHERE service_id=? AND status IN (\'paid\',\'review\',\'awaiting_receipt\',\'receipt_review\')",(renew,)).fetchone():raise ValueError("یک سفارش برای این سرویس در حال پردازش یا بررسی است")
  service=c.execute("SELECT * FROM services WHERE id=? AND user_id=? AND deleted=0",(renew,uid)).fetchone()
  if not service or service["protocol"]!=p["protocol"]:raise ValueError("سرویس قابل تمدید نیست")
 else:
  count=c.execute("SELECT COUNT(*) FROM orders WHERE json_extract(plan,'$.id')=? AND renew_id='' AND status!='cancelled'",(pid,)).fetchone()[0]
  if p["capacity"] and count>=p["capacity"]:raise ValueError("ظرفیت پلن تکمیل است")
 price=0 if trial else p["price"]
 if u["reseller"] and s["reseller"]:price=price*(100-s["reseller_discount"])//100
 coupon=u["state"][7:] if u["state"].startswith("coupon:") else ""
 if coupon and s["discounts"] and not trial:
  cp=c.execute("SELECT * FROM coupons WHERE code=? AND uses>0 AND (expires=0 OR expires>?)",(coupon,now())).fetchone()
  if cp and not c.execute("SELECT 1 FROM redeemed WHERE code=? AND user_id=?",(coupon,uid)).fetchone():
   price=price*(100-cp["percent"])//100
   c.execute("INSERT INTO redeemed VALUES(?,?)",(coupon,uid));c.execute("UPDATE coupons SET uses=uses-1 WHERE code=?",(coupon,))
   p["_coupon_code"]=coupon
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 if not card and not trial:money(c,uid,-price,"purchase",reference+":debit")
 sid=renew or secrets.token_hex(12)
 c.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",(reference,uid,json.dumps(p),price,"awaiting_receipt" if card else "paid",sid,renew,now(),"",int(trial)))
 return reference
def provision(c,o):
 p=json.loads(o["plan"]);uid=o["user_id"];sid=o["service_id"];name="ms-"+sid[:20]
 old=c.execute("SELECT * FROM services WHERE id=?",(sid,)).fetchone()
 # Persist absolute targets before contacting the VPN so retries cannot extend twice.
 data=json.loads(old["data"]) if old else {"uuid":str(uuid.uuid4()),"password":secrets.token_urlsafe(24),"subId":secrets.token_urlsafe(18)}
 if not o["error"].startswith("target:"):
  target=max(now(),old["expires"] if old and o["renew_id"] else 0)+p["days"]*86400
  data["expires"]=target
  data["totalGB"]=int(data.get("totalGB",0))+p["gb"]*1024**3 if old else p["gb"]*1024**3
  c.execute("UPDATE orders SET error=? WHERE id=?",("target:"+json.dumps(data),o["id"]));c.commit()
 else:data=json.loads(o["error"][7:]);target=data["expires"]
 if p["protocol"]=="xray":
  api=Panel()
  try:current=api.call("/panel/api/clients/get/"+name)
  except ValueError:current=None
  client={"email":name,"id":data["uuid"],"password":data["password"],"auth":data["password"],"security":"auto","flow":"","subId":data["subId"],"enable":True,"expiryTime":target*1000,"totalGB":data["totalGB"],"tgId":uid,"limitIp":0,"comment":"Masiha sales"}
  if current:api.call("/panel/api/clients/update/"+name,client)
  else:api.call("/panel/api/clients/add",{"client":client,"inboundIds":json.loads(p["inbounds"])})
  check=api.call("/panel/api/clients/get/"+name)
  if not set(json.loads(p["inbounds"])).issubset(set(check["inboundIds"])):raise ValueError("اتصال به همه اینباندها کامل نشده")
  data["subscription"]=[subscription_url(data["subId"])]
  data["links"]=api.call("/panel/api/clients/links/"+name)
 else:
  exists=any(u["username"]==name for u in vpn({"protocol":p["protocol"],"action":"list"})["users"])
  vpn({"protocol":p["protocol"],"action":"update" if exists else "create","username":name,"password":data["password"] if p["protocol"]=="openconnect" else "","enabled":True,"expires":target})
 c.execute("INSERT OR REPLACE INTO services VALUES(?,?,?,?,?,?,?,0)",(sid,uid,p["protocol"],name,o["plan"],target,json.dumps(data)))
 c.execute("UPDATE orders SET status='fulfilled',error='' WHERE id=?",(o["id"],))
 s=settings(c);u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if s["referrals"] and u["referrer"] and o["price"]:
  money(c,u["referrer"],o["price"]*s["commission"]//100,"commission",o["id"]+":commission")
 enqueue(c,uid,"اشتراک آماده است. از «سرویس‌های من» دریافت کنید.",[[("دریافت سرویس","service:"+sid)]],o["id"]+":delivered")
 for admin in s["admins"]+([s["report_channel"]] if s["report_channel"] else []):enqueue(c,admin,"سفارش "+o["id"]+" تحویل شد؛ مبلغ "+str(o["price"])+" تومان.",key=o["id"]+":admin:"+str(admin))

def restore_reserved_coupon(c,o):
 # A rejected receipt should free a coupon reserved by the card order.
 if not o or o["status"] not in ("awaiting_receipt","receipt_review"):return
 try: code=json.loads(o["plan"]).get("_coupon_code")
 except (ValueError,TypeError):code=None
 if code:
  removed=c.execute("DELETE FROM redeemed WHERE code=? AND user_id=?",(code,o["user_id"])).rowcount
  if removed:
   c.execute("UPDATE coupons SET uses=uses+1 WHERE code=?",(code,))
   audit(c,"coupon_restored","order:"+o["id"])

def decide_receipt(c,ident,approve):
 r=c.execute("SELECT * FROM topups WHERE id=?",(ident,)).fetchone()
 if not r or r["status"]!="pending":raise ValueError("این درخواست قبلاً رسیدگی شده")
 linked=c.execute("SELECT order_id FROM receipt_orders WHERE topup_id=?",(ident,)).fetchone()
 if linked:
  o=c.execute("SELECT * FROM orders WHERE id=?",(linked["order_id"],)).fetchone()
  if not o or o["status"]!="receipt_review" or o["price"]!=r["amount"] or o["user_id"]!=r["user_id"]:raise ValueError("سفارش رسید معتبر نیست")
  if not approve:restore_reserved_coupon(c,o)
  c.execute("UPDATE orders SET status=? WHERE id=?",("paid" if approve else "cancelled",o["id"]))
  enqueue(c,r["user_id"],"رسید سفارش تأیید شد؛ اشتراک در حال ساخت است." if approve else "رسید سفارش رد شد؛ با پشتیبانی تماس بگیرید.")
 else:
  if approve:money(c,r["user_id"],r["amount"],"topup","topup:"+str(ident))
  enqueue(c,r["user_id"],"درخواست شارژ "+("تأیید" if approve else "رد")+" شد.")
 c.execute("UPDATE topups SET status=? WHERE id=?",("approved" if approve else "rejected",ident))
 audit(c,"receipt_approved" if approve else "receipt_rejected",ident)

def plan_overview(c):
 # Only purchases of NEW subscriptions reserve a plan's limited stock.
 # Renewals must not consume a second plan slot.
 out=[]
 for row in c.execute("SELECT * FROM plans ORDER BY active DESC,id DESC"):
  p=dict(row)
  totals=c.execute("""SELECT
   COUNT(*) AS committed,
   SUM(CASE WHEN status='fulfilled' THEN 1 ELSE 0 END) AS fulfilled,
   SUM(CASE WHEN status IN ('awaiting_receipt','receipt_review','paid','review') THEN 1 ELSE 0 END) AS pending
   FROM orders
   WHERE json_extract(plan,'$.id')=? AND renew_id='' AND status!='cancelled'""",(p["id"],)).fetchone()
  p["has_history"]=bool(c.execute("SELECT 1 FROM orders WHERE json_extract(plan,'$.id')=? LIMIT 1",(p["id"],)).fetchone())
  p["sold"]=totals["committed"]
  p["delivered"]=totals["fulfilled"] or 0
  p["pending"]=totals["pending"] or 0
  p["remaining"]=max(0,p["capacity"]-p["sold"]) if p["capacity"] else None
  out.append(p)
 return out

def save_plan(c,p):
 if not isinstance(p,dict):raise ValueError("اطلاعات پلن نامعتبر است")
 proto=p.get("protocol")
 if proto not in ("xray","openvpn","openconnect"):
  raise ValueError("پروتکل پلن نامعتبر است")
 name=text(p.get("name"),100)
 category=text(p.get("category","عمومی"),100)
 if not name:raise ValueError("نام پلن لازم است")
 if not category:category="عمومی"
 ids=p.get("inbounds",[])
 if not isinstance(ids,list):raise ValueError("لیست اینباند نامعتبر است")
 ids=[integer(x,1,2**31-1) for x in ids]
 if len(set(ids))!=len(ids):raise ValueError("اینباند تکراری انتخاب شده است")
 gb=integer(p.get("gb",0),0,100000)
 if proto=="xray":
  available={x["id"] for x in inbound_list() if x["enable"] and x["protocol"] in
   ("vless","vmess","trojan","shadowsocks","hysteria","tuic")}
  if not ids or not set(ids).issubset(available):
   raise ValueError("برای پلن Xray حداقل یک اینباند فعال انتخاب کنید")
 elif ids:
  raise ValueError("برای پلن مستقل OpenVPN/OpenConnect اینباند Xray تعیین نکنید")
 elif gb:
  raise ValueError("پلن مستقل OpenVPN/OpenConnect هنوز سهمیه حجمی ندارد؛ حجم را صفر بگذارید")
 price=integer(p.get("price"),0,10**12)
 days=integer(p.get("days"),1,3650)
 capacity=integer(p.get("capacity",0),0,1000000)
 active=p.get("active",True)
 if type(active) is not bool:raise ValueError("وضعیت فعال‌بودن پلن نامعتبر است")
 is_trial=p.get("is_trial",False)
 if type(is_trial) is not bool:raise ValueError("نوع پلن تست باید درست یا نادرست باشد")
 if is_trial:price=0
 ident=p.get("id")
 values=(name,category,proto,price,days,gb,json.dumps(ids),capacity,int(active),int(is_trial))
 if ident is not None:
  ident=integer(ident,1,2**31-1)
  current=c.execute("SELECT * FROM plans WHERE id=?",(ident,)).fetchone()
  if not current:raise ValueError("پلن برای ویرایش پیدا نشد")
  if bool(current["is_trial"])!=is_trial and c.execute(
    "SELECT 1 FROM orders WHERE json_extract(plan,'$.id')=? LIMIT 1",(ident,)).fetchone():
   raise ValueError("نوع تست/عادی پلنی که سابقه سفارش دارد قابل تغییر نیست")
  sold=c.execute("""SELECT COUNT(*) FROM orders WHERE json_extract(plan,'$.id')=?
   AND renew_id='' AND status!='cancelled'""",(ident,)).fetchone()[0]
  if capacity and sold>capacity:raise ValueError("ظرفیت جدید از تعداد سفارش‌های ثبت‌شده کمتر است")
  c.execute("""UPDATE plans SET name=?,category=?,protocol=?,price=?,days=?,gb=?,
   inbounds=?,capacity=?,active=?,is_trial=? WHERE id=?""",(*values,ident))
 else:
  c.execute("""INSERT INTO plans(name,category,protocol,price,days,gb,
   inbounds,capacity,active,is_trial) VALUES(?,?,?,?,?,?,?,?,?,?)""",values)
  ident=c.execute("SELECT last_insert_rowid()").fetchone()[0]
 audit(c,"plan_saved",str(ident))
 return ident

def admin(req):
 with conn() as c:
  c.execute("BEGIN IMMEDIATE")
  a=req.get("action");s=settings(c)
  if a=="get":
   safe={k:v for k,v in s.items() if k in DEFAULT and k not in ("token","merchant_id")};safe["token_set"]=bool(s["token"]);safe["merchant_set"]=bool(s["merchant_id"])
   return {"settings":safe,"plans":plan_overview(c),"users":[dict(r) for r in c.execute("SELECT id,name,balance,blocked,reseller,phone,created FROM users ORDER BY created DESC LIMIT 500")],"orders":[dict(r) for r in c.execute("SELECT id,user_id,price,status,created FROM orders ORDER BY created DESC LIMIT 200")],"topups":[dict(r) for r in c.execute("SELECT * FROM topups ORDER BY id DESC LIMIT 200")],"coupons":[dict(r) for r in c.execute("SELECT * FROM coupons")],"tickets":[dict(r) for r in c.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 100")],"ledger":[dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 200")],"runtime":{"heartbeat":s.get("heartbeat",0),"error":s.get("last_error",""),"username":s.get("bot_username","")},"payments":[dict(r) for r in c.execute("SELECT id,user_id,amount,status,ref,created FROM payments ORDER BY created DESC LIMIT 200")],"revenue":c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='fulfilled'").fetchone()[0],"outbox_failed":[dict(r) for r in c.execute("SELECT id,chat,method,attempts FROM outbox WHERE done=-1 ORDER BY id DESC LIMIT 50")],"outbox_pending":c.execute("SELECT COUNT(*) FROM outbox WHERE done=0").fetchone()[0]}
  if a=="settings":return save_settings(c,req["values"])
  if a=="test":
   me=tg(s,"getMe",{});hook=tg(s,"getWebhookInfo",{})
   if hook.get("url"):tg(s,"deleteWebhook",{"drop_pending_updates":False})
   put(c,"bot_username",me["username"]);return {"username":me["username"]}
  if a=="inbounds":return inbound_list()
  if a=="plan":
   save_plan(c,req.get("values"))
  elif a=="plan_toggle":
   ident=integer(req.get("id"),1,2**31-1)
   enabled=req.get("active")
   if type(enabled) is not bool:raise ValueError("وضعیت پلن نامعتبر است")
   if not c.execute("UPDATE plans SET active=? WHERE id=?",(int(enabled),ident)).rowcount:
    raise ValueError("پلن پیدا نشد")
  elif a=="plan_delete":
   ident=integer(req.get("id"),1,2**31-1)
   if not c.execute("SELECT 1 FROM plans WHERE id=?",(ident,)).fetchone():
    raise ValueError("پلن پیدا نشد")
   if c.execute("SELECT 1 FROM orders WHERE json_extract(plan,'$.id')=? LIMIT 1",(ident,)).fetchone():
    raise ValueError("این پلن سابقه سفارش دارد؛ به‌جای حذف، آن را غیرفعال کنید")
   c.execute("DELETE FROM quotes WHERE plan_id=?",(ident,))
   c.execute("DELETE FROM plans WHERE id=?",(ident,))
  elif a=="user":
   uid=integer(req["id"],1)
   c.execute("UPDATE users SET blocked=?,reseller=? WHERE id=?",(int(bool(req.get("blocked"))),int(bool(req.get("reseller"))),uid))
  elif a=="credit":
   money(c,integer(req["id"],1),integer(req["amount"],-10**12),text(req.get("reason","admin")),text(req["reference"],100))
  elif a=="topup":
   decide_receipt(c,integer(req["id"],1),req.get("approve") is True)
  elif a=="coupon":
   v=req["values"];code=text(v["code"],40).upper()
   if not re.fullmatch("[A-Z0-9_-]{3,40}",code):raise ValueError("کد فقط حروف انگلیسی و عدد")
   c.execute("INSERT INTO coupons VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,credit=excluded.credit,uses=excluded.uses,expires=excluded.expires",(code,integer(v.get("percent",0),0,100),integer(v.get("credit",0)),integer(v["uses"],0),integer(v.get("expires",0))))
  elif a=="reply":
   r=c.execute("SELECT * FROM tickets WHERE id=?",(integer(req["id"],1),)).fetchone()
   if not r:raise ValueError("تیکت یافت نشد")
   body=text(req["body"]);c.execute("UPDATE tickets SET reply=? WHERE id=?",(body,r["id"]));enqueue(c,r["user_id"],"پاسخ پشتیبانی: "+body)
  elif a=="broadcast":
   body=text(req["body"]);ref=text(req["reference"],100)
   for u in c.execute("SELECT id FROM users WHERE blocked=0"):enqueue(c,u["id"],body,key="broadcast:"+ref+":"+str(u["id"]))
  elif a=="retry":
   c.execute("UPDATE orders SET status='paid' WHERE id=? AND status='review'",(text(req["id"]),))
  elif a=="cancel_order":
   ident=text(req.get("id"),120)
   o=c.execute("SELECT * FROM orders WHERE id=?",(ident,)).fetchone()
   if not o or o["status"] not in ("awaiting_receipt","receipt_review"):
    raise ValueError("فقط سفارش در انتظار رسید قابل لغو است")
   restore_reserved_coupon(c,o)
   linked=c.execute("SELECT topup_id FROM receipt_orders WHERE order_id=?",(ident,)).fetchone()
   if linked:
    c.execute("UPDATE topups SET status='rejected' WHERE id=? AND status='pending'",(linked["topup_id"],))
   c.execute("UPDATE orders SET status='cancelled' WHERE id=?",(ident,))
   enqueue(c,o["user_id"],"سفارش در انتظار رسید لغو شد؛ در صورت نیاز دوباره خرید کنید.",
     key="cancel:"+ident)
  elif a=="retry_outbox":
   # Admin-only local panel RPC. Message payload stays private in SQLite.
   ident=integer(req["id"],1)
   if not c.execute("UPDATE outbox SET done=0,attempts=0,next_try=0 WHERE id=? AND done=-1",(ident,)).rowcount:
    raise ValueError("پیام ناموفق برای تلاش مجدد یافت نشد")
  else:raise ValueError("عملیات نامعتبر")
  audit(c,a,"admin operation")
 return True
def gateway(method,payload):
 try:
  req=urllib.request.Request("https://payment.zarinpal.com/pg/v4/payment/"+method+".json",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","Accept":"application/json"})
  with urllib.request.urlopen(req,timeout=20) as r:return json.load(r).get("data") or {}
 except Exception:raise ValueError("ارتباط با درگاه ناموفق است")
def payment_create(c,uid,amount,reference):
 s=settings(c)
 if not s["zarinpal"] or not s["wallet"]:raise ValueError("درگاه غیرفعال است")
 amount=integer(amount,1000,10**9)
 row=c.execute("SELECT * FROM payments WHERE id=?",(reference,)).fetchone()
 if row:return row["authority"]
 r=gateway("request",{"merchant_id":s["merchant_id"],"amount":amount*10,"currency":"IRR","description":"Masiha wallet "+reference,"callback_url":PUBLIC_PANEL_URL+"/sales-payment"})
 if r.get("code")!=100 or not re.fullmatch(r"[A-Za-z0-9]{10,100}",r.get("authority","")):raise ValueError("درگاه درخواست را نپذیرفت")
 c.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?)",(reference,uid,amount,r["authority"],"pending","",now()))
 return r["authority"]
def payment_verify(authority):
 if not isinstance(authority,str) or not re.fullmatch(r"[A-Za-z0-9]{10,100}",authority):raise ValueError("شناسه پرداخت معتبر نیست")
 with conn() as c:
  c.execute("BEGIN IMMEDIATE")
  p=c.execute("SELECT * FROM payments WHERE authority=?",(authority,)).fetchone()
  if not p:raise ValueError("پرداخت یافت نشد")
  if p["status"]=="paid":return "این پرداخت قبلاً تأیید و موجودی شارژ شده است."
  s=settings(c);r=gateway("verify",{"merchant_id":s["merchant_id"],"amount":p["amount"]*10,"authority":authority})
  if r.get("code") not in (100,101):raise ValueError("پرداخت تأیید نشد؛ در صورت کسر وجه با پشتیبانی تماس بگیرید")
  money(c,p["user_id"],p["amount"],"zarinpal","payment:"+p["id"])
  c.execute("UPDATE payments SET status='paid',ref=? WHERE id=?",(str(r.get("ref_id","")),p["id"]))
  enqueue(c,p["user_id"],"پرداخت تأیید شد و کیف پول "+str(p["amount"])+" تومان شارژ شد.",[[("خرید اشتراک","plans")]],"payment:"+p["id"]+":notice")
  return "پرداخت تأیید شد. به ربات تلگرام برگردید و اشتراک را انتخاب کنید."
if __name__=="__main__":
 os.umask(0o077)
 try:
  request=json.loads(sys.stdin.read(65537))
  result=payment_verify(request.get("authority")) if len(sys.argv)>1 and sys.argv[1]=="payment" else admin(request)
  print(json.dumps({"success":True,"obj":result},ensure_ascii=False))
 except Exception as e:print(json.dumps({"success":False,"msg":str(e)},ensure_ascii=False))
