#!/usr/bin/python3
"""Independent AI subscriptions and receipt-review commerce.

No provider account password is ever solicited or persisted. Personal-account
activation is manual / via official provider authorization off-bot.
"""
import json,re,secrets
from sales import enqueue,settings,now,integer,audit

TYPES={"bot":"🤖 داخل ربات","personal":"📧 ایمیل شخصی","ready":"📨 ایمیل آماده"}
OPS=("text","image","video")

def setup(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS ai_sale_models(
 id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1
 );
 CREATE TABLE IF NOT EXISTS ai_sale_plans(
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL,
 name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('bot','personal','ready')),
 price INTEGER NOT NULL CHECK(price>=0),days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 730),
 credits_text INTEGER NOT NULL DEFAULT 0,credits_image INTEGER NOT NULL DEFAULT 0,
 credits_video INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1
 );
 CREATE TABLE IF NOT EXISTS ai_sale_orders(
 id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,plan_id INTEGER NOT NULL,
 plan_snapshot TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'awaiting_receipt',
 receipt TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',
 created INTEGER NOT NULL,updated INTEGER NOT NULL,confirmed INTEGER NOT NULL DEFAULT 0,
 fulfilled INTEGER NOT NULL DEFAULT 0
 );
 CREATE INDEX IF NOT EXISTS ai_sale_user_orders ON ai_sale_orders(user_id,created DESC);
 CREATE TABLE IF NOT EXISTS ai_sale_subscriptions(
 order_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires INTEGER NOT NULL,
 remaining_text INTEGER NOT NULL CHECK(remaining_text>=0),
 remaining_image INTEGER NOT NULL CHECK(remaining_image>=0),
 remaining_video INTEGER NOT NULL CHECK(remaining_video>=0),
 created INTEGER NOT NULL
 );
 CREATE TABLE IF NOT EXISTS ai_ready_stock(
 id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id INTEGER NOT NULL,delivery_code TEXT NOT NULL,
 order_id TEXT UNIQUE,added INTEGER NOT NULL,delivered INTEGER
 );
 """)
def plan(c,details):
 name=str(details.get("name","")).strip()
 kind=details.get("kind")
 price=integer(details.get("price"),0,10**9)
 days=integer(details.get("days"),1,730)
 model=integer(details.get("model_id"),1,10**8)
 if kind not in TYPES or not 2<=len(name)<=80:raise ValueError("نوع یا نام پلن نامعتبر است")
 if not c.execute("SELECT 1 FROM ai_sale_models WHERE id=?",(model,)).fetchone():raise ValueError("مدل تعریف نشده است")
 credits={x:integer(details.get("credits_"+x,0),0,1000000) for x in OPS}
 if kind!="bot" and any(credits.values()):raise ValueError("سهمیه مصرف فقط برای اشتراک داخل ربات است")
 values=(model,name,kind,price,days,*[credits[x] for x in OPS],int(details.get("active",True)))
 ident=details.get("id")
 if ident is not None:
  ident=integer(ident,1)
  if not c.execute("SELECT 1 FROM ai_sale_plans WHERE id=?",(ident,)).fetchone():raise ValueError("پلن پیدا نشد")
  c.execute("""UPDATE ai_sale_plans SET model_id=?,name=?,kind=?,price=?,days=?,
   credits_text=?,credits_image=?,credits_video=?,active=? WHERE id=?""",(*values,ident))
 else:
  ident=c.execute("""INSERT INTO ai_sale_plans
    (model_id,name,kind,price,days,credits_text,credits_image,credits_video,active)
    VALUES(?,?,?,?,?,?,?,?,?)""",values).lastrowid
 audit(c,"ai_plan_change",str(ident))
 return ident

def model(c,details):
 label=str(details.get("name","")).strip()
 if not 2<=len(label)<=70 or any(ch in label for ch in "<>{}"):raise ValueError("نام مدل نامعتبر است")
 ident=details.get("id")
 if ident:
  ident=integer(ident,1)
  if not c.execute("UPDATE ai_sale_models SET name=?,active=? WHERE id=?",
      (label,int(details.get("active",True)),ident)).rowcount:raise ValueError("مدل پیدا نشد")
 else:ident=c.execute("INSERT INTO ai_sale_models(name) VALUES(?)",(label,)).lastrowid
 return ident

def stock(c,details):
 pid=integer(details.get("plan_id"),1)
 p=c.execute("SELECT kind FROM ai_sale_plans WHERE id=?",(pid,)).fetchone()
 if not p or p["kind"]!="ready":raise ValueError("پلن ایمیل آماده معتبر نیست")
 code=details.get("delivery_code")
 if not isinstance(code,str) or not 4<=len(code.strip())<=250 or "\n" in code:
  raise ValueError("کد تحویل معتبر نیست؛ گذرواژه حساب را ذخیره نکنید")
 # This field is for redeemable activation codes, NEVER login credentials.
 if "@" in code or "password" in code.lower() or "پسورد" in code:
  raise ValueError("اطلاعات ورود و گذرواژه در موجودی ذخیره نشود؛ تنها کد فعال‌سازی مجاز است")
 ident=c.execute("INSERT INTO ai_ready_stock(plan_id,delivery_code,added) VALUES(?,?,?)",
                 (pid,code.strip(),now())).lastrowid
 return ident

def catalog(c,kind=None):
 sql="""SELECT p.*,m.name AS model_name,
    (SELECT COUNT(*) FROM ai_ready_stock x WHERE x.plan_id=p.id AND x.order_id IS NULL) AS stock
    FROM ai_sale_plans p JOIN ai_sale_models m ON m.id=p.model_id"""
 if kind:
  if kind not in TYPES:raise ValueError("نوع فروش نامعتبر")
  sql+=" WHERE p.kind=? ORDER BY p.id DESC"
  return [dict(r) for r in c.execute(sql,(kind,))]
 return [dict(r) for r in c.execute(sql+" ORDER BY p.id DESC")]

def choose(c,uid,pid):
 p=c.execute("""SELECT p.*,m.name model_name,m.active model_active FROM ai_sale_plans p
     JOIN ai_sale_models m ON m.id=p.model_id WHERE p.id=?""",(pid,)).fetchone()
 if not p or not p["active"] or not p["model_active"]:raise ValueError("این پلن در دسترس نیست")
 if p["kind"]=="ready" and not c.execute("SELECT 1 FROM ai_ready_stock WHERE plan_id=? AND order_id IS NULL",(pid,)).fetchone():
  raise ValueError("موجودی ایمیل آماده این پلن تمام شده است")
 ident="ai-"+secrets.token_hex(12)
 c.execute("""INSERT INTO ai_sale_orders(id,user_id,plan_id,plan_snapshot,created,updated)
 VALUES(?,?,?,?,?,?)""",(ident,uid,pid,json.dumps(dict(p),ensure_ascii=False),now(),now()))
 return ident

def owned(c,uid,oid):
 row=c.execute("SELECT * FROM ai_sale_orders WHERE id=? AND user_id=?",(oid,uid)).fetchone()
 if not row:raise ValueError("سفارش پیدا نشد")
 return row

def set_email(c,uid,oid,email):
 row=owned(c,uid,oid);p=json.loads(row["plan_snapshot"])
 if p["kind"]!="personal" or row["status"]!="awaiting_receipt":raise ValueError("مرحله ثبت ایمیل نامعتبر است")
 if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",email.strip()):
  raise ValueError("آدرس ایمیل معتبر نیست")
 c.execute("UPDATE ai_sale_orders SET email=?,updated=? WHERE id=?",(email.strip(),now(),oid))

def store_receipt(c,uid,oid,message):
 row=owned(c,uid,oid)
 if row["status"]!="awaiting_receipt":raise ValueError("رسید این سفارش قبلاً دریافت شده است")
 p=json.loads(row["plan_snapshot"])
 if p["kind"]=="personal" and not row["email"]:raise ValueError("ابتدا ایمیل را ثبت کنید")
 file=(message.get("photo") or [])
 raw=("photo:"+file[-1]["file_id"] if file else
      "document:"+message["document"]["file_id"] if message.get("document") else "")
 if not raw:raise ValueError("عکس یا فایل رسید پرداخت را ارسال کنید")
 c.execute("UPDATE ai_sale_orders SET receipt=?,status='preview',updated=? WHERE id=?",(raw,now(),oid))

def preview(c,uid,oid):
 row=owned(c,uid,oid);p=json.loads(row["plan_snapshot"])
 if row["status"]!="preview":raise ValueError("اطلاعات سفارش قابل تأیید نیست")
 body=("📋 بازبینی سفارش اشتراک هوش مصنوعی\nمدل: "+p["model_name"]+
    "\nنوع: "+TYPES[p["kind"]]+"\nپلن: "+p["name"]+
    "\nمدت: "+str(p["days"])+" روز\nمبلغ: "+format(p["price"],",")+" تومان"+
    ("\nایمیل شخصی: "+row["email"] if row["email"] else "")+
    "\n✅ رسید دریافت شد. اطلاعات فوق صحیح است؟")
 enqueue(c,uid,body,[[("✅ تأیید نهایی و ارسال به مدیر","ais:confirm:"+oid)],
   [("❌ لغو سفارش","ais:cancel:"+oid)]])
def confirm(c,uid,oid):
 row=owned(c,uid,oid)
 if row["status"]!="preview":raise ValueError("سفارش قبلاً بررسی شده است")
 c.execute("UPDATE ai_sale_orders SET status='review',confirmed=1,updated=? WHERE id=?",(now(),oid))
 p=json.loads(row["plan_snapshot"])
 for admin in settings(c)["admins"]:
  media_type,fid=row["receipt"].split(":",1)
  c.execute("""INSERT INTO outbox(dedupe,chat,method,payload)
    VALUES(?,?,?,?)""",(f"ai-proof:{oid}:{admin}",admin,
    "sendPhoto" if media_type=="photo" else "sendDocument",
    json.dumps({"chat_id":admin,media_type:fid})))
  enqueue(c,admin,("🧾 رسید سفارش هوش مصنوعی\nسفارش: "+oid+
    "\nکاربر: "+str(uid)+"\nمدل: "+p["model_name"]+
    "\nنوع: "+TYPES[p["kind"]]+"\nمدت: "+str(p["days"])+
    "\nمبلغ: "+format(p["price"],",")+" تومان"+
    ("\nایمیل شخصی: "+row["email"] if row["email"] else "")+
    "\nگذرواژه از طریق ربات دریافت نمی‌شود."),
    [[("✅ تأیید رسید","aisadmin:approve:"+oid),("❌ رد با ذکر علت","aisadmin:reject:"+oid)]],
    key=f"ai-admin-review:{oid}:{admin}")
 enqueue(c,uid,"✅ سفارش برای بررسی رسید به مدیر ارسال شد.",key="ai-review-notice:"+oid)

def _deliver(c,row):
 p=json.loads(row["plan_snapshot"]);oid=row["id"];uid=row["user_id"]
 if row["fulfilled"]:return
 if p["kind"]=="bot":
  credits=[int(p["credits_"+op]) for op in OPS]
  c.execute("""INSERT OR IGNORE INTO ai_sale_subscriptions
   (order_id,user_id,expires,remaining_text,remaining_image,remaining_video,created)
   VALUES(?,?,?,?,?,?,?)""",(oid,uid,now()+int(p["days"])*86400,*credits,now()))
  enqueue(c,uid,"✅ اشتراک هوش مصنوعی داخل ربات فعال شد.\nمدل: "+p["model_name"]+
    "\nمدت: "+str(p["days"])+" روز\nسهمیه متن: "+str(credits[0])+
    "\nتصویر: "+str(credits[1])+"\nویدیو: "+str(credits[2]),
    [[("🤖 استفاده از هوش مصنوعی","fun:ai")]],key="ai-fulfilled:"+oid)
 elif p["kind"]=="ready":
  code=c.execute("SELECT id,delivery_code FROM ai_ready_stock WHERE plan_id=? AND order_id IS NULL ORDER BY id LIMIT 1",
      (row["plan_id"],)).fetchone()
  if not code:raise ValueError("موجودی کد فعال‌سازی این پلن کافی نیست؛ تأیید متوقف شد")
  if not c.execute("UPDATE ai_ready_stock SET order_id=?,delivered=? WHERE id=? AND order_id IS NULL",
     (oid,now(),code["id"])).rowcount:raise ValueError("موجودی قبلاً مصرف شده است")
  enqueue(c,uid,"✅ سفارش ایمیل آماده تأیید شد.\nمدل: "+p["model_name"]+
   "\nکد فعال‌سازی یک‌بارمصرف: "+code["delivery_code"]+
   "\nاین کد را فقط در وب‌سایت رسمی ارائه‌دهنده وارد کنید. هیچ رمز عبوری داخل ربات منتقل نمی‌شود.",
   key="ai-fulfilled:"+oid)
 else:
  c.execute("UPDATE ai_sale_orders SET status='activating',updated=? WHERE id=?",(now(),oid))
  enqueue(c,uid,"✅ رسید سفارش ایمیل شخصی تأیید شد.\nدر حال فعال‌سازی اشتراک هستیم؛ نتیجه از همین ربات اعلام خواهد شد.\nبرای امنیت، هیچ رمز عبوری را در تلگرام ارسال نکنید.",
   key="ai-activating:"+oid)
  return
 c.execute("UPDATE ai_sale_orders SET status='fulfilled',fulfilled=1,updated=? WHERE id=?",(now(),oid))

def admin_decide(c,admin,oid,action,reason=""):
 if admin!="web-admin" and admin not in settings(c)["admins"]:raise ValueError("دسترسی مدیر لازم است")
 row=c.execute("SELECT * FROM ai_sale_orders WHERE id=?",(oid,)).fetchone()
 if not row:raise ValueError("سفارش پیدا نشد")
 if action=="reject":
  if row["status"]!="review":raise ValueError("این رسید در انتظار بررسی نیست")
  if not 4<=len(reason.strip())<=400:raise ValueError("علت رد رسید را با حداقل ۴ حرف وارد کنید")
  c.execute("UPDATE ai_sale_orders SET status='rejected',reason=?,updated=? WHERE id=?",(reason.strip(),now(),oid))
  enqueue(c,row["user_id"],"❌ رسید سفارش هوش مصنوعی تأیید نشد.\nعلت: "+reason.strip()+
    "\nبرای درخواست جدید به منوی خرید برگردید.",key="ai-rejected:"+oid)
 elif action=="approve":
  if row["status"]!="review":raise ValueError("رسید قبلاً بررسی شده است")
  _deliver(c,row)
 elif action=="complete":
  if row["status"]!="activating":raise ValueError("سفارش در صف فعال‌سازی نیست")
  c.execute("UPDATE ai_sale_orders SET status='fulfilled',fulfilled=1,updated=? WHERE id=?",(now(),oid))
  enqueue(c,row["user_id"],"✅ اشتراک روی ایمیل شخصی شما فعال شد. لطفاً از طریق سایت رسمی سرویس وارد حساب شوید.",
    key="ai-fulfilled:"+oid)
 else:raise ValueError("تصمیم مدیر نامعتبر است")
 audit(c,"ai_sale_"+action,oid)

def available(c,uid,op):
 if op not in OPS:raise ValueError("کاربری نامعتبر")
 col="remaining_"+op
 return c.execute(f"""SELECT order_id,{col} FROM ai_sale_subscriptions
 WHERE user_id=? AND expires>? AND {col}>0 ORDER BY expires ASC LIMIT 1""",
 (uid,now())).fetchone()

def consume(c,uid,op,ref):
 if op not in OPS:raise ValueError("نوع استفاده نامعتبر است")
 if c.execute("SELECT 1 FROM ai_usage WHERE reference=?",(ref,)).fetchone():return False
 row=available(c,uid,op)
 if not row:return False
 if not c.execute(f"UPDATE ai_sale_subscriptions SET remaining_{op}=remaining_{op}-1 WHERE order_id=? AND remaining_{op}>0",
 (row["order_id"],)).rowcount:raise ValueError("سهمیه کافی نیست")
 return True

def menu(c,uid):
 enqueue(c,uid,"🤖 خرید اشتراک هوش مصنوعی",[
 [("🤖 خرید اشتراک داخل ربات","ais:list:bot")],
 [("📧 خرید روی ایمیل شخصی","ais:list:personal")],
 [("📨 خرید روی ایمیل آماده","ais:list:ready")],
 [("📦 سفارش‌ها و اشتراک‌های من","ais:mine")],
 [("↩️ خرید جدید","buynew")]])

def callback(c,uid,cmd):
 if not settings(c)["buy"] and not cmd.startswith("ais:mine"):
  raise ValueError("فروشگاه در حال حاضر غیرفعال است")
 parts=cmd.split(":");op=parts[1] if len(parts)>1 else ""
 if op=="home":return menu(c,uid)
 if op=="list":
  kind=parts[2];items=[p for p in catalog(c,kind) if p["active"]]
  if kind=="ready":items=[p for p in items if p["stock"]>0]
  rows=[[("📦 "+p["model_name"]+" / "+p["name"]+" — "+format(p["price"],",")+" تومان",
          "ais:choose:"+str(p["id"]))] for p in items]
  rows.append([("↩️ خرید هوش مصنوعی","ais:home")])
  return enqueue(c,uid,"پلن‌های "+TYPES[kind] if items else "فعلاً پلن فعالی برای این نوع ثبت نشده است.",rows)
 if op=="choose":
  ident=choose(c,uid,integer(int(parts[2]),1));row=owned(c,uid,ident);p=json.loads(row["plan_snapshot"])
  if p["kind"]=="personal":
   c.execute("UPDATE users SET state=? WHERE id=?",("ais:email:"+ident,uid))
   return enqueue(c,uid,"📧 آدرس ایمیل شخصی خود را وارد کنید.\n⚠️ هیچ‌وقت رمز عبور یا کد ورود را در تلگرام نفرستید. فعال‌سازی صرفاً با مجوز رسمی سرویس انجام می‌شود.")
  c.execute("UPDATE users SET state=? WHERE id=?",("ais:receipt:"+ident,uid))
  return enqueue(c,uid,"مدل: "+p["model_name"]+"\nمدت: "+str(p["days"])+
    " روز\nمبلغ: "+format(p["price"],",")+" تومان\n"+settings(c)["card"]+
    "\nپس از پرداخت، تصویر رسید را ارسال کنید.",
    [[("💰 پرداخت با کیف پول","ais:paywallet:"+ident)]] if p["kind"]=="bot" else None)
 if op=="paywallet":
  oid=parts[2];row=owned(c,uid,oid);p=json.loads(row["plan_snapshot"])
  if p["kind"]!="bot" or row["status"]!="awaiting_receipt":
   raise ValueError("این سفارش امکان پرداخت از کیف پول را ندارد")
  from sales import money
  if row["receipt"]:raise ValueError("برای این سفارش رسید پرداخت ثبت شده است")
  if p["price"]:money(c,uid,-int(p["price"]),"ai_subscription","ai-sale:"+oid+":wallet")
  _deliver(c,row)
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  return
 if op=="confirm":
  return confirm(c,uid,parts[2])
 if op=="cancel":
  row=owned(c,uid,parts[2])
  if row["status"] not in ("awaiting_receipt","preview"):raise ValueError("لغو این سفارش ممکن نیست")
  c.execute("UPDATE ai_sale_orders SET status='cancelled',updated=? WHERE id=?",(now(),row["id"]))
  return enqueue(c,uid,"سفارش لغو شد. اگر پرداخت کرده‌اید با پشتیبانی تماس بگیرید.",[[("↩️ خرید هوش مصنوعی","ais:home")]])
 if op=="mine":
  rows=c.execute("SELECT id,status FROM ai_sale_orders WHERE user_id=? ORDER BY created DESC LIMIT 15",(uid,)).fetchall()
  ent=c.execute("""SELECT SUM(remaining_text),SUM(remaining_image),SUM(remaining_video) FROM ai_sale_subscriptions
   WHERE user_id=? AND expires>?""",(uid,now())).fetchone()
  return enqueue(c,uid,"📦 سفارش‌های من\n"+
    ("\n".join(x["id"][-10:]+" : "+x["status"] for x in rows) or "سفارشی ندارید")+
    "\n\nسهمیه فعال: متن "+str(ent[0] or 0)+" | تصویر "+str(ent[1] or 0)+" | ویدیو "+str(ent[2] or 0),
    [[("↩️ خرید هوش مصنوعی","ais:home")]])
 raise ValueError("گزینه خرید هوش مصنوعی نامعتبر است")

def input_message(c,uid,state,message):
 op,_,oid=state[4:].partition(":")
 row=owned(c,uid,oid)
 if op=="email":
  email=(message.get("text") or "").strip()
  if not email or " " in email:raise ValueError("ایمیل را به‌صورت متن وارد کنید")
  set_email(c,uid,oid,email)
  c.execute("UPDATE users SET state=? WHERE id=?",("ais:receipt:"+oid,uid))
  p=json.loads(row["plan_snapshot"])
  return enqueue(c,uid,"ایمیل ثبت شد: "+email+
   "\n⚠️ رمز ایمیل یا حساب سرویس را در ربات ارسال نکنید. پس از تأیید رسید، مدیر روش امن مجوزدهی رسمی را هماهنگ می‌کند."+
   "\nمبلغ: "+format(p["price"],",")+" تومان\n"+settings(c)["card"]+
   "\nپس از واریز تصویر رسید را ارسال کنید.")
 if op=="receipt":
  store_receipt(c,uid,oid,message)
  c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
  return preview(c,uid,oid)
 raise ValueError("مرحله خرید نامعتبر است")

def admin_callback(c,uid,cmd):
 if uid not in settings(c)["admins"]:raise ValueError("مدیر مجاز نیست")
 parts=cmd.split(":");op=parts[1] if len(parts)>1 else ""
 if op=="reject":
  oid=parts[2]
  row=c.execute("SELECT status FROM ai_sale_orders WHERE id=?",(oid,)).fetchone()
  if not row or row["status"]!="review":raise ValueError("رسید در انتظار بررسی نیست")
  c.execute("UPDATE users SET state=? WHERE id=?",("aisadmin:reject:"+oid,uid))
  return enqueue(c,uid,"❌ لطفاً علت رد رسید سفارش "+oid+" را بنویسید (حداقل ۴ حرف).")
 if op=="approve":
  admin_decide(c,uid,parts[2],"approve")
  return enqueue(c,uid,"✅ تصمیم ثبت شد؛ وضعیت سفارش به‌روزرسانی شد.")
 if op=="complete":
  admin_decide(c,uid,parts[2],"complete")
  return enqueue(c,uid,"✅ فعال‌سازی نهایی به کاربر اعلام شد.")
 if op=="pending":
  rows=c.execute("""SELECT id,status FROM ai_sale_orders
  WHERE status IN ('review','activating') ORDER BY created LIMIT 25""").fetchall()
  buttons=[[("🧾 "+r["id"][-12:]+" / "+r["status"],
    "aisadmin:detail:"+r["id"])] for r in rows]
  return enqueue(c,uid,"سفارش‌های هوش مصنوعی منتظر بررسی" if rows else "موردی در انتظار بررسی وجود ندارد",buttons)
 if op=="detail":
  row=c.execute("SELECT * FROM ai_sale_orders WHERE id=?",(parts[2],)).fetchone()
  if not row:raise ValueError("سفارش پیدا نشد")
  p=json.loads(row["plan_snapshot"])
  actions=([[("✅ تأیید رسید","aisadmin:approve:"+row["id"]),
              ("❌ رد با علت","aisadmin:reject:"+row["id"])]] if row["status"]=="review" else
           [[("✅ اعلام اتمام فعال‌سازی","aisadmin:complete:"+row["id"])]] if row["status"]=="activating" else [])
  return enqueue(c,uid,"سفارش: "+row["id"]+"\nنوع: "+TYPES[p["kind"]]+
   "\nمدل: "+p["model_name"]+"\nمبلغ: "+str(p["price"])+
   "\nایمیل: "+(row["email"] or "—")+"\nوضعیت: "+row["status"],actions)
 raise ValueError("عملیات مدیریت نامعتبر است")

def admin_input(c,uid,state,body):
 if uid not in settings(c)["admins"]:raise ValueError("دسترسی مدیر لازم است")
 if not state.startswith("aisadmin:reject:"):raise ValueError("مرحله رد نامعتبر است")
 oid=state[len("aisadmin:reject:"):]
 admin_decide(c,uid,oid,"reject",body)
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 return enqueue(c,uid,"❌ رسید رد شد و علت برای کاربر ارسال شد.")
