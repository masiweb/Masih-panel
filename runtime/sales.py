#!/usr/bin/python3
"""Masiha sales: private local administration and durable Telegram commerce."""
import contextlib, fcntl, hashlib, http.client, http.cookiejar, io, json, os, re, secrets, shlex, socket, sqlite3, subprocess, sys, time, urllib.request, urllib.error, urllib.parse, uuid, unicodedata
from pathlib import Path
from runtime_config import DOMAIN, PUBLIC_PANEL_URL
import gamification as loyalty
BASE=Path(os.environ.get("MASIHA_SALES_DIR","/etc/masiha-vpn-extension/sales"))
MESSAGE_DEFAULTS={
 "home":"به فروشگاه مسیحا خوش آمدید",
 "trial_menu":"پلن تست رایگان خود را انتخاب کنید؛ هیچ پرداختی لازم نیست.",
 "trial_empty":"در حال حاضر پلن تست فعالی وجود ندارد.",
 "trial_confirm":"تست رایگان ثبت شد و بدون پرداخت در حال فعال‌سازی است.",
 "trial_used":"شما قبلاً تست رایگان دریافت کرده‌اید.",
 "plans_menu":"پلن موردنظر را انتخاب کنید.",
 "plans_empty":"فعلاً پلنی برای فروش وجود ندارد.",
 "service_ready":"اشتراک «{name}» آماده است. از «سرویس‌های من» دریافت کنید.",
 "contact_prompt":"برای ادامه، شماره تلفن متعلق به حساب تلگرام خودتان را با دکمه زیر ارسال کنید.",
 "contact_saved":"شماره تماس شما تأیید شد. می‌توانید از منوی ربات استفاده کنید.",
 "support_prompt":"برای پشتیبانی پیام خود را ارسال کنید.",
 "support_saved":"پیام پشتیبانی ثبت شد.",
 "receipt_saved":"رسید ثبت شد؛ پس از تأیید مدیر موجودی شارژ می‌شود.",
 "order_confirm":"سفارش ثبت شد؛ در حال ساخت اشتراک.",
 "wallet_balance":"موجودی: {balance} تومان",
 "welcome_admin":"پنل مدیریت ربات: گزینه مدنظر را انتخاب کنید.",
 "admin_saved":"تغییرات با موفقیت ذخیره شد.",
 "coupon_saved":"کد تخفیف شما ثبت شد."
}
def bot_message(c,key,**values):
 if key not in MESSAGE_DEFAULTS:raise ValueError("شناسه متن ربات نامعتبر است")
 row=c.execute("SELECT body FROM message_templates WHERE id=?",(key,)).fetchone()
 source=row["body"] if row else MESSAGE_DEFAULTS[key]
 try:return source.format_map(values)
 except (KeyError,ValueError,AttributeError,IndexError):
  return MESSAGE_DEFAULTS[key].format_map(values)

DEFAULT={"enabled":False,"token":"","admins":[],"welcome":"به فروشگاه مسیحا خوش آمدید","support":"برای پشتیبانی پیام خود را ارسال کنید.","card":"","channel":"","require_contact":False,"buy":True,"renew":True,"wallet":True,"transfer":False,"trial":False,"referrals":False,"commission":5,"discounts":True,"notifications":True,"self_delete":False,"reseller":False,"reseller_discount":10,"trial_plan":0,"warning_days":3,"usage_warning_percent":10,"report_channel":"","bulk":False,"rename":True,"rotate":False,"new_user_notice":True,"zarinpal":False,"merchant_id":"","points_per_toman":100000,"points_per_purchase_unit":10,"points_per_referral":20,"ai_enabled":False,"ai_cost":5,"ai_api_url":"","ai_api_key":"","ai_model":""}
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
 CREATE TABLE IF NOT EXISTS message_templates(id TEXT PRIMARY KEY,body TEXT NOT NULL);
 """)
 loyalty.setup(c)
 # Media database stores only Telegram file IDs and text metadata, never bytes.
 import media_library
 media_library.setup(c)
 import social
 social.setup(c)
 # Backward-compatible additive migration; existing plans and orders survive.
 # Telegram usernames are optional and can change; update on later messages.
 if not any(col[1]=="telegram_username" for col in c.execute("PRAGMA table_info(users)")):
  try:c.execute("ALTER TABLE users ADD COLUMN telegram_username TEXT NOT NULL DEFAULT ''")
  except sqlite3.OperationalError as exc:
   if "duplicate column name" not in str(exc).lower():raise
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
def enqueue(c,chat,body,keys=None,key=None,quick_nav=True):
 """Always preserve navigation; text-entry stages show only a Back control."""
 p={"chat_id":chat,"text":body[:4000]}
 buttons=[list(row) for row in (keys or [])]
 user=c.execute("SELECT state FROM users WHERE id=?",(chat,)).fetchone() if type(chat) is int else None
 if user:
  state=user["state"] or ""
  callbacks={callback for row in buttons for label,callback in row}
  if state:
   # Waiting for a text answer: no full navigation row, only cancel/back.
   back=("↩️ بازگشت به مدیریت","a:home") if state.startswith("a:") else (
       ("↩️ بازگشت به سرگرمی","fun") if state.startswith("ai:") else ("↩️ انصراف / منوی اصلی","home"))
   if back[1] not in callbacks and not any(v in callbacks for v in ("a:home","home","fun")):
    buttons.append([back])
  elif quick_nav:
   # On regular replies preserve quick navigation; the main menu itself
   # intentionally has exactly the user's requested compact button order.
   quick=[(label,callback) for label,callback in [
      ("🏠 منوی اصلی","home"),("🎮 سرگرمی","fun"),("⭐ امتیازها","fun:points")
   ] if callback not in callbacks]
   if quick:buttons.append(quick)
 if buttons:p["reply_markup"]={"inline_keyboard":[[{"text":a,"callback_data":b} for a,b in row] for row in buttons]}
 c.execute("INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(key or secrets.token_hex(12),chat,"sendMessage",json.dumps(p,ensure_ascii=False)))
def needs_own_phone(user):
 """A Telegram user must have a saved own-contact phone when the gate is on."""
 return not user or not str(user["phone"] or "").strip()

def prompt_for_own_contact(c,uid,dedupe=None):
 """Persistent native Telegram share-contact keyboard, not a text phone field."""
 payload={"chat_id":uid,"text":bot_message(c,"contact_prompt"),
          "reply_markup":{"keyboard":[[{"text":"📱 ارسال شماره حساب تلگرام من",
                                        "request_contact":True}]],
                          "resize_keyboard":True,"one_time_keyboard":False,
                          "is_persistent":True}}
 key=dedupe or ("phone-gate:prompt:"+str(uid)+":"+str(now()//60))
 c.execute("INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
           (key,uid,"sendMessage",json.dumps(payload,ensure_ascii=False)))

def request_missing_phones(c,campaign=None):
 """One-time message to pre-existing members, without changing their account data."""
 marker=campaign or secrets.token_hex(10)
 users=c.execute("""SELECT id FROM users WHERE id>0 AND blocked=0
                   AND TRIM(COALESCE(phone,''))='' ORDER BY id""").fetchall()
 for row in users:
  uid=row["id"]
  prompt_for_own_contact(c,uid,"phone-gate:rollout:"+marker+":"+str(uid))
 return len(users)

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
def _telegram_connect(address,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None):
 """Prefer IPv4 when resolving Telegram, falling back to working IPv6.

 Some hosts have an AAAA DNS record but no usable IPv6 route. Python's
 default serial connect loses ~2.6 seconds on IPv6 before trying IPv4.
 This applies only to Telegram connections, never VPN tunnel sockets.
 """
 host,port=address
 addresses=socket.getaddrinfo(host,port,0,socket.SOCK_STREAM)
 addresses.sort(key=lambda item:0 if item[0]==socket.AF_INET else 1)
 last=None
 for family,kind,proto,_,remote in addresses:
  sock=socket.socket(family,kind,proto)
  try:
   if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:sock.settimeout(timeout)
   if source_address:sock.bind(source_address)
   sock.connect(remote)
   return sock
  except OSError as exc:
   last=exc
   sock.close()
 raise last if last else OSError("Unable to connect to Telegram API")

class _TelegramHTTPSHandler(urllib.request.HTTPSHandler):
 def https_open(self,req):
  def connection(host,**kwargs):
   conn=http.client.HTTPSConnection(host,**kwargs)
   conn._create_connection=_telegram_connect
   return conn
  return self.do_open(connection,req,context=self._context)

class _TelegramNoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  raise urllib.error.HTTPError(req.full_url,code,"Telegram API redirects not allowed",headers,fp)

_TELEGRAM_OPENER=urllib.request.build_opener(
 urllib.request.ProxyHandler({}),_TelegramHTTPSHandler(),_TelegramNoRedirect())

def telegram_open(req,timeout=35):
 """Open only genuine TLS Telegram Bot API requests with IPv4-first fallback."""
 target=urllib.parse.urlsplit(req.full_url)
 if target.scheme!="https" or target.hostname!="api.telegram.org" or target.port not in (None,443):
  raise ValueError("Invalid Telegram API endpoint")
 return _TELEGRAM_OPENER.open(req,timeout=timeout)

def tg(s,method,payload):
 try:
  req=urllib.request.Request("https://api.telegram.org/bot"+s["token"]+"/"+method,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
  with telegram_open(req,timeout=35) as r:obj=json.load(r)
  if not obj.get("ok"):raise ValueError()
  return obj["result"]
 except Exception:raise ValueError("ارتباط با تلگرام ناموفق است؛ توکن، شبکه و وضعیت webhook را بررسی کنید")
def normalize_channel(value):
 """Normalize public Telegram usernames, t.me links, or numeric channel IDs.

 Telegram Bot API getChatMember does not resolve a bare channel handle: the
 leading @ is mandatory. Never silently bypass mandatory membership.
 """
 value=text(value,160)
 if not value:return ""
 if value.startswith("https://t.me/"):
  value=value[len("https://t.me/"):].strip("/")
 if re.fullmatch(r"-100[0-9]{6,18}",value):
  return value
 if re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{4,31}",value):
  return "@"+value.lstrip("@")
 raise ValueError("شناسه کانال باید به صورت @channelname یا https://t.me/channelname وارد شود")

def telegram_channel_member(s,channel,uid):
 """Check membership without misclassifying unknown nonmembers as API outages.

 When the bot is a channel admin, Telegram can answer HTTP 400 for a non-member
 whose user ID is not known in that chat. This is NOT a bot token failure.
 """
 payload={"chat_id":normalize_channel(channel),"user_id":integer(uid,1,2**52)}
 req=urllib.request.Request("https://api.telegram.org/bot"+s["token"]+"/getChatMember",
     data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
 try:
  with telegram_open(req,timeout=20) as response:
   data=json.load(response)
 except urllib.error.HTTPError as exc:
  try: data=json.load(exc)
  except (ValueError,UnicodeDecodeError):
   raise RuntimeError("پاسخ نامعتبر هنگام بررسی عضویت کانال") from None
 except Exception as exc:
  raise RuntimeError("ارتباط موقت با تلگرام هنگام بررسی عضویت کانال ناموفق بود") from exc
 if data.get("ok"):
  result=data.get("result") or {}
  status=result.get("status")
  return status in ("creator","administrator","member") or (
    status=="restricted" and result.get("is_member") is True)
 code=data.get("error_code")
 message=str(data.get("description","")).lower()
 if code==400 and any(x in message for x in (
     "participant_id_invalid","user not found",
     "invalid user identifier","user_id_invalid","user_id is invalid")):
  return False
 if "not enough rights" in message or "chat_admin_required" in message:
  raise ValueError("ربات برای کنترل عضویت باید در کانال مدیر باشد؛ تنظیمات کانال را بررسی کنید")
 if code==400 and "chat not found" in message:
  raise ValueError("کانال عضویت اجباری پیدا نشد؛ آیدی کانال را در پنل تنظیمات بررسی کنید")
 # An unknown Telegram server error is transient. Do not acknowledge the user
 # update and lose it: the sales worker will retry after Telegram recovers.
 raise RuntimeError("خطای موقت سرویس تلگرام هنگام بررسی عضویت کانال")

def save_settings(c,r):
 s=settings(c)
 settings_before=s.copy()
 for k,v in r.items():
  if k not in DEFAULT:continue
  if k=="token":
   if not v:continue
   if s["token"] and v.split(":")[0]!=s["token"].split(":")[0] and c.execute("SELECT 1 FROM users LIMIT 1").fetchone():raise ValueError("تغییر هویت ربات دارای مشتری مجاز نیست؛ فقط توکن جدید همان ربات را وارد کنید")
   if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}",v):raise ValueError("فرمت توکن نادرست است")
  elif k=="channel":
   v=normalize_channel(v)
   if v and s["enabled"] and v!=s["channel"]:
    # Prevent bad channel names and missing bot admin permissions from
    # silently locking out every customer on the next /start.
    channel_info=tg(s,"getChat",{"chat_id":v})
    if channel_info.get("type") not in ("channel","supergroup"):
     raise ValueError("شناسه واردشده یک کانال یا گروه معتبر نیست")
    bot=tg(s,"getMe",{})
    membership=tg(s,"getChatMember",{"chat_id":v,"user_id":bot["id"]})
    if membership.get("status") not in ("administrator","creator"):
     raise ValueError("ربات باید ابتدا مدیر کانال عضویت اجباری شود")
  elif k=="ai_api_key":
   # Never erase previously saved secrets on a regular settings save.
   if not v:continue
   if not isinstance(v,str) or not 12<=len(v)<=256 or any(ord(x)<33 or ord(x)>126 for x in v):
    raise ValueError("کلید API هوش مصنوعی نامعتبر است")
  elif k=="ai_api_url":
   if v:
    v=text(v,300)
    # Validate without contacting an untrusted host at form submission.
    parsed=urllib.parse.urlparse(v)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
     raise ValueError("API هوش مصنوعی باید آدرس HTTPS عمومی باشد")
  elif k=="ai_model":
   v=text(v,100)
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
 if s["ai_enabled"] and not all(s.get(v) for v in ("ai_api_url","ai_api_key","ai_model")):
  raise ValueError("برای فعال‌سازی AI ابتدا آدرس API، کلید و مدل را در پنل وب وارد کنید")
 if s["enabled"]:
  me=tg(s,"getMe",{});tg(s,"deleteWebhook",{"drop_pending_updates":False});put(c,"bot_username",me["username"])
 for k in DEFAULT:put(c,k,s[k])
 if s["require_contact"] and not settings_before["require_contact"]:
  request_missing_phones(c)
  put(c,"contact_onboarding_rollout_v2",True)
 elif not s["require_contact"]:
  put(c,"contact_onboarding_rollout_v2",False)
 audit(c,"settings","updated")
 return True
# Keep the account's immutable internal ID separate from its user-facing label.
# Reserving labels in quotes prevents two parallel checkout screens from
# showing the same name; finalized orders and services are checked too.
LABEL_ALPHABET="abcdefghijklmnopqrstuvwxyz0123456789"
def subscription_label(value):
 if not isinstance(value,str):raise ValueError("نام اشتراک را وارد کنید")
 value=" ".join(unicodedata.normalize("NFC",value).split())
 if not 2<=len(value)<=55 or any(ord(ch)<32 or ord(ch)==127 for ch in value):
  raise ValueError("نام اشتراک باید بین ۲ تا ۵۵ کاراکتر باشد")
 if not all(ch.isalnum() or ch in (" ","-","_","‌") for ch in value):
  raise ValueError("در نام اشتراک فقط حروف، اعداد، فاصله، خط تیره و زیرخط مجاز است")
 return value

def label_taken(c,label):
 needle=label.casefold()
 # 'casefold' works for Persian and Latin; sqlite NOCASE covers ASCII only.
 for row in c.execute("SELECT data FROM services WHERE deleted=0"):
  try: existing=json.loads(row["data"]).get("label","")
  except (TypeError,ValueError):continue
  if isinstance(existing,str) and existing.casefold()==needle:return True
 for row in c.execute("SELECT plan FROM orders WHERE status!='cancelled'"):
  try: existing=json.loads(row["plan"]).get("_display_name","")
  except (TypeError,ValueError):continue
  if isinstance(existing,str) and existing.casefold()==needle:return True
 for row in c.execute("SELECT plan FROM quotes WHERE expires>?",(now(),)):
  try: existing=json.loads(row["plan"]).get("label","")
  except (TypeError,ValueError):continue
  if isinstance(existing,str) and existing.casefold()==needle:return True
 return False

def unique_subscription_label(c,preferred):
 name=subscription_label(preferred)
 if not label_taken(c,name):return name
 # Always generate five secure alphanumeric characters on collision.
 for _ in range(64):
  candidate=name[:54]+"-"+"".join(secrets.choice(LABEL_ALPHABET) for _ in range(5))
  if not label_taken(c,candidate):return candidate
 raise ValueError("نام اشتراک تکراری است؛ لطفاً نام دیگری انتخاب کنید")

def order(c,uid,pid,reference,renew="",trial=False,card=False,label=None):
 existing=c.execute("SELECT id FROM orders WHERE id=?",(reference,)).fetchone()
 if existing:
  if trial:raise ValueError(bot_message(c,"trial_used"))
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
  if c.execute("SELECT 1 FROM orders WHERE user_id=? AND trial=1",(uid,)).fetchone():raise ValueError(bot_message(c,"trial_used"))
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
 if not renew:
  # Checkout has already reserved the chosen name in its quote. The paid
  # order stores it as an immutable snapshot for delayed receipt approvals.
  p["_display_name"]=subscription_label(label) if label else unique_subscription_label(c,p["name"])
 else:
  existing_service=c.execute("SELECT data FROM services WHERE id=?",(renew,)).fetchone()
  if existing_service:
   p["_display_name"]=json.loads(existing_service["data"]).get("label",p["name"])
 c.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",(reference,uid,json.dumps(p),price,"awaiting_receipt" if card else "paid",sid,renew,now(),"",int(trial)))
 return reference
def provision(c,o):
 p=json.loads(o["plan"]);uid=o["user_id"];sid=o["service_id"];name="ms-"+sid[:20]
 old=c.execute("SELECT * FROM services WHERE id=?",(sid,)).fetchone()
 # Persist absolute targets before contacting the VPN so retries cannot extend twice.
 data=json.loads(old["data"]) if old else {"uuid":str(uuid.uuid4()),"password":secrets.token_urlsafe(24),"subId":secrets.token_urlsafe(18)}
 if not old:data["label"]=p.get("_display_name") or p["name"]
 elif not data.get("label"):data["label"]=p.get("_display_name") or p["name"]
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
  client={"email":name,"id":data["uuid"],"password":data["password"],"auth":data["password"],"security":"auto","flow":"","subId":data["subId"],"enable":True,"expiryTime":target*1000,"totalGB":data["totalGB"],"tgId":uid,"limitIp":0,"comment":"Masiha sales | "+data["label"][:55]}
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
 s=settings(c)
 loyalty.purchase(c,dict(o)|{"status":"fulfilled"},s,now())
 u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if s["referrals"] and u["referrer"] and o["price"]:
  money(c,u["referrer"],o["price"]*s["commission"]//100,"commission",o["id"]+":commission")
 enqueue(c,uid,bot_message(c,"service_ready",name=data["label"]),[[("دریافت سرویس","service:"+sid)]],o["id"]+":delivered")
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
 if is_trial and active and not settings(c)["trial"]:
  # Creating an ACTIVE trial plan must make the bot trial menu available.
  # Administrators can explicitly switch trials off afterward from settings.
  put(c,"trial",True)
 audit(c,"plan_saved",str(ident))
 return ident

def admin(req):
 with conn() as c:
  c.execute("BEGIN IMMEDIATE")
  a=req.get("action");s=settings(c)
  if a=="get":
   safe={k:v for k,v in s.items() if k in DEFAULT and k not in ("token","merchant_id","ai_api_key")};safe["token_set"]=bool(s["token"]);safe["merchant_set"]=bool(s["merchant_id"]);safe["ai_api_key_set"]=bool(s.get("ai_api_key"))
   return {"settings":safe,"plans":plan_overview(c),"users":[dict(r) for r in c.execute("SELECT id,name,balance,blocked,reseller,phone,created FROM users ORDER BY created DESC LIMIT 500")],"orders":[dict(r) for r in c.execute("SELECT id,user_id,price,status,created FROM orders ORDER BY created DESC LIMIT 200")],"topups":[dict(r) for r in c.execute("SELECT * FROM topups ORDER BY id DESC LIMIT 200")],"coupons":[dict(r) for r in c.execute("SELECT * FROM coupons")],"tickets":[dict(r) for r in c.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 100")],"ledger":[dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 200")],"runtime":{"heartbeat":s.get("heartbeat",0),"error":s.get("last_error",""),"username":s.get("bot_username","")},"payments":[dict(r) for r in c.execute("SELECT id,user_id,amount,status,ref,created FROM payments ORDER BY created DESC LIMIT 200")],"revenue":c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='fulfilled'").fetchone()[0],"outbox_failed":[dict(r) for r in c.execute("SELECT id,chat,method,attempts FROM outbox WHERE done=-1 ORDER BY id DESC LIMIT 50")],"outbox_pending":c.execute("SELECT COUNT(*) FROM outbox WHERE done=0").fetchone()[0],"message_templates":{key:(c.execute("SELECT body FROM message_templates WHERE id=?",(key,)).fetchone() or {"body":default})["body"] for key,default in MESSAGE_DEFAULTS.items()},"message_defaults":MESSAGE_DEFAULTS,"loyalty":{"total_users_with_points":c.execute("SELECT COUNT(DISTINCT user_id) FROM loyalty_points").fetchone()[0],"total_awarded":c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE points>0").fetchone()[0],"recent":[dict(r) for r in c.execute("SELECT user_id,points,kind,created FROM loyalty_points ORDER BY id DESC LIMIT 30")]},"media":{"total_music":c.execute("SELECT COUNT(*) FROM media_items WHERE kind='music' AND status='published'").fetchone()[0],"total_films":c.execute("SELECT COUNT(*) FROM media_items WHERE kind='film' AND status='published'").fetchone()[0],"playlists":c.execute("SELECT COUNT(*) FROM playlists WHERE removed=0").fetchone()[0],"field_points":c.execute("SELECT v FROM media_settings WHERE k='field_points'").fetchone()[0],"like_points":c.execute("SELECT v FROM media_settings WHERE k='like_points'").fetchone()[0], "social_rewards":{k:c.execute("SELECT v FROM media_settings WHERE k=?",(k,)).fetchone()[0] for k in __import__("social").DEFAULT_POINTS}, "broadcast_music":bool(c.execute("SELECT v FROM media_settings WHERE k='broadcast_music'").fetchone()[0]),"broadcast_movie":bool(c.execute("SELECT v FROM media_settings WHERE k='broadcast_movie'").fetchone()[0]),"broadcast_series":bool(c.execute("SELECT v FROM media_settings WHERE k='broadcast_series'").fetchone()[0]),"reports":[dict(r) for r in c.execute("SELECT r.id,r.media_id,r.reporter_id,r.reason,r.created,m.title FROM media_reports r JOIN media_items m ON m.id=r.media_id WHERE r.status='pending' ORDER BY r.id DESC LIMIT 40")]}}
  if a=="social_admin":
   import social
   return social.admin_view(c,req)
  if a=="social_adjust":
   import social
   return social.adjust_account(c,req.get("code"),req.get("operation"),
     req.get("amount"),req.get("request_id"),req.get("note",""),manager="web-admin")
  if a=="social_report":
   ident=integer(req.get("id"),1,2**31-1)
   decision=req.get("decision")
   if decision not in ("resolved","block"):raise ValueError("وضعیت گزارش نامعتبر است")
   row=c.execute("SELECT target FROM social_reports WHERE id=? AND status='pending'",
                 (ident,)).fetchone()
   if not row:raise ValueError("گزارش پیدا نشد یا قبلاً بررسی شده است")
   if decision=="block":
    c.execute("UPDATE users SET blocked=1 WHERE id=?",(row["target"],))
    c.execute("DELETE FROM social_chat_queue WHERE user_id=?",(row["target"],))
    c.execute("""UPDATE social_chat_pairs SET closed=? WHERE closed=0 AND
      (first_user=? OR second_user=?)""",(now(),row["target"],row["target"]))
   c.execute("UPDATE social_reports SET status=? WHERE id=?",(decision,ident))
   audit(c,"social_report_"+decision,str(ident))
   return True
  if a=="media_settings":
   val=req.get("values",{})
   toggle_keys={"broadcast_music","broadcast_movie","broadcast_series"}
   import social
   numeric_keys={"field_points","like_points","social_chat_retention_days"}|set(social.DEFAULT_POINTS)|set(social.CHAT_COST_DEFAULTS)
   if not isinstance(val,dict) or not val or set(val)-(toggle_keys|numeric_keys):
    raise ValueError("تنظیمات رسانه نامعتبر است")
   for field,value in val.items():
    if field in toggle_keys:
     if type(value) is not bool:raise ValueError("کلیدهای اعلان فقط روشن یا خاموش هستند")
     c.execute("UPDATE media_settings SET v=? WHERE k=?",(int(value),field))
     if not value:
      # Disabling cancels NOT-YET-SENT media notifications of this type.
      genre=field[len("broadcast_"):]
      c.execute("""UPDATE outbox SET done=2,payload='{}'
        WHERE done=0 AND dedupe LIKE ?""",("media-broadcast:"+genre+":%",))
    else:
     ceiling=365 if field=="social_chat_retention_days" else 10000 if field in (set(social.DEFAULT_POINTS)|set(social.CHAT_COST_DEFAULTS)) else 10
     floor=30 if field=="social_chat_retention_days" else 0
     c.execute("UPDATE media_settings SET v=? WHERE k=?",(integer(value,floor,ceiling),field))
   audit(c,"media_settings","moderator:"+",".join(sorted(val)))
   return True
  if a=="media_report":
   report_id=integer(req.get("id"),1,2**31-1)
   decision=req.get("decision")
   if decision not in ("remove","dismiss"):raise ValueError("عملیات گزارش نامعتبر است")
   record=c.execute("SELECT media_id FROM media_reports WHERE id=? AND status='pending'",(report_id,)).fetchone()
   if not record:raise ValueError("این گزارش بررسی شده یا وجود ندارد")
   if decision=="remove":
    c.execute("UPDATE media_items SET status='removed' WHERE id=?",(record["media_id"],))
    c.execute("UPDATE media_reports SET status='removed' WHERE media_id=? AND status='pending'",(record["media_id"],))
   else:c.execute("UPDATE media_reports SET status='dismissed' WHERE id=?",(report_id,))
   audit(c,"media_report_"+decision,str(report_id))
   return True
  if a=="settings":return save_settings(c,req["values"])
  if a=="message_templates":
   values=req.get("values")
   if not isinstance(values,dict) or not values or set(values)-set(MESSAGE_DEFAULTS):
    raise ValueError("شناسه متن‌های ربات نامعتبر است")
   allowed=re.compile(r"\{(name|balance)\}")
   for key,body in values.items():
    body=text(body,1000)
    if not body:raise ValueError("متن پاسخ نمی‌تواند خالی باشد")
    # Only safely whitelisted placeholders are allowed: no Python expressions.
    if re.sub(r"\{[^{}]*\}", "", body).find("{")>=0 or re.sub(r"\{[^{}]*\}", "",body).find("}")>=0:
     raise ValueError("قالب متن دارای آکولاد نامعتبر است")
    if any(not allowed.fullmatch(token) for token in re.findall(r"\{[^{}]*\}",body)):
     raise ValueError("فقط متغیرهای {name} و {balance} مجاز هستند")
    c.execute("INSERT INTO message_templates(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",(key,body))
   audit(c,"message_templates","web only")
   return True
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
   if enabled and c.execute("SELECT is_trial FROM plans WHERE id=?",(ident,)).fetchone()["is_trial"]:
    put(c,"trial",True)
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
