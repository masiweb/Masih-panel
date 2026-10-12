#!/usr/bin/python3
import base64, contextlib, fcntl, hashlib, io, json, os, re, secrets, time, urllib.request
from sales import *
from sales_bot_admin import main as admin_menu, callback as admin_callback, input_text as admin_text
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
 s=settings(c);u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 rows=[]
 if s["buy"]:rows.append([("خرید اشتراک","plans")])
 rows.append([("سرویس‌های من","services")])
 if s["wallet"]:rows.append([("کیف پول","wallet")])
 if s["trial"] and c.execute("SELECT 1 FROM plans WHERE active=1 AND is_trial=1 LIMIT 1").fetchone() and not c.execute(
    "SELECT 1 FROM orders WHERE user_id=? AND trial=1 LIMIT 1",(uid,)).fetchone():
  rows.append([("🎁 دریافت تست رایگان","trial")])
 if s["discounts"]:rows.append([("کد تخفیف / هدیه","coupon")])
 if s["referrals"]:rows.append([("دعوت دوستان","referral")])
 rows.append([("پشتیبانی","support")])
 if uid in s["admins"]:rows.append([("مدیریت فروش","admin")])
 welcome=bot_message(c,"home") if c.execute("SELECT 1 FROM message_templates WHERE id='home'").fetchone() else s["welcome"]
 enqueue(c,uid,welcome+"\n"+bot_message(c,"wallet_balance",balance=u["balance"]),rows)
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
   candidate=int(body.split("ref_")[1])
   if candidate!=uid and c.execute("SELECT 1 FROM users WHERE id=?",(candidate,)).fetchone():ref=candidate
 c.execute("INSERT OR IGNORE INTO users(id,name,referrer,created) VALUES(?,?,?,?)",(uid,who.get("first_name","")[:100],ref,now()))
 u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
 if u["blocked"]:return
 if s["channel"] and uid not in s["admins"]:
  member=tg(s,"getChatMember",{"chat_id":s["channel"],"user_id":uid})
  if member["status"] not in ("creator","administrator","member") and not member.get("is_member"):
   enqueue(c,uid,"ابتدا در کانال "+s["channel"]+" عضو شوید.",[[("بررسی عضویت","home")]]);return
 if s["require_contact"] and not u["phone"] and uid not in s["admins"]:
  contact=m.get("contact",{})
  if contact.get("user_id")==uid and isinstance(contact.get("phone_number"),str) and len(contact["phone_number"])<=32:
   c.execute("UPDATE users SET phone=? WHERE id=?",(contact["phone_number"],uid))
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,bot_message(c,"contact_saved"))
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
     (secrets.token_hex(12),uid,"sendMessage",json.dumps({"chat_id":uid,"text":"✅","reply_markup":{"remove_keyboard":True}})))
   return
  else:
   payload={"chat_id":uid,"text":bot_message(c,"contact_prompt"),"reply_markup":{"keyboard":[[{"text":"ارسال شماره من","request_contact":True}]],"resize_keyboard":True,"one_time_keyboard":True}}
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),uid,"sendMessage",json.dumps(payload)));return
 if new and s["new_user_notice"]:
  for aid in s["admins"]:enqueue(c,aid,"عضو جدید: "+str(uid)+" / "+who.get("first_name",""))
 if not q and not body.startswith("/start") and u["state"]:
  state=u["state"]
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
    enqueue(c,aid,"رسید سفارش "+ident+"\nکاربر: "+str(uid)+"\nمبلغ: "+str(o["price"])+" تومان",[[("تأیید","approve:"+str(rid)),("رد","reject:"+str(rid))]])
    if receipt.startswith(("photo:","document:")):
     kind,value=receipt.split(":",1)
     c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),aid,"sendPhoto" if kind=="photo" else "sendDocument",json.dumps({"chat_id":aid,kind:value})))
    else:enqueue(c,aid,receipt)
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
  if state=="online":
   authority=payment_create(c,uid,integer(int(body),1000,10**9),"tg:"+str(up["update_id"]))
   c.execute("UPDATE users SET state=\'\' WHERE id=?",(uid,))
   enqueue(c,uid,"لینک پرداخت امن زرین‌پال:\nhttps://payment.zarinpal.com/pg/StartPay/"+authority+"\nپس از پرداخت به همین ربات برگردید.");return
  if state=="topup":
   amount=integer(int(body),1000,10**9)
   c.execute("UPDATE users SET state=? WHERE id=?",("receipt:"+str(amount),uid))
   enqueue(c,uid,"رسید پرداخت را به صورت تصویر یا متن ارسال کنید.\n"+s["card"]);return
  if state.startswith("receipt:"):
   receipt="photo:"+m["photo"][-1]["file_id"] if m.get("photo") else text(body,2000)
   if not receipt:raise ValueError("رسید ارسال نشده")
   amount=int(state.split(":")[1])
   ident=c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(?,?,?,?)",(uid,amount,receipt,now())).lastrowid
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
   enqueue(c,uid,bot_message(c,"receipt_saved"))
   for aid in s["admins"]:
    enqueue(c,aid,"رسید شماره "+str(ident)+"؛ کاربر "+str(uid)+"؛ مبلغ "+str(amount)+" تومان",[[("تأیید","approve:"+str(ident)),("رد","reject:"+str(ident))]])
    if receipt.startswith("photo:"):
     c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",(secrets.token_hex(12),aid,"sendPhoto",json.dumps({"chat_id":aid,"photo":receipt[6:]})))
    else:enqueue(c,aid,receipt)
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
   target,amount=map(int,body.split());integer(target,1);integer(amount,1,10**9)
   if target==uid:raise ValueError("گیرنده باید متفاوت باشد")
   ref="transfer:"+str(up["update_id"])
   money(c,uid,-amount,"transfer",ref+":out");money(c,target,amount,"transfer",ref+":in")
   c.execute("UPDATE users SET state='' WHERE id=?",(uid,));enqueue(c,uid,"انتقال انجام شد.");enqueue(c,target,"اعتبار دریافتی: "+str(amount)+" تومان");return
 if cmd.startswith("/start") or cmd=="home":home(c,uid)
 elif cmd.startswith("a:"):
  admin_callback(c,uid,cmd)
 elif cmd=="plans":
  rows=[[ (p["category"]+" / "+p["name"]+" — "+str(p["price"])+" تومان","choose:"+str(p["id"])) ] for p in c.execute("SELECT * FROM plans WHERE active=1 AND is_trial=0 LIMIT 70")]
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
  if not s["wallet"]:raise ValueError("کیف پول غیرفعال")
  rows=[]
  if s["card"]:rows.append([("شارژ با کارت‌به‌کارت","topup")])
  if s["zarinpal"]:rows.append([("پرداخت آنلاین زرین‌پال","online")])
  if s["transfer"]:rows.append([("انتقال موجودی","transfer")])
  enqueue(c,uid,bot_message(c,"wallet_balance",balance=u["balance"]),rows)
 elif cmd=="online":
  if not s["zarinpal"] or not s["wallet"]:raise ValueError("درگاه غیرفعال")
  c.execute("UPDATE users SET state=\'online\' WHERE id=?",(uid,));enqueue(c,uid,"مبلغ شارژ به تومان را وارد کنید.")
 elif cmd in ("topup","transfer","coupon","support"):
  if cmd=="topup" and (not s["wallet"] or not s["card"]):raise ValueError("پرداخت دستی فعال نیست")
  if cmd=="transfer" and not s["transfer"]:raise ValueError("انتقال غیرفعال")
  if cmd=="coupon" and not s["discounts"]:raise ValueError("کد تخفیف غیرفعال")
  c.execute("UPDATE users SET state=? WHERE id=?",(cmd,uid))
  enqueue(c,uid,{"topup":"مبلغ شارژ به تومان را وارد کنید.","transfer":"آیدی عددی گیرنده و مبلغ تومان را با فاصله ارسال کنید.","coupon":"کد تخفیف یا هدیه را بفرستید.","support":(bot_message(c,"support_prompt") if c.execute("SELECT 1 FROM message_templates WHERE id='support_prompt'").fetchone() else s["support"])}[cmd])
 elif cmd=="referral":
  if not s["referrals"]:raise ValueError("زیرمجموعه غیرفعال")
  enqueue(c,uid,"لینک دعوت شما:\nhttps://t.me/"+s.get("bot_username","")+"?start=ref_"+str(uid)+"\nکمیسیون: "+str(s["commission"])+"٪")
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
 with urllib.request.urlopen(req,timeout=35) as r:
  if not json.load(r).get("ok"):raise ValueError("upload failed")
def tick():
 with conn() as c:
  s=settings(c);put(c,"heartbeat",now());c.commit()
  if not s["enabled"]:
   put(c,"last_error","");c.commit();return
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
  for row in c.execute("SELECT * FROM outbox WHERE done=0 AND next_try<=? ORDER BY id LIMIT 15",(now(),)).fetchall():
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
   try:tick()
   except Exception:
    with conn() as c:put(c,"last_error","ارتباط یا پردازش ربات ناموفق است؛ اتصال تلگرام و تنظیمات را بررسی کنید")
   time.sleep(3)
if __name__=="__main__":main()
