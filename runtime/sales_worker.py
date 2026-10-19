#!/usr/bin/python3
import base64, contextlib, fcntl, hashlib, io, json, os, re, secrets, time, urllib.request
from sales import *
from sales_bot_admin import main as admin_menu, callback as admin_callback, input_text as admin_text, queue_receipt_review
import entertainment
import media_library
import social
def quote(c,uid,pid,renew="",quantity=1,label=None):
 p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(pid,)).fetchone()
 if not p:raise ValueError("پلن موجود نیست")
 if p["is_trial"]:raise ValueError("پلن تست رایگان نیازی به پیش‌فاکتور یا پرداخت ندارد")
 ident=secrets.token_hex(12)
 # If label is supplied, reserve it before showing the payment options.
 # Renewals keep the already-provisioned service label unchanged.
 assigned=unique_subscription_label(c,label) if label is not None and not renew else ""
 c.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?)",(ident,uid,pid,json.dumps({"plan":dict(p),"quantity":quantity,"label":assigned}),renew,now()+900))
 return ident
def purchase_quote(c,uid,ident,card=False):
 q=c.execute("SELECT * FROM quotes WHERE id=? AND user_id=?",(ident,uid)).fetchone()
 if not q or q["expires"]<now():raise ValueError("پیش‌فاکتور منقضی شده؛ پلن را دوباره انتخاب کنید")
 p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(q["plan_id"],)).fetchone()
 snapshot=json.loads(q["plan"]);quantity=snapshot.get("quantity",1);s=settings(c)
 if not p or dict(p)!=snapshot["plan"]:raise ValueError("پلن تغییر کرده؛ قیمت و شرایط جدید را دوباره بررسی کنید")
 if p["is_trial"]:raise ValueError("پلن تست فقط از مسیر دریافت رایگان قابل فعال‌سازی است")
 if quantity>1:
  user=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
  if not s["bulk"] or not s["reseller"] or not user["reseller"]:raise ValueError("خرید انبوه نماینده غیرفعال است")
 if card and (quantity!=1 or not s["card"]):raise ValueError("پرداخت کارت‌به‌کارت برای این سفارش فعال نیست")
 if not q["renew_id"] and not snapshot.get("label"):
  raise ValueError("ابتدا نام اشتراک را انتخاب کنید")
 for index in range(quantity):
  label=snapshot.get("label") if index==0 else unique_subscription_label(c,snapshot["label"])
  order(c,uid,q["plan_id"],"quote:"+ident+":"+str(index),
        renew=q["renew_id"],card=card,label=label)
 return ident
def home(c,uid):
 """Compact user-first keyboard; managers see a single extra control."""
 if c.execute("SELECT 1 FROM social_chat_pairs WHERE closed=0 AND (first_user=? OR second_user=?)",
              (uid,uid)).fetchone() or c.execute("SELECT 1 FROM social_chat_queue WHERE user_id=?",(uid,)).fetchone():
  social._leave(c,uid)
 s=settings(c)
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 buttons=[
  [("🛒 خرید جدید","plans"),("📦 اشتراک‌های من","services")],
  [("🤖 هوش مصنوعی","fun:ai")],
  [("🎵 موزیک","fun:music"),("🎬 فیلم","fun:film")],
  [("🌐 مسیحا سوشال • پروفایل و دوستان","soc:home")],
  [("⭐ امتیاز و تخفیف","points_discount")],
  [("🎮 سرگرمی","fun"),("🛟 پشتیبانی","support")],
  [("💳 کیف پول","wallet")]
 ]
 if uid in s["admins"]:buttons.append([("⚙️ منوی مدیریت","admin")])
 welcome=(bot_message(c,"home") if c.execute(
  "SELECT 1 FROM message_templates WHERE id='home'").fetchone() else s["welcome"])
 enqueue(c,uid,welcome,buttons,quick_nav=False)

def points_and_discounts(c,uid):
 import gamification as loyalty
 conf=settings(c)
 lines=["⭐ امتیاز شما: "+format(loyalty.balance(c,uid),","),
        "🎟 کدهای تخفیف فعال:"]
 coupons=[]
 if conf["discounts"]:
  coupons=c.execute("""SELECT code,percent,credit FROM coupons
   WHERE uses>0 AND (expires=0 OR expires>?)
   AND NOT EXISTS(SELECT 1 FROM redeemed WHERE redeemed.code=coupons.code AND redeemed.user_id=?)
   ORDER BY CASE WHEN expires=0 THEN 1 ELSE 0 END,expires,code LIMIT 20""",
   (now(),uid)).fetchall()
 for cp in coupons:
  benefit=str(cp["percent"])+"٪" if cp["percent"] else format(cp["credit"],",")+" تومان اعتبار"
  lines.append("• "+cp["code"]+" — "+benefit)
 if not coupons:lines.append("کد قابل استفاده‌ای ندارید.")
 lines.extend(["","🎯 روش‌های کسب امتیاز:",
    "🛒 خرید: هر "+format(conf["points_per_toman"],",")+" تومان = "+
       str(conf["points_per_purchase_unit"])+" امتیاز",
    "👥 دعوت موفق: "+str(conf["points_per_referral"])+" امتیاز",
    "🎵 تکمیل اطلاعات رسانه و ❤️ لایک معتبر"])
 buttons=[]
 if conf["discounts"]:buttons.append([("🎟 ثبت کد تخفیف / هدیه","coupon")])
 if conf["referrals"] or conf.get("points_per_referral",0):
  buttons.append([("👥 دعوت دوستان","referral")])
 buttons.extend([[("⭐ جزئیات امتیاز","fun:points"),("🛒 خرید جدید","plans")],
                 [("🏠 منوی اصلی","home")]])
 enqueue(c,uid,"\n".join(lines),buttons)

def wallet_page(c,uid):
 conf=settings(c)
 if not conf["wallet"]:
  enqueue(c,uid,"کیف پول فعلاً غیرفعال است.",[[("🏠 منوی اصلی","home")]])
  return
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 balance=c.execute("SELECT balance FROM users WHERE id=?",(uid,)).fetchone()["balance"]
 buttons=[]
 if conf["card"] or (conf["zarinpal"] and conf["merchant_id"]):
  buttons.append([("➕ افزایش موجودی","walletadd")])
 if conf["transfer"]:buttons.append([("↗️ انتقال موجودی","transfer")])
 buttons.append([("🏠 منوی اصلی","home")])
 enqueue(c,uid,"💳 کیف پول\nموجودی: "+format(balance,",")+" تومان",buttons)

def wallet_amount_options(c,uid,amount):
 conf=settings(c)
 if not conf["wallet"]:raise ValueError("کیف پول غیرفعال است")
 amount=integer(amount,1000,10**9)
 buttons=[]
 if conf["card"]:buttons.append([("💳 کارت‌به‌کارت","walletcard:"+str(amount))])
 if conf["zarinpal"] and conf["merchant_id"]:
  buttons.append([("🌐 پرداخت آنلاین زرین‌پال","walletonline:"+str(amount))])
 if not buttons:raise ValueError("روش پرداخت فعالی وجود ندارد")
 c.execute("UPDATE users SET state=? WHERE id=?",("wallet_method:"+str(amount),uid))
 buttons.append([("↩️ کیف پول","wallet")])
 enqueue(c,uid,"مبلغ شارژ: "+format(amount,",")+" تومان\nروش پرداخت را انتخاب کنید:",buttons)

def deliver(c,uid,sid):
 r=c.execute("SELECT * FROM services WHERE id=? AND user_id=? AND deleted=0",(sid,uid)).fetchone()
 if not r:raise ValueError("سرویس پیدا نشد")
 d=json.loads(r["data"]);p=json.loads(r["plan"]);s=settings(c)
 rows=[]
 if s["renew"]:rows.append([("تمدید همین پلن","renew:"+sid)])
 if s["rename"]:rows.append([("نام دلخواه","rename:"+sid)])
 if s["self_delete"]:rows.append([("حذف اشتراک","deleteask:"+sid)])
 if r["protocol"]=="xray":
  if s["rotate"]:rows.append([("تعویض لینک و قطع لینک قبلی","rotateask:"+sid)])
  details=Panel().call("/panel/api/clients/get/"+r["username"])
  body="اشتراک: "+d.get("label",p["name"])+"\nپلن: "+p["name"]+"\nمصرف: "+str(round(details["usedTraffic"]/1024**3,2))+" GB\nانقضا: "+time.strftime("%Y-%m-%d",time.gmtime(r["expires"]))
  enqueue(c,uid,body,rows)
  links=[subscription_url(details["client"]["subId"])]
  if isinstance(links,dict):links=list(links.values())
  if isinstance(links,str):links=[links]
  for link in links:
   if isinstance(link,str) and link.startswith("https://"):enqueue(c,uid,link,[[("QR ساب","qr:"+sid)]])
  import unified
  for profile in unified.profiles(details["client"]["subId"]):
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),uid,"file",json.dumps({"filename":profile["filename"],"content":profile["content"]})))
   if profile["protocol"]=="openconnect":enqueue(c,uid,profile["content"])
  raw=Panel().call("/panel/api/clients/links/"+r["username"])
  for link in (raw if isinstance(raw,list) else [raw]):
   if isinstance(link,str):enqueue(c,uid,link)
 else:
  cfg=vpn({"action":"config","protocol":r["protocol"],"username":r["username"]})
  if r["protocol"]=="openconnect":enqueue(c,uid,cfg["content"]+"\nPassword: "+d["password"],rows)
  else:
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),uid,"file",json.dumps({"filename":cfg["filename"],"content":cfg["content"]})))
   enqueue(c,uid,d.get("label",p["name"])+"\nفایل اختصاصی اتصال شما",rows)
def handle(c,up):
 s=settings(c);q=up.get("callback_query");m=q.get("message",{}) if q else up.get("message",{})
 who=q.get("from",{}) if q else m.get("from",{});uid=who.get("id")
 if not uid or m.get("chat",{}).get("type")!="private":return
 body=m.get("text","");cmd=q.get("data","") if q else body
 new=not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone()
 ref=0
 if new and body.startswith("/start ref_"):
  with contextlib.suppress(Exception):
   code_or_legacy=body.split("ref_",1)[1].split()[0]
   candidate=(social.by_code(c,code_or_legacy) if code_or_legacy.startswith("MS-")
              else int(code_or_legacy))
   if candidate!=uid and c.execute("SELECT 1 FROM users WHERE id=?",(candidate,)).fetchone():ref=candidate
 c.execute("INSERT OR IGNORE INTO users(id,name,referrer,created,telegram_username) VALUES(?,?,?,?,?)",
  (uid,who.get("first_name","")[:100],ref,now(),str(who.get("username") or "")[:32]))
 # Telegram usernames are mutable, so keep the latest value when present.
 if "username" in who:
  c.execute("UPDATE users SET telegram_username=? WHERE id=?",
            (str(who.get("username") or "")[:32],uid))
 u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if u["blocked"]:return
 # Immutable random public ID exists from first contact with the bot.
 social.profile(c,uid)
 if body.startswith("/start soc_"):
  with contextlib.suppress(ValueError):
   social.by_code(c,body.split("soc_",1)[1].strip().split()[0])
   c.execute("UPDATE users SET state=? WHERE id=?",("soc:pendinginvite:"+
    body.split("soc_",1)[1].strip().split()[0],uid))
   u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if s["channel"] and uid not in s["admins"]:
  # An unknown or nonmember account can cause Telegram HTTP 400. The
  # membership helper maps that response to False, never "bot token failed".
  channel=normalize_channel(s["channel"])
  if not telegram_channel_member(s,channel,uid):
   join_url=("https://t.me/"+channel[1:]) if channel.startswith("@") else None
   buttons=[]
   if join_url:buttons.append([{"text":"📢 عضویت در کانال","url":join_url}])
   buttons.append([{"text":"✅ بررسی مجدد عضویت","callback_data":"home"}])
   payload={"chat_id":uid,"text":"برای شروع استفاده از ربات، ابتدا در کانال "+channel+
     " عضو شوید. سپس دکمه بررسی مجدد را بزنید.",
     "reply_markup":{"inline_keyboard":buttons}}
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
     (secrets.token_hex(12),uid,"sendMessage",json.dumps(payload,ensure_ascii=False)))
   return
 # Strict gate also covers existing members and inline callback queries.
 # Only a contact object referring to the *sending Telegram account* passes.
 if s["require_contact"] and needs_own_phone(u):
  contact=m.get("contact") if not q else None
  phone=contact.get("phone_number") if isinstance(contact,dict) else None
  normalized=re.sub(r"[ ()-]","",phone.strip()) if isinstance(phone,str) else ""
  valid_phone=(bool(re.fullmatch(r"\+?[0-9]{7,15}",normalized))
               and 7<=len(normalized.lstrip("+"))<=15)
  if (isinstance(contact,dict) and type(contact.get("user_id")) is int
      and contact["user_id"]==uid and valid_phone):
   c.execute("UPDATE users SET phone=?,state='' WHERE id=?",(normalized,uid))
   loyalty.referral(c,uid,s,now())
   enqueue(c,uid,bot_message(c,"contact_saved"))
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
     (secrets.token_hex(12),uid,"sendMessage",json.dumps(
      {"chat_id":uid,"text":"✅ شماره حساب تلگرام تأیید شد.",
       "reply_markup":{"remove_keyboard":True}},ensure_ascii=False)))
   pending=u["state"] or ""
   if pending.startswith("soc:pendinginvite:"):
    with contextlib.suppress(ValueError):
     if social.invite_start(c,uid,"/start soc_"+pending.split(":",2)[2]):
      return
   home(c,uid)
   return
  # A forged/shared contact, normal message, deep link, and every callback
  # receive the SAME mandatory self-share keyboard, never normal actions.
  prompt_for_own_contact(c,uid)
  return
 # Reward the inviter only when this account passed channel/phone gating.
 # An idempotent ledger reference prevents duplicate points on /start retries.
 loyalty.referral(c,uid,s,now())
 if u["state"].startswith("soc:pendinginvite:") and (cmd=="home" or body.startswith("/start")):
  invite=u["state"].split(":",2)[2]
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  with contextlib.suppress(ValueError):
   if social.invite_start(c,uid,"/start soc_"+invite):return
 if new and s["new_user_notice"]:
  for aid in s["admins"]:enqueue(c,aid,"عضو جدید: "+str(uid)+" / "+who.get("first_name",""))
 # Private playlist invitations are resolved only after mandatory channel/
 # contact onboarding has succeeded, and are scoped to the invitee.
 if not q and social.invite_start(c,uid,body):
  return
 if not q and media_library.invite_start(c,uid,body):
  return
 # Received/forwarded Telegram audio and video are accepted directly, even
 # without pressing an upload button. Financial receipt and text workflows
 # take precedence, preventing a media file from hijacking a paid order.
 if not q and media_library.is_media_message(m) and (
   not u["state"] or u["state"].startswith("med:upload:")):
  media_library.accept(c,uid,m,u["state"].split(":")[2] if u["state"].startswith("med:upload:") else None)
  return
 if body in ("🏠 منوی اصلی","منوی اصلی","/menu"):cmd="home"
 if body in ("🎮 سرگرمی","سرگرمی"):cmd="fun"
 if body in ("⭐ امتیازهای من","امتیازات من"):cmd="fun:points"
 if body in ("کیف پول","💳 کیف پول"):cmd="wallet"
 if not q and not body.startswith("/start") and cmd not in ("home","fun","fun:points","wallet") and u["state"]:
  state=u["state"]
  if state.startswith("soc:"):
   social.input_text(c,uid,state,m,up["update_id"])
   return
  if state.startswith("med:"):
   media_library.text_input(c,uid,state,m,up["update_id"])
   return
  if state=="ai:chat":
   entertainment.ai_answer(c,uid,body,up["update_id"])
   return
  if state.startswith("a:"):
   admin_text(c,uid,state,body,up["update_id"])
   return
  if state.startswith("planname:"):
   parts=state.split(":",3)
   if len(parts) not in (3,4):raise ValueError("درخواست خرید نامعتبر است؛ دوباره پلن را انتخاب کنید")
   pid=integer(int(parts[1]),1);qty=integer(int(parts[2]),1,20)
   carried_coupon=text(parts[3],40) if len(parts)==4 else ""
   chosen=subscription_label(body)
   ident=quote(c,uid,pid,quantity=qty,label=chosen)
   assigned=json.loads(c.execute("SELECT plan FROM quotes WHERE id=?",(ident,)).fetchone()["plan"])["label"]
   c.execute("UPDATE users SET state=? WHERE id=?",("coupon:"+carried_coupon if carried_coupon else "",uid))
   if qty>1:
    keys=[[("تأیید خرید "+str(qty)+" اشتراک","buy:"+ident)]]
    title="نام پایه سفارش انبوه"
   else:
    keys=[[("پرداخت از کیف پول","buy:"+ident)]]
    if s["card"]:keys.append([("پرداخت کارت‌به‌کارت","cardbuy:"+ident)])
    title="نام اشتراک شما"
   suffix=" (برای جلوگیری از تکرار، پسوند تصادفی اضافه شد)" if assigned!=chosen else ""
   plan_name=c.execute("SELECT name,price,days,gb FROM plans WHERE id=?",(pid,)).fetchone()
   enqueue(c,uid,title+": "+assigned+suffix+
     "\nپلن: "+plan_name["name"]+"\nمدت: "+str(plan_name["days"])+
     " روز\nحجم: "+(str(plan_name["gb"])+" گیگابایت" if plan_name["gb"] else "نامحدود")+
     "\nقیمت پایه هر اشتراک: "+str(plan_name["price"])+" تومان",keys)
   return
  if (state=="transfer" and not s["transfer"]) or (state=="coupon" and not s["discounts"]) or ((state=="topup" or state.startswith("receipt:")) and (not s["wallet"] or not s["card"])):raise ValueError("این امکان غیرفعال شده است")
  if state.startswith("orderreceipt:"):
   ident=state[len("orderreceipt:"):]
   o=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(ident,uid)).fetchone()
   if not o or o["status"]!="awaiting_receipt":raise ValueError("سفارش در انتظار رسید پیدا نشد")
   receipt="photo:"+m["photo"][-1]["file_id"] if m.get("photo") else ("document:"+m["document"]["file_id"] if m.get("document") else text(body,2000))
   if not receipt:raise ValueError("تصویر یا فایل رسید را ارسال کنید")
   rid=c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(?,?,?,?)",(uid,o["price"],receipt,now())).lastrowid
   c.execute("INSERT INTO receipt_orders VALUES(?,?)",(rid,ident))
   c.execute("UPDATE orders SET status='receipt_review' WHERE id=?",(ident,))
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,"رسید ثبت شد. بعد از تأیید مدیر، اشتراک خودکار ساخته می‌شود.")
   for aid in s["admins"]:
    queue_receipt_review(c,aid,rid)
   return
  if state.startswith("rename:"):
   if not s["rename"]:raise ValueError("تغییر نام غیرفعال")
   sid=state.split(":")[1];r=c.execute("SELECT * FROM services WHERE id=? AND user_id=?",(sid,uid)).fetchone()
   if not r:raise ValueError("سرویس پیدا نشد")
   data=json.loads(r["data"])
   # Renaming must obey the same uniqueness rule as initial checkout.
   new_label=subscription_label(body)
   if new_label.casefold()!=str(data.get("label","")).casefold():
    new_label=unique_subscription_label(c,new_label)
   data["label"]=new_label
   c.execute("UPDATE services SET data=? WHERE id=?",(json.dumps(data),sid));c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,"نام اشتراک ثبت شد: "+data["label"]);return
  if state in ("wallet_amount","topup","online"):
   amount=integer(int(body.replace(",","").replace("٬","").strip()),1000,10**9)
   wallet_amount_options(c,uid,amount)
   return
  if state.startswith("wallet_method:"):
   raise ValueError("روش پرداخت را با دکمه‌ها انتخاب کنید")
  if state.startswith("receipt:"):
   receipt=("photo:"+m["photo"][-1]["file_id"] if m.get("photo") else
      ("document:"+m["document"]["file_id"] if m.get("document") else text(body,2000)))
   if not receipt:raise ValueError("رسید ارسال نشده")
   amount=int(state.split(":")[1])
   ident=c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(?,?,?,?)",(uid,amount,receipt,now())).lastrowid
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,bot_message(c,"receipt_saved"))
   for aid in s["admins"]:
    queue_receipt_review(c,aid,ident)
   return
  if state=="coupon":
   code=text(body,40).upper();cp=c.execute("SELECT * FROM coupons WHERE code=? AND uses>0 AND (expires=0 OR expires>?)",(code,now())).fetchone()
   if not cp or c.execute("SELECT 1 FROM redeemed WHERE code=? AND user_id=?",(code,uid)).fetchone():raise ValueError("کد نامعتبر یا مصرف‌شده")
   if cp["credit"]:
    money(c,uid,cp["credit"],"gift","gift:"+code+":"+str(uid))
    c.execute("INSERT INTO redeemed VALUES(?,?)",(code,uid));c.execute("UPDATE coupons SET uses=uses-1 WHERE code=?",(code,))
    c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   else:c.execute("UPDATE users SET state=? WHERE id=?",("coupon:"+code,uid))
   enqueue(c,uid,bot_message(c,"coupon_saved"),[[("خرید","plans")]]);return
  if state=="support":
   c.execute("INSERT INTO tickets(user_id,body,created) VALUES(?,?,?)",(uid,text(body,3000),now()))
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,));enqueue(c,uid,bot_message(c,"support_saved"));return
  if state=="transfer":
   chunks=body.split()
   if len(chunks)!=2:raise ValueError("شناسه عمومی MS-... و مبلغ را وارد کنید")
   target=social.by_code(c,chunks[0].upper())
   amount=integer(int(chunks[1]),1,10**9)
   if target==uid:raise ValueError("گیرنده باید متفاوت باشد")
   ref="transfer:"+str(up["update_id"])
   money(c,uid,-amount,"transfer",ref+":out");money(c,target,amount,"transfer",ref+":in")
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,));enqueue(c,uid,"انتقال انجام شد.");enqueue(c,target,"اعتبار دریافتی: "+str(amount)+" تومان");return
 if cmd.startswith("/start") or cmd=="home":
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  home(c,uid)
 elif cmd=="fun" or cmd.startswith("fun:"):
  # Leaving a wallet/payment/text-entry step must cancel that pending input,
  # including when AI or media is opened directly from the main menu.
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  entertainment.callback(c,uid,cmd)
 elif cmd.startswith("soc:"):
  social.callback(c,uid,cmd)
 elif cmd.startswith("med:"):
  media_library.callback(c,uid,cmd)
 elif cmd.startswith("medadmin:"):
  media_library.admin_action(c,uid,cmd)
 elif cmd.startswith("a:"):
  admin_callback(c,uid,cmd)
 elif cmd=="plans":
  # Keep an applied discount across plan selection, but leave unrelated
  # payment or edit prompts when the user switches to shopping.
  if not u["state"].startswith("coupon:"):
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  if not s["buy"]:
   enqueue(c,uid,"خرید اشتراک فعلاً غیرفعال است.",[[("🏠 منوی اصلی","home")]])
   return
  rows=[[ (p["category"]+" / "+p["name"]+" — "+str(p["price"])+" تومان","choose:"+str(p["id"])) ]
        for p in c.execute("SELECT * FROM plans WHERE active=1 AND is_trial=0 LIMIT 70")]
  if s["trial"] and not c.execute("SELECT 1 FROM orders WHERE user_id=? AND trial=1 LIMIT 1",(uid,)).fetchone():
   if c.execute("SELECT 1 FROM plans WHERE active=1 AND is_trial=1 LIMIT 1").fetchone():
    rows.insert(0,[("🎁 دریافت تست رایگان","trial")])
  enqueue(c,uid,bot_message(c,"plans_menu") if rows else bot_message(c,"plans_empty"),rows)
 elif cmd.startswith("bulk:"):
  if not s["bulk"] or not s["reseller"] or not u["reseller"]:raise ValueError("دسترسی نماینده لازم است")
  _,pid,qty=cmd.split(":");qty=integer(int(qty),2,20);pid=int(pid)
  p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(pid,)).fetchone()
  if not p or p["is_trial"]:raise ValueError("پلن فروش عمده معتبر نیست")
  price=p["price"]*(100-s["reseller_discount"])//100
  coupon_state=u["state"][7:] if u["state"].startswith("coupon:") else ""
  c.execute("UPDATE users SET state=? WHERE id=?",("planname:"+str(pid)+":"+str(qty)+(":"+coupon_state if coupon_state else ""),uid))
  enqueue(c,uid,"خرید "+str(qty)+" اشتراک؛ جمع پیش از کد تخفیف "+str(price*qty)+
    " تومان. لطفاً نام پایه سرویس‌ها را ارسال کنید؛ نام‌های تکراری پسوند ۵ کاراکتری می‌گیرند.")
 elif cmd.startswith("rename:"):
  if not s["rename"]:raise ValueError("تغییر نام غیرفعال")
  sid=cmd.split(":")[1]
  if not c.execute("SELECT 1 FROM services WHERE id=? AND user_id=?",(sid,uid)).fetchone():raise ValueError("سرویس یافت نشد")
  c.execute("UPDATE users SET state=? WHERE id=?",(cmd,uid));enqueue(c,uid,"نام دلخواه سرویس را ارسال کنید.")
 elif cmd.startswith("choose:"):
  pid=int(cmd.split(":")[1]);p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(pid,)).fetchone()
  if not p:raise ValueError("پلن یافت نشد")
  if p["is_trial"]:
   enqueue(c,uid,"این پلن تست رایگان است؛ برای دریافت بدون پرداخت ادامه دهید.",
      [[("🎁 دریافت رایگان","trialpick:"+str(pid))]])
   return
  coupon_state=u["state"][7:] if u["state"].startswith("coupon:") else ""
  c.execute("UPDATE users SET state=? WHERE id=?",("planname:"+str(pid)+":1"+(":"+coupon_state if coupon_state else ""),uid))
  enqueue(c,uid,"پلن: "+p["name"]+"\nمدت: "+str(p["days"])+" روز\nحجم: "+
    (str(p["gb"])+" GB" if p["gb"] else "نامحدود")+
    "\nقیمت پایه: "+str(p["price"])+" تومان"+
    "\n\nلطفاً نام دلخواه اشتراک خود را ارسال کنید (۲ تا ۵۵ کاراکتر)."+
    "\nاگر نام قبلاً استفاده شده باشد، یک پسوند تصادفی ۵ کاراکتری به آن اضافه می‌شود.")
  if s["bulk"] and s["reseller"] and u["reseller"]:enqueue(c,uid,"خرید انبوه نماینده",[[("۵ اشتراک","bulk:"+str(pid)+":5"),("۱۰ اشتراک","bulk:"+str(pid)+":10")]])
 elif cmd.startswith("cardbuy:"):
  ident=cmd.split(":")[1];purchase_quote(c,uid,ident,card=True)
  o=c.execute("SELECT * FROM orders WHERE id=?",("quote:"+ident+":0",)).fetchone()
  if o["status"]!="awaiting_receipt":raise ValueError("این سفارش قبلاً ثبت شده است")
  c.execute("UPDATE users SET state=? WHERE id=?",("orderreceipt:"+o["id"],uid))
  enqueue(c,uid,"شماره سفارش: "+o["id"]+"\nمبلغ دقیق: "+str(o["price"])+" تومان\n"+s["card"]+"\nپس از واریز، عکس یا فایل رسید را همین‌جا ارسال کنید.")
 elif cmd.startswith("buy:"):
  purchase_quote(c,uid,cmd.split(":")[1]);enqueue(c,uid,bot_message(c,"order_confirm"))
 elif cmd=="trial":
  if not s["trial"]:raise ValueError("دریافت تست رایگان غیرفعال است")
  if c.execute("SELECT 1 FROM orders WHERE user_id=? AND trial=1",(uid,)).fetchone():
   raise ValueError("شما قبلاً تست رایگان دریافت کرده‌اید")
  rows=[[("🎁 "+p["name"]+" — "+str(p["days"])+" روز، "+str(p["gb"])+" گیگابایت",
          "trialpick:"+str(p["id"]))] for p in c.execute(
            "SELECT * FROM plans WHERE active=1 AND is_trial=1 ORDER BY id LIMIT 40")]
  enqueue(c,uid,bot_message(c,"trial_menu") if rows else bot_message(c,"trial_empty"),rows)
 elif cmd.startswith("trialpick:"):
  if not s["trial"]:raise ValueError("تست رایگان غیرفعال است")
  pid=int(cmd.partition(":")[2])
  order(c,uid,pid,"trial:"+str(uid),trial=True)
  enqueue(c,uid,bot_message(c,"trial_confirm"),
          key="trial:accepted:"+str(uid))
 elif cmd=="services":
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  rows=[[(json.loads(r["data"]).get("label",json.loads(r["plan"])["name"])+" / "+r["username"],"service:"+r["id"])] for r in c.execute("SELECT * FROM services WHERE user_id=? AND deleted=0 ORDER BY expires DESC LIMIT 70",(uid,))]
  enqueue(c,uid,"سرویس‌های شما" if rows else "هنوز سرویسی ندارید.",rows)
 elif cmd.startswith("service:"):deliver(c,uid,cmd.split(":")[1])
 elif cmd.startswith("renew:"):
  sid=cmd.split(":")[1];r=c.execute("SELECT * FROM services WHERE id=? AND user_id=? AND deleted=0",(sid,uid)).fetchone()
  if not r:raise ValueError("سرویس پیدا نشد")
  p=c.execute("SELECT * FROM plans WHERE id=? AND active=1",(json.loads(r["plan"])["id"],)).fetchone()
  if not p:raise ValueError("پلن تمدید فعال نیست")
  enqueue(c,uid,"تمدید اشتراک «"+json.loads(r["data"]).get("label",r["username"])+
    "»: "+str(p["days"])+" روز؛ قیمت "+str(p["price"])+
    " تومان.",[[("تأیید تمدید","renewok:"+quote(c,uid,p["id"],sid))]])
 elif cmd.startswith("renewok:"):
  purchase_quote(c,uid,cmd.split(":")[1]);enqueue(c,uid,"تمدید ثبت شد.")
 elif cmd=="wallet":
  wallet_page(c,uid)
 elif cmd=="points_discount":
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  points_and_discounts(c,uid)
 elif cmd in ("walletadd","topup","online"):
  if not s["wallet"] or not (s["card"] or (s["zarinpal"] and s["merchant_id"])):
   raise ValueError("شارژ کیف پول فعلاً غیرفعال است")
  c.execute("UPDATE users SET state='wallet_amount' WHERE id=?",(uid,))
  enqueue(c,uid,"مبلغ شارژ را به تومان وارد کنید.",[[("↩️ کیف پول","wallet")]])
 elif cmd.startswith(("walletcard:","walletonline:")):
  method,_,raw=cmd.partition(":")
  amount=integer(int(raw),1000,10**9)
  state=c.execute("SELECT state FROM users WHERE id=?",(uid,)).fetchone()["state"]
  if state!="wallet_method:"+str(amount):raise ValueError("ابتدا مبلغ شارژ را وارد کنید")
  if method=="walletcard":
   if not s["wallet"] or not s["card"]:raise ValueError("کارت‌به‌کارت غیرفعال است")
   c.execute("UPDATE users SET state=? WHERE id=?",("receipt:"+str(amount),uid))
   enqueue(c,uid,"💳 مبلغ: "+format(amount,",")+" تومان\n"+s["card"],
       [[("↩️ کیف پول","wallet")]])
   enqueue(c,uid,"📷 پس از واریز، عکس یا فایل رسید را ارسال کنید.",
       [[("↩️ کیف پول","wallet")]])
  else:
   if not s["wallet"] or not s["zarinpal"] or not s["merchant_id"]:
    raise ValueError("درگاه پرداخت غیرفعال است")
   authority=payment_create(c,uid,amount,"tg:"+str(up["update_id"]))
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,"🌐 لینک پرداخت "+format(amount,",")+" تومان:\n"+
       "https://payment.zarinpal.com/pg/StartPay/"+authority,
       [[("↩️ کیف پول","wallet")]])
 elif cmd in ("transfer","coupon","support"):
  if cmd=="transfer" and not s["transfer"]:raise ValueError("انتقال غیرفعال است")
  if cmd=="coupon" and not s["discounts"]:raise ValueError("کد تخفیف غیرفعال است")
  c.execute("UPDATE users SET state=? WHERE id=?",(cmd,uid))
  prompt={"transfer":"شناسه عمومی مسیحا (MS-XXXXXXXX) و مبلغ تومان را وارد کنید.",
          "coupon":"کد تخفیف یا هدیه را بفرستید.",
          "support":(bot_message(c,"support_prompt") if c.execute(
              "SELECT 1 FROM message_templates WHERE id='support_prompt'").fetchone() else s["support"])}[cmd]
  back=[[("↩️ کیف پول","wallet")]] if cmd=="transfer" else (
       [[("↩️ امتیاز و تخفیف","points_discount")]] if cmd=="coupon" else
       [[("🏠 منوی اصلی","home")]])
  enqueue(c,uid,prompt,back)
 elif cmd=="referral":
  if not s["referrals"] and not s.get("points_per_referral",0):raise ValueError("سیستم دعوت دوستان غیرفعال است")
  enqueue(c,uid,"لینک دعوت اختصاصی:\nhttps://t.me/"+s.get("bot_username","")+"?start=ref_"+social.code(c,uid)+
   "\nامتیاز هر دعوت موفق: "+str(s["points_per_referral"])+
   "\nکمیسیون خرید زیرمجموعه: "+(str(s["commission"])+"٪" if s["referrals"] else "غیرفعال"),
   [[("⭐ امتیازهای من","fun:points"),("🏠 منوی اصلی","home")]])
 elif cmd=="admin":admin_menu(c,uid)
 elif cmd.startswith(("approve:","reject:")):
  if uid not in s["admins"]:raise ValueError("دسترسی مدیر لازم است")
  decide_receipt(c,int(cmd.split(":")[1]),cmd.startswith("approve:"));enqueue(c,uid,"ثبت شد.")
 elif cmd.startswith("rotateask:"):
  if not s["rotate"]:raise ValueError("تعویض لینک غیرفعال")
  enqueue(c,uid,"لینک‌ها و اطلاعات اتصال قبلی از کار می‌افتند. ادامه؟",[[("تأیید تعویض","rotate:"+cmd.split(":")[1])]])
 elif cmd.startswith("rotate:"):
  if not s["rotate"]:raise ValueError("تعویض لینک غیرفعال")
  sid=cmd.split(":")[1];r=c.execute("SELECT * FROM services WHERE id=? AND user_id=? AND protocol='xray' AND deleted=0",(sid,uid)).fetchone()
  if not r:raise ValueError("سرویس یافت نشد")
  api=Panel();current=api.call("/panel/api/clients/get/"+r["username"]);client={k:v for k,v in current["client"].items() if k in ("email","security","flow","limitIp","totalGB","expiryTime","enable","tgId","subId","comment","group","reset","limitHwid")};data=json.loads(r["data"])
  data.update(uuid=str(uuid.uuid4()),password=secrets.token_urlsafe(24),subId=secrets.token_urlsafe(18))
  client.update(id=data["uuid"],password=data["password"],auth=data["password"],subId=data["subId"])
  api.call("/panel/api/clients/update/"+r["username"],client)
  data["subscription"]=[];data["links"]=[]
  c.execute("UPDATE services SET data=? WHERE id=?",(json.dumps(data),sid))
  enqueue(c,uid,"اطلاعات قبلی باطل شد. لینک جدید را دریافت کنید.",[[("دریافت","service:"+sid)]])
 elif cmd.startswith("qr:"):
  r=c.execute("SELECT * FROM services WHERE id=? AND user_id=?",(cmd.split(":")[1],uid)).fetchone()
  if not r:raise ValueError("سرویس یافت نشد")
  links=[subscription_url(json.loads(r["data"])["subId"])]
  if isinstance(links,dict):links=list(links.values())
  if isinstance(links,str):links=[links]
  link=next((x for x in links if isinstance(x,str) and x.startswith("https://")),"")
  if not link:raise ValueError("ساب موجود نیست")
  c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),uid,"qr",json.dumps({"text":link})))
 elif cmd.startswith("deleteask:"):
  if not s["self_delete"]:raise ValueError("حذف غیرفعال")
  enqueue(c,uid,"اشتراک بدون بازگشت وجه حذف شود؟",[[("بله، حذف شود","delete:"+cmd.split(":")[1])]])
 elif cmd.startswith("delete:"):
  if not s["self_delete"]:raise ValueError("حذف غیرفعال")
  sid=cmd.split(":")[1];r=c.execute("SELECT * FROM services WHERE id=? AND user_id=? AND deleted=0",(sid,uid)).fetchone()
  if not r:raise ValueError("سرویس یافت نشد")
  if r["protocol"]=="xray":Panel().call("/panel/api/clients/del/"+r["username"],{})
  else:vpn({"action":"delete","protocol":r["protocol"],"username":r["username"]})
  c.execute("UPDATE services SET deleted=1 WHERE id=?",(sid,));enqueue(c,uid,"حذف شد.")
 else:home(c,uid)
def upload(s,chat,filename,content,photo=False):
 boundary="Masiha"+secrets.token_hex(12);field="photo" if photo else "document"
 body=("--"+boundary+'\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n'+str(chat)+"\r\n--"+boundary+'\r\nContent-Disposition: form-data; name="'+field+'"; filename="'+filename+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+content+("\r\n--"+boundary+"--\r\n").encode()
 req=urllib.request.Request("https://api.telegram.org/bot"+s["token"]+("/sendPhoto" if photo else "/sendDocument"),data=body,headers={"Content-Type":"multipart/form-data; boundary="+boundary})
 with telegram_open(req,timeout=35) as r:
  if not json.load(r).get("ok"):raise ValueError("upload failed")
def tick():
 with conn() as c:
  s=settings(c);put(c,"heartbeat",now());c.commit()
  if not s["enabled"]:
   put(c,"last_error","");c.commit();return
  if now()-s.get("social_cleanup_checked",0)>86400:
   # Drop private chat message bodies after the configured retention period.
   retention=c.execute("SELECT v FROM media_settings WHERE k='social_chat_retention_days'").fetchone()
   days=int(retention[0]) if retention else 90
   c.execute("DELETE FROM social_chat_messages WHERE created<?",(now()-max(30,min(365,days))*86400,))
   c.execute("DELETE FROM social_geo WHERE updated<?",(now()-30*86400,))
   put(c,"social_cleanup_checked",now())
   c.commit()
  if s["require_contact"] and not s.get("contact_onboarding_rollout_v2"):
   request_missing_phones(c)
   put(c,"contact_onboarding_rollout_v2",True)
   c.commit()
  if not s.get("bot_username"):
   me=tg(s,"getMe",{});put(c,"bot_username",me["username"]);c.commit()
  updates=tg(s,"getUpdates",{"offset":s.get("offset",0),"timeout":1,"limit":20,"allowed_updates":["message","callback_query"]})
  for up in updates:
   if c.execute("SELECT 1 FROM updates WHERE id=?",(up["update_id"],)).fetchone():continue
   try:
    c.execute("BEGIN IMMEDIATE");handle(c,up)
    c.execute("INSERT INTO updates VALUES(?,?)",(up["update_id"],now()));put(c,"offset",up["update_id"]+1);c.commit()
   except ValueError as e:
    # Expected user/business validation errors must not poison the queue.
    c.rollback()
    m=up.get("message") or up.get("callback_query",{}).get("message",{})
    if m.get("chat",{}).get("type")=="private":
     enqueue(c,m["chat"]["id"],str(e))
    c.execute("INSERT OR IGNORE INTO updates VALUES(?,?)",(up["update_id"],now()))
    put(c,"offset",up["update_id"]+1)
    c.commit()
   except Exception:
    # An API/network/panel error is transient; DO NOT acknowledge the update.
    # Otherwise paid orders or receipt approvals could be permanently lost.
    c.rollback()
    raise
   if up.get("callback_query"):
    with contextlib.suppress(Exception):tg(s,"answerCallbackQuery",{"callback_query_id":up["callback_query"]["id"]})
  for o in c.execute("SELECT * FROM orders WHERE status='paid' LIMIT 5").fetchall():
   try:provision(c,o);c.commit()
   except Exception:
    c.rollback();c.execute("UPDATE orders SET status='review' WHERE id=?",(o["id"],))
    for aid in s["admins"]:enqueue(c,aid,"سفارش "+o["id"]+" نیاز به بررسی دارد. موجودی مجدد کسر نمی‌شود.")
    c.commit()
  if s["notifications"]:
   for r in c.execute("SELECT * FROM services WHERE deleted=0 AND expires BETWEEN ? AND ?",(now(),now()+s["warning_days"]*86400)).fetchall():
    enqueue(c,r["user_id"],"اشتراک شما نزدیک انقضاست.",[[("مشاهده / تمدید","service:"+r["id"])]],"expiry:"+r["id"]+":"+str(r["expires"]))
   if now()-s.get("usage_checked",0)>300:
    for r in c.execute("SELECT * FROM services WHERE deleted=0 AND protocol='xray' ORDER BY id LIMIT 100").fetchall():
     try:
      info=Panel().call("/panel/api/clients/get/"+r["username"]);total=info["client"]["totalGB"];used=info["usedTraffic"]
      if total and total-used<=total*s["usage_warning_percent"]//100:
       enqueue(c,r["user_id"],"حجم اشتراک شما رو به اتمام است.",[[("مشاهده / تمدید","service:"+r["id"])]],"usage:"+r["id"]+":"+str(total))
     except Exception:pass
    put(c,"usage_checked",now())
   c.commit()
  for row in c.execute("""SELECT o.* FROM outbox AS o
    LEFT JOIN users AS u ON u.id=o.chat
    WHERE o.done=0 AND o.next_try<=? AND
       (?=0 OR (u.id>0 AND TRIM(COALESCE(u.phone,''))<>'')
        OR o.dedupe LIKE 'phone-gate:%')
    ORDER BY o.id LIMIT 15""",(now(),int(bool(s["require_contact"])))).fetchall():
   try:
    p=json.loads(row["payload"])
    if row["method"]=="file":upload(s,row["chat"],p["filename"],p["content"].encode())
    elif row["method"]=="qr":
     import qrcode
     buf=io.BytesIO();qrcode.make(p["text"]).save(buf,format="PNG");upload(s,row["chat"],"subscription.png",buf.getvalue(),True)
    else:tg(s,row["method"],p)
    c.execute("UPDATE outbox SET done=1,payload='{}' WHERE id=?",(row["id"],))
   except Exception:
    new_attempts=row["attempts"]+1
    # -1 is a *visible dead-letter*, never "done/sent". Admin can retry.
    status=-1 if new_attempts>=16 else 0
    c.execute("UPDATE outbox SET attempts=?,next_try=?,done=? WHERE id=?",
              (new_attempts,now()+min(3600,2**min(new_attempts,11)),status,row["id"]))
    if status==-1:
     audit(c,"telegram_delivery_failed","outbox:"+str(row["id"]))
   c.commit();time.sleep(.08)
  failed=c.execute("SELECT COUNT(*) FROM outbox WHERE done=-1").fetchone()[0]
  put(c,"last_error",("ارسال "+str(failed)+" پیام تلگرام ناموفق مانده؛ صف خطا را بررسی کنید") if failed else "")
  c.commit()
def main():
 os.umask(0o077);BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
 with open(BASE/"worker.lock","a") as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  while True:
   try:
    tick()
   except Exception:
    with conn() as c:
     put(c,"last_error","ارتباط یا پردازش ربات ناموفق است؛ اتصال تلگرام و تنظیمات را بررسی کنید")
    # Back off only for failed polls, not for successful bot interactions.
    time.sleep(1)
   else:
    # getUpdates already waits up to one second if idle.
    # A further 3-second unconditional sleep made users wait unnecessarily.
    time.sleep(.08)
if __name__=="__main__":main()
