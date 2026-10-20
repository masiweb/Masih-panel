#!/usr/bin/python3
"""Bounded multi-turn AI context, consent-based PDF/TXT responses, and history deletion."""
import base64,io,re,json,secrets
from PIL import Image,ImageDraw,ImageFont
from sales import now,enqueue,settings

FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
EXPORT_LIMIT=18000
CONTEXT_LIMIT=12500
CONTEXT_MESSAGES=22
LONG_WORDS=("کامل","جامع","مفصل","موشکافانه","تحلیل عمیق","بررسی دقیق",
            "گزارش","تحقیق","مقاله","پژوهش","پروپوزال","جزئیات کامل",
            "مرحله به مرحله","قدم به قدم","راهنمای جامع","آموزش کامل",
            "بسیار طولانی","با جزئیات","همه جزئیات","توضیحات کامل")

def setup(c):
 c.executescript("""CREATE TABLE IF NOT EXISTS ai_chat_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,role TEXT NOT NULL
 CHECK(role IN ('user','assistant')),content TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',
 reference TEXT NOT NULL,created INTEGER NOT NULL,UNIQUE(reference,role));
 CREATE INDEX IF NOT EXISTS ai_chat_history_user ON ai_chat_history(user_id,id);
 CREATE TABLE IF NOT EXISTS ai_long_requests(
 user_id INTEGER PRIMARY KEY,update_id INTEGER NOT NULL,
 question TEXT NOT NULL,created INTEGER NOT NULL);""")

def is_long(question):
 return isinstance(question,str) and (len(question.strip())>=350 or
      (len(question.strip())>=25 and any(word in question.lower() for word in LONG_WORDS)))

def previous(c,user_id):
 rows=c.execute("""SELECT role,content FROM ai_chat_history WHERE user_id=?
 ORDER BY id DESC LIMIT ?""",(user_id,CONTEXT_MESSAGES)).fetchall()
 picked=[];size=0
 for r in rows:
  content=r["content"]
  if size+len(content)>CONTEXT_LIMIT:
   if picked:break
   content=content[-CONTEXT_LIMIT:]
  picked.append({"role":r["role"],"content":content})
  size+=len(content)
 return picked[::-1]

def record(c,uid,question,answer,model,reference):
 # Caller guarantees API output and point/quota debit succeeded before saving.
 c.execute("INSERT OR IGNORE INTO ai_chat_history(user_id,role,content,model,reference,created) VALUES(?,?,?,?,?,?)",
   (uid,"user",question,"",reference,now()))
 c.execute("INSERT OR IGNORE INTO ai_chat_history(user_id,role,content,model,reference,created) VALUES(?,?,?,?,?,?)",
   (uid,"assistant",answer,model,reference,now()))

def clear(c,uid):
 c.execute("DELETE FROM ai_chat_history WHERE user_id=?",(uid,))
 c.execute("DELETE FROM ai_long_requests WHERE user_id=?",(uid,))
 return enqueue(c,uid,"🗑 سابقه گفتگوهای هوش مصنوعی شما پاک شد. پرسش‌های جدید بدون استفاده از گفتگوهای قبلی پاسخ داده می‌شوند.",
   [[("💬 گفتگوی جدید","fun:ai:text"),("🏠 منوی اصلی","home")]])

def offer(c,uid,question,update_id):
 c.execute("""INSERT INTO ai_long_requests(user_id,update_id,question,created)
 VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
 update_id=excluded.update_id,question=excluded.question,created=excluded.created""",
 (uid,update_id,question,now()))
 enqueue(c,uid,"📚 درخواست شما به پاسخ مفصل نیاز دارد.\n"
       "آیا ترجیح می‌دهید پاسخ کامل را به‌صورت دو فایل PDF و متن قابل‌کپی (TXT) دریافت کنید؟\n"
       "در هر دو حالت، هزینه تنها یک پاسخ موفق طبق پلن شماست.",
       [[("📄 بله، PDF و فایل متنی","ai:export:yes")],
        [("💬 خیر، پاسخ در همین گفتگو","ai:export:no")],
        [("🗑 پاک‌کردن سابقه","ai:clear"),("🏠 منوی اصلی","home")]],
       key="ai-export-offer:"+str(update_id),quick_nav=False)

def consume_offer(c,uid,cmd):
 if cmd not in ("ai:export:yes","ai:export:no"):
  raise ValueError("گزینه فایل هوش مصنوعی نامعتبر است")
 row=c.execute("SELECT * FROM ai_long_requests WHERE user_id=?",(uid,)).fetchone()
 if not row:raise ValueError("درخواست مفصل فعالی وجود ندارد؛ از بخش گفتگوی هوش مصنوعی دوباره سؤال کنید")
 if now()-row["created"]>86400:
  c.execute("DELETE FROM ai_long_requests WHERE user_id=?",(uid,))
  raise ValueError("این درخواست منقضی شده است؛ سؤال را دوباره ارسال کنید")
 # The caller deletes the offer after SUCCESSFUL answer generation/queue.
 return row["question"],row["update_id"],cmd.endswith(":yes")

def _wrap_line(line,font,width,draw):
 if not line.strip():return [""]
 words=line.split()
 out=[];current=""
 for word in words:
  candidate=(current+" "+word).strip()
  if draw.textlength(candidate,font=font,direction="rtl")<=width:
   current=candidate
  else:
   if current:out.append(current);current=""
   if draw.textlength(word,font=font,direction="rtl")<=width:
    current=word
   else:
    piece=""
    for ch in word:
     if piece and draw.textlength(piece+ch,font=font,direction="rtl")>width:
      out.append(piece);piece=""
     piece+=ch
    current=piece
 if current:out.append(current)
 return out

def make_pdf(answer):
 """Image-backed A4 PDF with real Persian shaping via Pillow libraqm.
 TXT counterpart remains fully searchable, copyable and UTF-8.
 No files or font material are distributed from the server."""
 if not isinstance(answer,str) or not answer.strip():raise ValueError("متن پاسخ برای فایل خالی است")
 if len(answer)>EXPORT_LIMIT:answer=answer[:EXPORT_LIMIT]+"\n\n[باقی پاسخ در سقف مجاز خروجی قرار نگرفت]"
 font=ImageFont.truetype(FONT,27,layout_engine=ImageFont.Layout.RAQM)
 head_font=ImageFont.truetype(FONT,30,layout_engine=ImageFont.Layout.RAQM)
 W,H=1240,1754
 left,right,top,bottom=95,95,120,90
 dummy=Image.new("RGB",(1,1),"white");measure=ImageDraw.Draw(dummy)
 wrapped=[]
 for paragraph in answer.replace("\r\n","\n").replace("\r","\n").split("\n"):
  wrapped.extend(_wrap_line(paragraph,font,W-left-right,measure))
 if len(wrapped)>1100:raise ValueError("پاسخ برای ساخت PDF بیش از حد طولانی است")
 pages=[];page=None;drawer=None;y=0
 def new_page():
  canvas=Image.new("RGB",(W,H),"white")
  pen=ImageDraw.Draw(canvas)
  pen.text((W-right,55),"پاسخ هوش مصنوعی - مسیحا",font=head_font,fill="#17304d",direction="rtl",anchor="ra")
  pen.line((left,98,W-right,98),fill="#dbe1ea",width=3)
  return canvas,pen,top
 page,drawer,y=new_page()
 for line in wrapped:
  if y+51>H-bottom:
   pages.append(page);page,drawer,y=new_page()
  if line:
   # anchor ra binds text's right edge to the right margin.
   drawer.text((W-right,y),line,font=font,fill="#222222",direction="rtl",anchor="ra",
               spacing=7,stroke_width=0)
  y+=48 if line else 28
 pages.append(page)
 for i,img in enumerate(pages,1):
  pen=ImageDraw.Draw(img)
  pen.text((W//2,H-42),str(i)+" / "+str(len(pages)),font=font,anchor="mm",fill="#687489")
 out=io.BytesIO()
 pages[0].save(out,format="PDF",save_all=True,append_images=pages[1:],resolution=144)
 raw=out.getvalue()
 if not raw.startswith(b"%PDF") or len(raw)>12_000_000:
  raise ValueError("ساخت فایل PDF موفق نبود")
 return raw

def queue_files(c,uid,answer,ref):
 textbytes=("\ufeff"+answer).encode("utf-8")
 if len(textbytes)>125000:raise ValueError("پاسخ متنی بیش از اندازه است")
 pdf=make_pdf(answer)
 for extension,payload in (("txt",textbytes),("pdf",pdf)):
  c.execute("""INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload)
   VALUES(?,?,?,?)""",("ai-export:"+ref+":"+extension,uid,"file_b64",json.dumps({
    "filename":"masiha-ai-answer-"+ref.replace(":","-")+"."+extension,
    "data":base64.b64encode(payload).decode("ascii"),
   })))
 return len(pdf),len(textbytes)
