#!/usr/bin/python3
"""Idempotent loyalty points, onboarding referrals, and safe AI-provider billing.

Units: currency amounts are TOMAN, points are whole integers. Exactly-once
awards enforced by UNIQUE ledger reference, never by UI state or timestamps.
"""
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

def setup(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS loyalty_points (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  points INTEGER NOT NULL,
  kind TEXT NOT NULL,
  reference TEXT NOT NULL UNIQUE,
  created INTEGER NOT NULL
 );
 CREATE INDEX IF NOT EXISTS loyalty_points_user ON loyalty_points(user_id,id);
 CREATE TABLE IF NOT EXISTS ai_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  reference TEXT NOT NULL UNIQUE,
  model TEXT NOT NULL,
  points INTEGER NOT NULL,
  created INTEGER NOT NULL
 );
 """)

def balance(c,uid):
 row=c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=?",(uid,)).fetchone()
 return int(row[0])

def award(c,uid,points,kind,reference,now):
 if not isinstance(points,int) or not 0<=points<=10**9:
  raise ValueError("Invalid points amount")
 if not points:return False
 if not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone():
  return False
 result=c.execute("INSERT OR IGNORE INTO loyalty_points(user_id,points,kind,reference,created) VALUES(?,?,?,?,?)",
                  (uid,points,kind,reference,now))
 return result.rowcount>0

def purchase(c,order,settings,now):
 """Grant points only for paid, fulfilled, non-trial orders."""
 if order["status"]!="fulfilled" or order["trial"] or order["price"]<=0:return False
 unit=settings["points_per_toman"]
 reward=settings["points_per_purchase_unit"]
 # Award complete units only; no point multiplication from wallet topups.
 if unit<=0 or reward<=0:return False
 pts=(int(order["price"])//unit)*reward
 return award(c,order["user_id"],pts,"purchase","purchase:"+order["id"],now)

def referral(c,new_user_id,settings,now):
 """One inviter award after first successful onboarding; never for self-invites."""
 if not settings.get("referrals"):return False
 record=c.execute("SELECT id,referrer FROM users WHERE id=?",(new_user_id,)).fetchone()
 if not record or not record["referrer"] or record["referrer"]==new_user_id:return False
 reward=settings["points_per_referral"]
 if reward<=0:return False
 return award(c,record["referrer"],reward,"referral","referral:"+str(new_user_id),now)

def redeem(c,uid,points,reference,now):
 if not isinstance(points,int) or points<=0:raise ValueError("AI usage cost must be positive")
 if not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone():raise ValueError("کاربر وجود ندارد")
 if c.execute("SELECT 1 FROM loyalty_points WHERE reference=?",(reference,)).fetchone():
  return False
 if balance(c,uid)<points:raise ValueError("امتیاز کافی ندارید")
 return award(c,uid,-points,"ai",reference,now) if False else _debit(c,uid,points,reference,now)

def _debit(c,uid,points,reference,now):
 c.execute("INSERT INTO loyalty_points(user_id,points,kind,reference,created) VALUES(?,?,?,?,?)",
           (uid,-points,"ai",reference,now))
 return True

def _is_public_host(url):
 parsed=urllib.parse.urlparse(url)
 if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
  raise ValueError("AI API must be a public HTTPS endpoint")
 host=parsed.hostname.lower()
 if host in ("localhost","metadata.google.internal") or host.endswith((".local",".internal",".localhost")):
  raise ValueError("Private AI API hosts are not allowed")
 try:
  addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
 except socket.gaierror as e:
  raise ValueError("AI API hostname could not be resolved") from e
 for a in addresses:
  ip=ipaddress.ip_address(a[4][0])
  if not ip.is_global:raise ValueError("AI API must resolve only to public IP addresses")
 return url

def query_ai(config,prompt):
 """OpenAI-compatible Chat Completions endpoint. API key never enters Telegram."""
 url=_is_public_host(config["ai_api_url"])
 if not url.rstrip("/").endswith("/chat/completions"):
  url=url.rstrip("/")+"/chat/completions"
 if not config.get("ai_api_key") or not config.get("ai_model"):
  raise ValueError("اتصال هوش مصنوعی هنوز از پنل مدیریت تنظیم نشده است")
 if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=1600:
  raise ValueError("پیام باید بین ۱ تا ۱۶۰۰ کاراکتر باشد")
 data=json.dumps({"model":config["ai_model"],"messages":[
  {"role":"system","content":"You are a concise, safe Persian-language assistant. Do not request secrets."},
  {"role":"user","content":prompt.strip()}],"max_tokens":350}).encode()
 req=urllib.request.Request(url,data=data,headers={
  "Authorization":"Bearer "+config["ai_api_key"],
  "Content-Type":"application/json",
  "Accept":"application/json"
 },method="POST")
 # Disable proxies and redirects: redirect to private infrastructure must not
 # forward the API secret. urllib may follow redirects by default.
 class RejectRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):
   raise ValueError("AI API redirects are not allowed")
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),RejectRedirect())
 try:
  with opener.open(req,timeout=30) as reply:
   if reply.status!=200:raise ValueError("سرویس هوش مصنوعی موقتاً در دسترس نیست")
   payload=json.loads(reply.read(50000))
   answer=payload["choices"][0]["message"]["content"]
   if not isinstance(answer,str) or not answer.strip():raise ValueError("پاسخ هوش مصنوعی نامعتبر است")
   return answer[:3500]
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,KeyError,IndexError,ValueError,TypeError) as exc:
  # Deliberately avoid leaking provider response/API key into Telegram.
  raise ValueError("ارتباط با سرویس هوش مصنوعی موفق نبود؛ امتیازی کسر نشد") from None
