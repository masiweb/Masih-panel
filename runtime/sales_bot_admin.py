#!/usr/bin/python3
"""Telegram administration over the existing authenticated admin-ID allowlist.

Only regular bot operations are accessible; bot tokens/merchant secret,
administrator IDs, bot disable and response templates are WEB-only. All state
changes reuse the exact same validated commerce helpers as the web UI.
"""
import json
import secrets
import gamification as loyalty
from sales import (
 DEFAULT, settings, save_settings, save_plan, plan_overview, enqueue, bot_message, integer,
 text, audit, money, decide_receipt, restore_reserved_coupon, now, tg
)

SETTINGS_BOOL = {
 "buy":"خرید", "renew":"تمدید", "wallet":"کیف پول",
 "transfer":"انتقال موجودی", "trial":"تست رایگان", "discounts":"تخفیف",
 "referrals":"معرفی دوستان", "reseller":"نمایندگی", "notifications":"هشدارها",
 "require_contact":"اجبار شماره تماس", "self_delete":"حذف اشتراک توسط مشتری",
 "bulk":"خرید انبوه", "rename":"تغییر نام اشتراک", "rotate":"تعویض لینک",
 "new_user_notice":"اعلان کاربر جدید", "zarinpal":"درگاه پرداخت",
 "ai_enabled":"فعال‌سازی هوش مصنوعی"
}
SETTINGS_EDIT = {
 # All end-user response texts are deliberately WEB-only, including legacy
 # welcome/support fields; do not expose them through the admin Telegram bot.
 "card":"اطلاعات کارت‌به‌کارت", "channel":"کانال اجباری",
 "report_channel":"کانال گزارش فروش",
 "commission":"درصد پورسانت", "reseller_discount":"تخفیف نماینده",
 "warning_days":"روز هشدار انقضا", "usage_warning_percent":"درصد هشدار حجم",
 "points_per_toman":"مبلغ هر واحد خرید (تومان)",
 "points_per_purchase_unit":"امتیاز هر واحد خرید",
 "points_per_referral":"امتیاز دعوت موفق",
 "ai_cost":"هزینه هر پیام هوش مصنوعی"
}
def ensure_admin(c,uid):
 if uid not in settings(c)["admins"]:raise ValueError("دسترسی این بخش فقط برای مدیر ربات است")

def send(c,uid,message,buttons=None):
 enqueue(c,uid,message,buttons)

def main(c,uid):
 ensure_admin(c,uid)
 # Discard any half-completed input workflow on return to the admin home.
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 send(c,uid,bot_message(c,"welcome_admin"),[
  [("تنظیم امکانات","a:settings"),("پلن‌های فروش","a:plans")],
  [("کاربران و نمایندگان","a:users"),("سفارش‌ها","a:orders")],
  [("رسیدهای در انتظار","a:receipts"),("کدهای تخفیف","a:coupons")],
  [("تیکت پشتیبانی","a:tickets"),("پیام همگانی","a:broadcast")],
  [("پیام‌های ارسال‌نشده","a:outbox"),("گزارش مالی","a:finance")],
  [("آدرس پنل وب","a:web")]])
def back():return [[("بازگشت به مدیریت","a:home")]]

def plan_line(p):
 return ("🎁 " if p["is_trial"] else "📦 ")+p["name"]+" / "+str(p["days"])+" روز / "+str(p["gb"])+" GB / "+str(p["price"])+" تومان"

def plan_form_help():
 return ("فرم پلن را در یک پیام با جداکننده | بفرستید:\n"
  "نام | دسته | نوع | قیمت | روز | گیگابایت | ظرفیت | تست | اینباندها\n"
  "نوع: xray یا openvpn یا openconnect؛ تست: 0 یا 1؛ "
  "اینباندها: 1,2,3 (برای سرویس مستقل خالی). "
  "برای تست رایگان قیمت صفر می‌شود. نمونه:\n"
  "اشتراک تست | عمومی | xray | 0 | 2 | 2 | 50 | 1 | 1,2\n"
  "برای لغو دستور /start را ارسال کنید.")

def callback(c,uid,cmd):
 ensure_admin(c,uid)
 s=settings(c)
 parts=cmd.split(":")
 op=parts[1] if len(parts)>1 else "home"
 ident=parts[2] if len(parts)>2 else ""
 if op=="home":return main(c,uid)
 if op=="web":
  from runtime_config import PUBLIC_PANEL_URL
  return send(c,uid,"اطلاعات محرمانه بات، هویت مدیران و «متن‌های پیش‌فرض» تنها از پنل امن وب قابل تغییرند:\n"+PUBLIC_PANEL_URL+"/panel/sales-bot",back())
 if op in ("settings","plans","users","orders","receipts","coupons","tickets","outbox","finance"):
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 if op=="settings":
  rows=[[(("✅" if s[k] else "◻️")+" "+label,"a:toggle:"+k)] for k,label in SETTINGS_BOOL.items()]
  rows.extend([[(label,"a:cfg:"+k)] for k,label in SETTINGS_EDIT.items()])
  rows+=back()
  return send(c,uid,"تنظیمات ربات؛ روی هر کلید بزنید. تغییر متن‌های پیش‌فرض فقط در پنل وب است.",rows)
 if op=="toggle":
  if ident not in SETTINGS_BOOL:raise ValueError("این تنظیم از تلگرام قابل تغییر نیست")
  save_settings(c,{ident:not s[ident]})
  send(c,uid,SETTINGS_BOOL[ident]+": "+("فعال شد" if not s[ident] else "غیرفعال شد"))
  return callback(c,uid,"a:settings")
 if op=="cfg":
  if ident not in SETTINGS_EDIT:raise ValueError("این تنظیم فقط از وب قابل تغییر است")
  c.execute("UPDATE users SET state=? WHERE id=?",("a:cfg:"+ident,uid))
  return send(c,uid,"مقدار جدید «"+SETTINGS_EDIT[ident]+"» را در پیام بعدی بفرستید. خالی‌کردن: -")
 if op=="plans":
  rows=[[(plan_line(p)[:51],"a:plan:"+str(p["id"]))] for p in plan_overview(c)[:35]]
  rows+= [[("➕ ساخت پلن","a:newplan")]] + back()
  return send(c,uid,"مدیریت پلن‌ها؛ تعداد: "+str(len(plan_overview(c))),rows)
 if op=="newplan":
  c.execute("UPDATE users SET state='a:newplan' WHERE id=?",(uid,))
  return send(c,uid,plan_form_help())
 if op=="editplan":
  pid=integer(int(ident),1)
  if not c.execute("SELECT 1 FROM plans WHERE id=?",(pid,)).fetchone():raise ValueError("پلن یافت نشد")
  c.execute("UPDATE users SET state=? WHERE id=?",("a:editplan:"+str(pid),uid))
  return send(c,uid,plan_form_help())
 if op=="plan":
  pid=integer(int(ident),1)
  p=next((x for x in plan_overview(c) if x["id"]==pid),None)
  if not p:raise ValueError("پلن وجود ندارد")
  rows=[
   [("ویرایش پلن","a:editplan:"+ident),("کپی پلن","a:copyplan:"+ident)],
   [(("غیرفعال‌کردن" if p["active"] else "فعال‌کردن"),"a:toggleplan:"+ident)],
   [("حذف پلن بدون سفارش","a:delask:"+ident)]]+back()
  return send(c,uid,plan_line(p)+"\nفروش: "+str(p["sold"])+" / در انتظار: "+str(p["pending"])+
   "\nظرفیت باقی‌مانده: "+str(p["remaining"] if p["remaining"] is not None else "نامحدود"),rows)
 if op=="copyplan":
  p=c.execute("SELECT * FROM plans WHERE id=?",(integer(int(ident),1),)).fetchone()
  if not p:raise ValueError("پلن پیدا نشد")
  values=dict(p);values.pop("id");values["name"]+=" (کپی)";values["active"]=False
  values["is_trial"]=bool(values["is_trial"]);values["inbounds"]=json.loads(values["inbounds"])
  new_id=save_plan(c,values)
  send(c,uid,"کپی پلن غیرفعال ساخته شد.")
  return callback(c,uid,"a:plan:"+str(new_id))
 if op=="toggleplan":
  pid=integer(int(ident),1)
  row=c.execute("SELECT active,is_trial FROM plans WHERE id=?",(pid,)).fetchone()
  if not row:raise ValueError("پلن وجود ندارد")
  enabled=not bool(row["active"])
  c.execute("UPDATE plans SET active=? WHERE id=?",(int(enabled),pid))
  if enabled and row["is_trial"]:save_settings(c,{"trial":True})
  return callback(c,uid,"a:plan:"+ident)
 if op=="delask":
  return send(c,uid,"حذف پلن بدون سفارش غیرقابل بازگشت است. مطمئن هستید؟",[[("بله، حذف","a:delok:"+ident)]]+back())
 if op=="delok":
  pid=integer(int(ident),1)
  if c.execute("SELECT 1 FROM orders WHERE json_extract(plan,'$.id')=? LIMIT 1",(pid,)).fetchone():
   raise ValueError("پلن سابقه سفارش دارد و باید غیرفعال شود")
  c.execute("DELETE FROM quotes WHERE plan_id=?",(pid,))
  if not c.execute("DELETE FROM plans WHERE id=?",(pid,)).rowcount:raise ValueError("پلن پیدا نشد")
  return callback(c,uid,"a:plans")
 if op=="users":
  rows=[[(str(r["id"])+" / "+r["name"][:22],"a:user:"+str(r["id"]))] for r in c.execute(
   "SELECT id,name FROM users ORDER BY created DESC LIMIT 30")]
  return send(c,uid,"کاربران اخیر",rows+back())
 if op=="user":
  user=c.execute("SELECT * FROM users WHERE id=?",(integer(int(ident),1),)).fetchone()
  if not user:raise ValueError("کاربر یافت نشد")
  # Telegram profile photos are optional and user-controlled. Cache only
  # Telegram's file_id in the message outbox, never image bytes in SQLite.
  try:
   photos=tg(s,"getUserProfilePhotos",{"user_id":user["id"],"limit":1})
   files=photos.get("photos") or []
   if files and files[0]:
    file_id=files[0][-1]["file_id"]
    c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
      (secrets.token_hex(12),uid,"sendPhoto",json.dumps({
       "chat_id":uid,"photo":file_id,"caption":"🖼 تصویر پروفایل کاربر "+str(user["id"])})))
  except (ValueError,KeyError,TypeError):
   pass  # absence or Telegram privacy restrictions must not block the card
  invites=c.execute("SELECT COUNT(*) FROM loyalty_points WHERE reference LIKE 'referral:%' AND user_id=?",(user["id"],)).fetchone()[0]
  subs=c.execute("SELECT COUNT(*) FROM services WHERE user_id=?",(user["id"],)).fetchone()[0]
  text_card="👤 "+(user["name"] or "بدون نام")+"\nشناسه: "+str(user["id"])+
     "\nموجودی: "+str(user["balance"])+" تومان\nشماره: "+(user["phone"] or "ثبت نشده")+
     "\n⭐ امتیاز: "+str(loyalty.balance(c,user["id"]))+
     "\nدعوت موفق: "+str(invites)+"\nاشتراک‌های خریداری‌شده: "+str(subs)+
     "\nوضعیت: "+("مسدود" if user["blocked"] else "فعال")+
     " | نماینده: "+("بله" if user["reseller"] else "خیر")
  return send(c,uid,text_card,[
   [("📦 لیست اشتراک‌های کاربر","a:usubs:"+ident)],
   [("مسدود/آزاد","a:block:"+ident),("نمایندگی روشن/خاموش","a:reseller:"+ident)],
   [("تغییر اعتبار","a:credit:"+ident)],
   [("↩️ فهرست کاربران","a:users")]]+back())
 if op=="usubs":
  user_id=integer(int(ident),1)
  if not c.execute("SELECT 1 FROM users WHERE id=?",(user_id,)).fetchone():
   raise ValueError("کاربر یافت نشد")
  records=c.execute("SELECT * FROM services WHERE user_id=? ORDER BY expires DESC LIMIT 40",(user_id,)).fetchall()
  rows=[]
  for rec in records:
   try:label=json.loads(rec["data"]).get("label") or rec["username"]
   except (ValueError,TypeError):label=rec["username"]
   rows.append([(("❌ " if rec["deleted"] else "📦 ")+label[:30],"a:usub:"+rec["id"])])
  return send(c,uid,"اشتراک‌های کاربر "+str(user_id)+
      "\nتعداد: "+str(len(records))+" (حداکثر ۴۰ مورد اخیر)"+
      ("\nهنوز اشتراکی خریداری نشده است." if not rows else ""),
      rows+[[("↩️ بازگشت به مشخصات کاربر","a:user:"+str(user_id))]]+back())
 if op=="usub":
  rec=c.execute("SELECT * FROM services WHERE id=?",(ident,)).fetchone()
  if not rec:raise ValueError("اشتراک وجود ندارد")
  p=json.loads(rec["plan"]);d=json.loads(rec["data"])
  return send(c,uid,"📦 اشتراک "+str(d.get("label") or rec["username"])+
    "\nنوع: "+rec["protocol"]+"\nپلن: "+str(p.get("name",""))+
    "\nنام سیستمی: "+rec["username"]+
    "\nانقضا (Unix): "+str(rec["expires"])+
    "\nوضعیت: "+("حذف‌شده" if rec["deleted"] else "فعال یا منقضی‌شده"),
    [[("↩️ اشتراک‌های همین کاربر","a:usubs:"+str(rec["user_id"]))]]+back())
 if op in ("block","reseller"):
  uid2=integer(int(ident),1)
  field="blocked" if op=="block" else "reseller"
  if not c.execute("UPDATE users SET "+field+"=1-"+field+" WHERE id=?",(uid2,)).rowcount:raise ValueError("کاربر یافت نشد")
  return callback(c,uid,"a:user:"+ident)
 if op=="credit":
  c.execute("UPDATE users SET state=? WHERE id=?",("a:credit:"+str(integer(int(ident),1)),uid))
  return send(c,uid,"مبلغ تغییر موجودی را با علامت مثبت یا منفی و توضیح وارد کنید:\n15000 | افزایش دستی")
 if op=="orders":
  rows=[[(str(o["id"])[:18]+" / "+str(o["status"]),"a:order:"+str(o["id"]))] for o in c.execute(
    "SELECT id,status FROM orders ORDER BY created DESC LIMIT 25")]
  return send(c,uid,"آخرین سفارش‌ها",rows+back())
 if op=="order":
  order=c.execute("SELECT * FROM orders WHERE id=?",(ident,)).fetchone()
  if not order:raise ValueError("سفارش یافت نشد")
  rows=[]
  if order["status"]=="review":rows.append([("تلاش مجدد تحویل","a:retry:"+ident)])
  if order["status"] in ("awaiting_receipt","receipt_review"):rows.append([("لغو سفارش","a:cancelask:"+ident)])
  return send(c,uid,"سفارش: "+ident+"\nکاربر: "+str(order["user_id"])+"\nمبلغ: "+str(order["price"])+"\nوضعیت: "+order["status"],rows+back())
 if op=="retry":
  if not c.execute("UPDATE orders SET status='paid' WHERE id=? AND status='review'",(ident,)).rowcount:raise ValueError("سفارش در انتظار تحویل مجدد نیست")
  return callback(c,uid,"a:order:"+ident)
 if op=="cancelask":
  return send(c,uid,"لغو سفارش در انتظار رسید؟",[[("تأیید لغو","a:cancelok:"+ident)]]+back())
 if op=="cancelok":
  order=c.execute("SELECT * FROM orders WHERE id=?",(ident,)).fetchone()
  if not order or order["status"] not in ("awaiting_receipt","receipt_review"):raise ValueError("این سفارش قابل لغو نیست")
  restore_reserved_coupon(c,order)
  linked=c.execute("SELECT topup_id FROM receipt_orders WHERE order_id=?",(ident,)).fetchone()
  if linked:c.execute("UPDATE topups SET status='rejected' WHERE id=? AND status='pending'",(linked["topup_id"],))
  c.execute("UPDATE orders SET status='cancelled' WHERE id=?",(ident,))
  enqueue(c,order["user_id"],"سفارش در انتظار رسید شما توسط مدیر لغو شد.")
  return callback(c,uid,"a:order:"+ident)
 if op=="receipts":
  rows=[[(str(r["id"])+" / "+str(r["amount"])+" تومان","a:receipt:"+str(r["id"]))] for r in c.execute("SELECT id,amount FROM topups WHERE status='pending' LIMIT 30")]
  return send(c,uid,"رسیدهای در انتظار",rows+back())
 if op=="receipt":
  rid=integer(int(ident),1)
  row=c.execute("SELECT * FROM topups WHERE id=? AND status='pending'",(rid,)).fetchone()
  if not row:raise ValueError("رسید در انتظار یافت نشد")
  return send(c,uid,"رسید "+str(rid)+" | "+str(row["amount"])+" تومان | کاربر "+str(row["user_id"]),[
   [("تأیید","approve:"+ident),("رد","reject:"+ident)]]+back())
 if op=="coupons":
  rows=[[(r["code"]+" / "+str(r["uses"])+" باقی","a:couponview:"+r["code"])] for r in c.execute("SELECT code,uses FROM coupons LIMIT 25")]
  return send(c,uid,"کدهای تخفیف",rows+[[("افزودن/ویرایش کد","a:newcoupon")]]+back())
 if op=="couponview":
  row=c.execute("SELECT * FROM coupons WHERE code=?",(ident,)).fetchone()
  if not row:raise ValueError("کد یافت نشد")
  return send(c,uid,str(dict(row)),back())
 if op=="newcoupon":
  c.execute("UPDATE users SET state='a:newcoupon' WHERE id=?",(uid,))
  return send(c,uid,"کد | درصد تخفیف | اعتبار هدیه | تعداد استفاده | انقضا به timestamp (صفر نامحدود)\nOFF20 | 20 | 0 | 50 | 0")
 if op=="tickets":
  rows=[[(str(r["id"])+" / "+str(r["user_id"]),"a:ticket:"+str(r["id"]))] for r in c.execute("SELECT id,user_id FROM tickets ORDER BY id DESC LIMIT 20")]
  return send(c,uid,"درخواست‌های پشتیبانی",rows+back())
 if op=="ticket":
  row=c.execute("SELECT * FROM tickets WHERE id=?",(integer(int(ident),1),)).fetchone()
  if not row:raise ValueError("درخواست پیدا نشد")
  return send(c,uid,"درخواست "+ident+"\n"+row["body"][:1700]+
    "\nپاسخ قبلی: "+row["reply"][:400],[[("پاسخ‌دادن","a:reply:"+ident)]]+back())
 if op=="reply":
  c.execute("UPDATE users SET state=? WHERE id=?",("a:reply:"+str(integer(int(ident),1)),uid))
  return send(c,uid,"پاسخ درخواست را ارسال کنید.")
 if op=="broadcast":
  c.execute("UPDATE users SET state='a:broadcast' WHERE id=?",(uid,))
  return send(c,uid,"متن پیام همگانی را ارسال کنید. پس از آن تأیید جداگانه می‌خواهم.")
 if op=="broadcastcancel":
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  return send(c,uid,"ارسال پیام همگانی لغو شد.",back())
 if op=="broadcastok":
  user=c.execute("SELECT state FROM users WHERE id=?",(uid,)).fetchone()
  if not user or not user["state"].startswith("a:broadcast-confirm:"):raise ValueError("پیام در انتظار تأیید نیست")
  body=user["state"].split(":",2)[2]
  for r in c.execute("SELECT id FROM users WHERE blocked=0"):
   enqueue(c,r["id"],body,key="bot-broadcast:"+str(uid)+":"+str(now())+":"+str(r["id"]))
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  return send(c,uid,"پیام همگانی در صف ارسال قرار گرفت.",back())
 if op=="outbox":
  rows=[[(str(r["id"])+" / "+str(r["attempts"])+" تلاش","a:outretry:"+str(r["id"]))] for r in c.execute("SELECT id,attempts FROM outbox WHERE done=-1 ORDER BY id DESC LIMIT 30")]
  return send(c,uid,"پیام‌های ناموفق؛ با لمس هر پیام ارسال دوباره آغاز می‌شود.",rows+back())
 if op=="outretry":
  if not c.execute("UPDATE outbox SET done=0,attempts=0,next_try=0 WHERE id=? AND done=-1",(integer(int(ident),1),)).rowcount:
   raise ValueError("پیام ناموفق موجود نیست")
  return callback(c,uid,"a:outbox")
 if op=="finance":
  sold=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='fulfilled'").fetchone()[0]
  pending=c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('paid','review','awaiting_receipt','receipt_review')").fetchone()[0]
  rows=list(c.execute("SELECT user_id,amount FROM ledger ORDER BY id DESC LIMIT 5"))
  details="\n".join(str(r["user_id"])+": "+str(r["amount"])+" تومان" for r in rows) or "هنوز سندی وجود ندارد"
  return send(c,uid,"درآمد تحویل‌شده: "+str(sold)+" تومان\nسفارش‌های در انتظار: "+str(pending)+
    "\nپنج سند اخیر:\n"+details,back())
 raise ValueError("فرمان مدیریتی نامعتبر است")

def input_text(c,uid,state,body,update_id):
 ensure_admin(c,uid)
 parts=state.split(":")
 op=parts[1] if len(parts)>1 else ""
 data=body.strip()
 if op=="cfg":
  key=parts[2] if len(parts)>2 else ""
  if key not in SETTINGS_EDIT:raise ValueError("این گزینه قابل تغییر نیست")
  value="" if data=="-" else data
  if type(DEFAULT[key]) is int:
   high=10**9 if key=="points_per_toman" else 10000 if key in ("points_per_purchase_unit","points_per_referral","ai_cost") else 100
   low=1 if key in ("points_per_toman","ai_cost") else 0
   value=integer(int(data),low,high)
  save_settings(c,{key:value})
 elif op in ("newplan","editplan"):
  fields=[v.strip() for v in data.split("|")]
  if len(fields)!=9:raise ValueError("فرم پلن باید ۹ قسمت داشته باشد")
  name,category,protocol,price,days,gb,capacity,is_trial,ids=fields
  values={"name":name,"category":category,"protocol":protocol,
          "price":integer(int(price),0),"days":integer(int(days),1,3650),
          "gb":integer(int(gb),0),"capacity":integer(int(capacity),0),
          "is_trial":bool(integer(int(is_trial),0,1)),
          "active":True,
          "inbounds":[integer(int(v.strip()),1) for v in ids.split(",") if v.strip()]}
  if op=="editplan":values["id"]=integer(int(parts[2]),1)
  pid=save_plan(c,values)
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  return callback(c,uid,"a:plan:"+str(pid))
 elif op=="credit":
  uid2=integer(int(parts[2]),1)
  values=[p.strip() for p in data.split("|",1)]
  if len(values)!=2:raise ValueError("مبلغ و توضیح را با | جدا کنید")
  amount=integer(int(values[0]),-10**12,10**12)
  if amount==0:raise ValueError("مبلغ صفر معتبر نیست")
  money(c,uid2,amount,text(values[1],150),"telegram-admin:"+str(uid)+":"+str(update_id))
 elif op=="newcoupon":
  fields=[x.strip() for x in data.split("|")]
  if len(fields)!=5:raise ValueError("کد تخفیف باید ۵ قسمت داشته باشد")
  import re
  code,percent,credit,uses,expires=fields
  code=code.upper()
  if not re.fullmatch("[A-Z0-9_-]{3,40}",code):raise ValueError("فرمت کد تخفیف نامعتبر است")
  c.execute("INSERT INTO coupons VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,credit=excluded.credit,uses=excluded.uses,expires=excluded.expires",
   (code,integer(int(percent),0,100),integer(int(credit),0),integer(int(uses),0),integer(int(expires),0)))
 elif op=="reply":
  ident=integer(int(parts[2]),1)
  r=c.execute("SELECT * FROM tickets WHERE id=?",(ident,)).fetchone()
  if not r:raise ValueError("درخواست وجود ندارد")
  value=text(data,3000)
  if not value:raise ValueError("متن پاسخ لازم است")
  c.execute("UPDATE tickets SET reply=? WHERE id=?",(value,ident))
  enqueue(c,r["user_id"],"پاسخ پشتیبانی: "+value)
 elif op=="broadcast":
  value=text(data,2000)
  if not value:raise ValueError("متن پیام لازم است")
  c.execute("UPDATE users SET state=? WHERE id=?",("a:broadcast-confirm:"+value,uid))
  return send(c,uid,"این پیام برای همه کاربران فعال ارسال شود؟\n"+value,
    [[("تأیید ارسال به همه","a:broadcastok"),("لغو","a:broadcastcancel")]])
 else:raise ValueError("ورودی مدیریت قابل شناسایی نیست")
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 audit(c,"telegram_admin_"+op,str(uid))
 send(c,uid,bot_message(c,"admin_saved"),back())
