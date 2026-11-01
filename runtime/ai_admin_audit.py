#!/usr/bin/python3
"""Admin-only AI activity explorer; Telegram bot user dossier.
No API keys or provider credentials are ever included in responses.
Existing output media can be replayed only if Telegram returned a file ID.
"""
import json,datetime,secrets
from sales import enqueue,settings,audit,integer

PAGE_SIZE=6
MAX_PAGE=10000
STATUS={"queued":"در صف","polling":"در حال پردازش","done":"موفق و ارسال‌شده",
        "failed":"ناموفق"}
KIND={"image":"تصویر","video":"ویدیو"}
def setup(c):
 existed=bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='ai_text_audit'").fetchone())
 c.execute("""CREATE TABLE IF NOT EXISTS ai_text_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
  reference TEXT NOT NULL UNIQUE,question TEXT NOT NULL,answer TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL)""")
 c.execute("CREATE INDEX IF NOT EXISTS ai_text_audit_lookup ON ai_text_audit(user_id,id)")
 if not existed:
  c.execute("""INSERT OR IGNORE INTO ai_text_audit(user_id,reference,question,answer,model,created)
  SELECT u.user_id,u.reference,u.content,COALESCE(a.content,''),COALESCE(a.model,''),u.created
  FROM ai_chat_history u LEFT JOIN ai_chat_history a ON
    a.user_id=u.user_id AND a.reference=u.reference AND a.role='assistant'
  WHERE u.role='user' AND u.created>=?""",(__import__('time').time()-90*86400,))
  c.commit()

def record_text(c,uid,ref,question,answer,model,created):
 c.execute("""INSERT OR IGNORE INTO ai_text_audit(user_id,reference,question,answer,model,created)
  VALUES(?,?,?,?,?,?)""",(uid,ref,question,answer,model,created))

def prune(c):
 horizon=__import__('time').time()-90*86400
 c.execute("DELETE FROM ai_text_audit WHERE created<?",(horizon,))
 # Keep aggregate success/failure counters but discard old prompt and media IDs.
 c.execute("""UPDATE ai_media_jobs SET prompt='',file_id='',output_file_id='',provider_id=''
   WHERE created<? AND status IN ('done','failed') AND (prompt<>'' OR file_id<>'' OR output_file_id<>'')""",
   (horizon,))

def secure(c,admin,target):
 if admin not in settings(c)["admins"]:
  raise ValueError("فقط مدیر می‌تواند گزارش هوش مصنوعی کاربران را مشاهده کند")
 target=integer(int(target),1)
 if not c.execute("SELECT 1 FROM users WHERE id=?",(target,)).fetchone():
  raise ValueError("کاربر وجود ندارد")
 return target

def _date(timestamp):
 return datetime.datetime.fromtimestamp(int(timestamp),datetime.timezone.utc).strftime("%Y/%m/%d %H:%M UTC")

def _send(c,admin,text,buttons=None):
 return enqueue(c,admin,text,buttons or [],quick_nav=False)

def _back(target):
 return [[("↩️ استفاده از هوش مصنوعی","a:aiuse:"+str(target))],
         [("👤 پرونده کاربر","a:user:"+str(target))]]

def summary(c,admin,target):
 target=secure(c,admin,target)
 details=c.execute("""SELECT
  (SELECT COUNT(*) FROM ai_text_audit WHERE user_id=?) AS texts,
  (SELECT COUNT(*) FROM ai_media_jobs WHERE uid=? AND kind='image') AS images,
  (SELECT COUNT(*) FROM ai_media_jobs WHERE uid=? AND kind='video') AS videos,
  (SELECT COUNT(*) FROM ai_media_jobs WHERE uid=? AND status='failed') AS errors,
  (SELECT COUNT(*) FROM ai_usage WHERE user_id=?) AS usage_count""",
  (target,target,target,target,target)).fetchone()
 audit(c,"ai_admin_activity_view",str(admin)+":target="+str(target))
 return _send(c,admin,"🤖 استفاده از هوش مصنوعی — کاربر "+str(target)+
  "\n💬 گفتگوهای متنی ثبت‌شده: "+str(details["texts"])+
  "\n🖼 درخواست‌های تصویر: "+str(details["images"])+
  "\n🎬 درخواست‌های ویدیو: "+str(details["videos"])+
  "\n⚠️ درخواست‌های رسانه‌ای ناموفق: "+str(details["errors"])+
  "\n📊 موارد ثبت‌شده در سابقه مصرف: "+str(details["usage_count"])+
  "\n\nبرای مشاهده محتوای درخواست و نتیجه، یکی از بخش‌ها را باز کنید."
  "\nمحتوای متنی قدیمی که پیش از فعال‌سازی حافظه ذخیره نشده و "
  "فایل‌های خروجی قدیمیِ فاقد شناسه تلگرام، قابل بازیابی نیستند.",
  [[("💬 گفتگوهای متنی","a:aitext:"+str(target)+":0")],
   [("🖼 تصاویر ارسالی و دریافتی","a:aimedia:"+str(target)+":image:0")],
   [("🎬 ویدیوهای ارسالی و دریافتی","a:aimedia:"+str(target)+":video:0")]]+
  [[("👤 پرونده کاربر","a:user:"+str(target))]])

def _page(n):
 return integer(int(n),0,MAX_PAGE)

def text_list(c,admin,target,page):
 target=secure(c,admin,target);page=_page(page)
 total=c.execute("SELECT COUNT(*) FROM ai_text_audit WHERE user_id=?",(target,)).fetchone()[0]
 entries=c.execute("""SELECT id,question,created,reference FROM ai_text_audit
  WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?""",
 (target,PAGE_SIZE,page*PAGE_SIZE)).fetchall()
 rows=[]
 for r in entries:
  preview=" ".join(r["question"].split())[:34] or "بدون متن"
  rows.append([("📝 "+preview,"a:aidetail:"+str(target)+":"+str(r["id"]))])
 nav=[]
 if page>0:nav.append(("⬅️ جدیدتر","a:aitext:"+str(target)+":"+str(page-1)))
 if (page+1)*PAGE_SIZE<total:nav.append(("قدیمی‌تر ➡️","a:aitext:"+str(target)+":"+str(page+1)))
 if nav:rows.append(nav)
 return _send(c,admin,"💬 تاریخچه گفتگوی هوش مصنوعی کاربر "+str(target)+
  "\nتعداد درخواست‌های متنی ثبت‌شده: "+str(total)+
  "\nصفحه "+str(page+1)+(("\nدرخواست یا پاسخی در این صفحه وجود ندارد.") if not entries else ""),
  rows+_back(target))

def text_detail(c,admin,target,record_id):
 target=secure(c,admin,target);record_id=integer(int(record_id),1)
 source=c.execute("SELECT * FROM ai_text_audit WHERE user_id=? AND id=?",
   (target,record_id)).fetchone()
 if not source:raise ValueError("درخواست متنی کاربر یافت نشد")
 head=("🤖 گزارش گفتگوی کاربر "+str(target)+
   "\n📅 تاریخ: "+_date(source["created"])+
   "\n🔖 مرجع: "+source["reference"]+
   "\n🧑 درخواست کاربر:\n"+source["question"][:1600]+
   "\n🧠 مدل: "+(source["model"] or "ثبت نشده"))
 _send(c,admin,head,_back(target))
 answer=source["answer"]
 if answer:
  for idx in range(0,len(answer),3000):
   _send(c,admin,"🤖 پاسخ هوش مصنوعی ("+str(idx//3000+1)+"):\n"+
     answer[idx:idx+3000],_back(target) if idx+3000>=len(answer) else [])
 else:
  _send(c,admin,"⚠️ متن پاسخ برای این درخواست ثبت نشده است.",_back(target))

def media_list(c,admin,target,kind,page):
 target=secure(c,admin,target)
 if kind not in KIND:raise ValueError("نوع رسانه نامعتبر است")
 page=_page(page)
 total=c.execute("SELECT COUNT(*) FROM ai_media_jobs WHERE uid=? AND kind=?",(target,kind)).fetchone()[0]
 entries=c.execute("""SELECT id,prompt,status,created FROM ai_media_jobs
  WHERE uid=? AND kind=? ORDER BY id DESC LIMIT ? OFFSET ?""",
 (target,kind,PAGE_SIZE,page*PAGE_SIZE)).fetchall()
 rows=[[("📎 #"+str(r["id"])+" "+(" ".join(r["prompt"].split())[:28])+
         " ("+STATUS.get(r["status"],r["status"])+")",
         "a:aijob:"+str(target)+":"+str(r["id"]))] for r in entries]
 nav=[]
 if page>0:nav.append(("⬅️ جدیدتر","a:aimedia:"+str(target)+":"+kind+":"+str(page-1)))
 if (page+1)*PAGE_SIZE<total:nav.append(("قدیمی‌تر ➡️","a:aimedia:"+str(target)+":"+kind+":"+str(page+1)))
 if nav:rows.append(nav)
 return _send(c,admin,"📚 تاریخچه "+KIND[kind]+" کاربر "+str(target)+
  "\nتعداد کل: "+str(total)+" | صفحه "+str(page+1),
  rows+_back(target))

def media_detail(c,admin,target,record_id):
 target=secure(c,admin,target);record_id=integer(int(record_id),1)
 row=c.execute("SELECT * FROM ai_media_jobs WHERE uid=? AND id=?",(target,record_id)).fetchone()
 if not row:raise ValueError("درخواست رسانه پیدا نشد")
 usage=c.execute("SELECT model,points FROM ai_usage WHERE user_id=? AND reference=?",
 (target,"ai:media:"+str(row["update_id"]))).fetchone()
 text=("🤖 جزئیات استفاده از هوش مصنوعی\n"
  "کاربر: "+str(target)+" | درخواست: #"+str(row["id"])+
  "\nنوع: "+KIND.get(row["kind"],row["kind"])+
  "\nزمان: "+_date(row["created"])+
  "\nآخرین تغییر: "+_date(row["updated"])+
  "\nوضعیت: "+STATUS.get(row["status"],row["status"])+
  "\nمدل: "+(usage["model"] if usage else "نامشخص یا تکمیل‌نشده")+
  "\nامتیاز کسرشده: "+(str(usage["points"]) if usage else "ثبت نشده")+
  "\n📝 متن درخواست:\n"+row["prompt"][:1600]+
  "\n📤 رسانه ارسالی: "+("دارد" if row["file_id"] else "فقط درخواست متنی")+
  "\n📥 رسانه تولیدشده: "+("قابل نمایش" if row["output_file_id"] else
       "شناسه خروجی ثبت نشده (ممکن است قدیمی باشد یا درخواست ناموفق شده باشد)"))
 if row["error_code"]:text+="\n⚠️ خطای ثبت‌شده: "+row["error_code"]
 rows=[]
 if row["file_id"]:
  rows.append([("📤 نمایش رسانه ارسال‌شده","a:aifile:"+str(target)+":"+str(record_id)+":in")])
 if row["output_file_id"]:
  rows.append([("📥 نمایش خروجی هوش مصنوعی","a:aifile:"+str(target)+":"+str(record_id)+":out")])
 rows.append([("↩️ فهرست "+KIND.get(row["kind"],"رسانه"),
               "a:aimedia:"+str(target)+":"+row["kind"]+":0")])
 return _send(c,admin,text,rows+_back(target))

def replay_file(c,admin,target,record_id,direction):
 target=secure(c,admin,target);record_id=integer(int(record_id),1)
 if direction not in ("in","out"):raise ValueError("جهت فایل نامعتبر است")
 row=c.execute("SELECT * FROM ai_media_jobs WHERE id=? AND uid=?",(record_id,target)).fetchone()
 if not row:raise ValueError("فایل درخواست پیدا نشد")
 fid=row["file_id"] if direction=="in" else row["output_file_id"]
 if not fid:raise ValueError("این فایل قدیمی یا ناموفق، شناسه قابل بازیابی در تلگرام ندارد")
 kind=((row["input_media_type"] or ("photo" if row["kind"]=="image" else "video")) if direction=="in" else ("photo" if row["kind"]=="image" else "video"))
 if kind not in ("photo","video","document"):raise ValueError("نوع مدیای ذخیره‌شده نامعتبر است")
 method={"photo":"sendPhoto","video":"sendVideo","document":"sendDocument"}[kind]
 # Forward Telegram's opaque file reference, no remote fetches or raw media
 # stored in the local DB. The allowlist check happens again here.
 payload={"chat_id":admin,{"photo":"photo","video":"video","document":"document"}[kind]:fid}
 c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
   (secrets.token_hex(12),admin,method,json.dumps(payload)))
 return _send(c,admin,"✅ "+("رسانه ورودی" if direction=="in" else "خروجی هوش مصنوعی")+
   " به گفتگوی مدیریت ارسال می‌شود.",_back(target))

def route(c,admin,op,args):
 # Every entry path checks admin allowlist, including deep-link callbacks.
 if op=="aiuse" and len(args)==1:return summary(c,admin,args[0])
 if op=="aitext" and len(args)==2:return text_list(c,admin,*args)
 if op=="aidetail" and len(args)==2:return text_detail(c,admin,*args)
 if op=="aimedia" and len(args)==3:return media_list(c,admin,*args)
 if op=="aijob" and len(args)==2:return media_detail(c,admin,*args)
 if op=="aifile" and len(args)==3:return replay_file(c,admin,*args)
 raise ValueError("درخواست گزارش هوش مصنوعی نامعتبر است")

