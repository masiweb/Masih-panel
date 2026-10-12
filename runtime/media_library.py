#!/usr/bin/env python3
"""Telegram-native music/video library; only metadata and Telegram file_ids in SQLite.

Every reader enforces ownership/playlist membership. All media is user-submitted
and requires a rights confirmation before public listing. No media bytes are
downloaded to, or persisted on, the VPN host.
"""
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlparse

FIELDS={
 "music":[("title","نام موزیک"),("artist","نام خواننده"),("genre","ژانر یا سبک"),("lyrics","متن موزیک")],
 "film":[("title","نام فیلم یا سریال"),("media_type","نوع: سینمایی یا سریال"),
         ("season","شماره فصل (اگر سریال است)"),("episode","شماره قسمت (اگر سریال است)"),
         ("actors","نام بازیگران"),("year","سال ساخت"),("language","زبان"),
         ("genre","ژانر"),("dubbed","دوبله دارد؟ بله یا خیر"),
         ("subtitles","زیرنویس دارد؟ بله یا خیر"),("director","کارگردان"),
         ("description","خلاصه داستان")]
}
GENRE_LABEL={"music":"🎵 موزیک","film":"🎬 فیلم"}
MAX_PAGE=20
DAILY_METADATA_LIMIT=30
DAILY_LIKE_LIMIT=20

def setup(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS media_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('music','film')),
  telegram_method TEXT NOT NULL CHECK(telegram_method IN ('sendAudio','sendVoice','sendVideo','sendVideoNote','sendDocument','link')),
  file_id TEXT NOT NULL DEFAULT '',
  unique_id TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  lyrics TEXT NOT NULL DEFAULT '',
  media_type TEXT NOT NULL DEFAULT '',
  season INTEGER NOT NULL DEFAULT 0,
  episode INTEGER NOT NULL DEFAULT 0,
  actors TEXT NOT NULL DEFAULT '',
  year INTEGER NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT '',
  dubbed TEXT NOT NULL DEFAULT '',
  subtitles TEXT NOT NULL DEFAULT '',
  director TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  cover_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','removed')),
  rights_ok INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL,
  UNIQUE(kind,unique_id)
 );
 CREATE INDEX IF NOT EXISTS media_feed ON media_items(kind,status,created DESC);
 CREATE INDEX IF NOT EXISTS media_owner ON media_items(owner_id,kind,status);
 CREATE TABLE IF NOT EXISTS media_metadata_reward(
  media_id INTEGER NOT NULL,field TEXT NOT NULL,
  PRIMARY KEY(media_id,field)
 );
 CREATE TABLE IF NOT EXISTS media_likes(
  user_id INTEGER NOT NULL,media_id INTEGER NOT NULL,
  created INTEGER NOT NULL,PRIMARY KEY(user_id,media_id)
 );
 CREATE TABLE IF NOT EXISTS media_comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,media_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,body TEXT NOT NULL,created INTEGER NOT NULL,
  removed INTEGER NOT NULL DEFAULT 0
 );
 CREATE INDEX IF NOT EXISTS media_comments_latest ON media_comments(media_id,id DESC);
 CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('music','film')),
  name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
  private INTEGER NOT NULL,invite_token TEXT NOT NULL UNIQUE,
  removed INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL
 );
 CREATE INDEX IF NOT EXISTS playlists_pop ON playlists(kind,private,removed,created DESC);
 CREATE TABLE IF NOT EXISTS playlist_entries(
  playlist_id INTEGER NOT NULL,media_id INTEGER NOT NULL,created INTEGER NOT NULL,
  PRIMARY KEY(playlist_id,media_id)
 );
 CREATE TABLE IF NOT EXISTS playlist_members(
  playlist_id INTEGER NOT NULL,user_id INTEGER NOT NULL,created INTEGER NOT NULL,
  PRIMARY KEY(playlist_id,user_id)
 );
 CREATE TABLE IF NOT EXISTS playlist_likes(
  playlist_id INTEGER NOT NULL,user_id INTEGER NOT NULL,created INTEGER NOT NULL,
  PRIMARY KEY(playlist_id,user_id)
 );
 CREATE TABLE IF NOT EXISTS playlist_comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,playlist_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,body TEXT NOT NULL,removed INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL
 );
 CREATE TABLE IF NOT EXISTS media_reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,media_id INTEGER NOT NULL,
  reporter_id INTEGER NOT NULL,reason TEXT NOT NULL,created INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', UNIQUE(media_id,reporter_id)
 );
 CREATE TABLE IF NOT EXISTS media_settings(k TEXT PRIMARY KEY,v INTEGER NOT NULL);
 INSERT OR IGNORE INTO media_settings(k,v) VALUES
  ('field_points',2),('like_points',1),('max_media_mb',0);
 """)

def _sales():
 import sales
 return sales

def clean(value,maximum=160):
 if not isinstance(value,str):raise ValueError("لطفاً متن معتبر ارسال کنید")
 value=value.strip()
 if len(value)>maximum:raise ValueError("متن طولانی است؛ حداکثر "+str(maximum)+" کاراکتر")
 return value

def setting(c,key):
 return c.execute("SELECT v FROM media_settings WHERE k=?",(key,)).fetchone()[0]

def _buttons(c,uid,items):
 # Retain same Telegram inline-button format as existing bot.
 return items

def send(c,uid,message,buttons=None):
 _sales().enqueue(c,uid,message,buttons or [[("↩️ سرگرمی","fun")]])

def _state(c,uid,value):
 c.execute("UPDATE users SET state=? WHERE id=?",(value,uid))

def _owner(c,uid,item_id,allow_draft=False):
 row=c.execute("SELECT * FROM media_items WHERE id=?",(int(item_id),)).fetchone()
 if not row or row["owner_id"]!=uid or row["status"]=="removed" or (row["status"]!="published" and not allow_draft):
  raise ValueError("اجازه دسترسی به این فایل را ندارید")
 return row

def _media(c,mid):
 row=c.execute("SELECT * FROM media_items WHERE id=? AND status='published'",(int(mid),)).fetchone()
 if not row:raise ValueError("این فایل موجود نیست یا حذف شده است")
 return row

def _playlist(c,uid,pid):
 p=c.execute("SELECT * FROM playlists WHERE id=? AND removed=0",(int(pid),)).fetchone()
 if not p:raise ValueError("پلی‌لیست موجود نیست")
 if p["private"] and p["owner_id"]!=uid and not c.execute(
     "SELECT 1 FROM playlist_members WHERE playlist_id=? AND user_id=?",(pid,uid)).fetchone():
  raise ValueError("این پلی‌لیست خصوصی است؛ ابتدا دعوت‌نامه معتبر را باز کنید")
 return p

def _queue_file(c,uid,method,file_id,caption,buttons):
 if method=="link":
  raise AssertionError("use send for external URLs")
 field={"sendAudio":"audio","sendVoice":"voice","sendVideo":"video","sendVideoNote":"video_note","sendDocument":"document"}[method]
 markup={"inline_keyboard":[[{"text":label,"callback_data":cmd} for label,cmd in row] for row in buttons]}
 payload={"chat_id":uid,field:file_id,"caption":caption[:950],"reply_markup":markup}
 c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
           (secrets.token_hex(12),uid,method,json.dumps(payload,ensure_ascii=False)))

def kind_menu(c,uid,kind):
 send(c,uid,GENRE_LABEL[kind]+" | کتابخانه رسانه مسیحا\nفایل را مستقیماً ارسال یا فوروارد کنید؛ روی سرور VPN ذخیره نمی‌شود.",
  [[("➕ ارسال رسانه","med:upload:"+kind),("🆕 آخرین ۲۰ مورد","med:latest:"+kind)],
   [("📂 رسانه‌های من","med:mine:"+kind),("📚 پلی‌لیست‌ها","med:lists:"+kind)],
   [("🔎 جست‌وجو","med:search:"+kind),("🔥 برترین‌ها","med:top:"+kind)],
   [("🏆 برترین کاربران","med:leaders:"+kind),("↩️ سرگرمی","fun")]])

def playlist_menu(c,uid,kind):
 send(c,uid,"📚 پلی‌لیست‌های "+GENRE_LABEL[kind],
 [[("🌎 ۵ پلی‌لیست عمومی تازه","med:plfeed:"+kind),("📚 پلی‌لیست‌های من","med:plmine:"+kind)],
  [("🔥 محبوب‌ترین پلی‌لیست‌ها","med:pltop:"+kind)],
  [("➕ ساخت عمومی","med:plnew:"+kind+":0"),("🔒 ساخت خصوصی","med:plnew:"+kind+":1")],
  [("↩️ بازگشت به رسانه‌ها","med:"+kind)]])

def _count(c,table,col,value):
 return c.execute("SELECT COUNT(*) FROM "+table+" WHERE "+col+"=?",(value,)).fetchone()[0]

def _media_summary(row):
 out=GENRE_LABEL[row["kind"]]+" "+(row["title"] or "بدون نام")
 if row["kind"]=="music":
  if row["artist"]:out+=" | "+row["artist"]
 else:
  out+=" | "+("سریال" if row["media_type"]=="series" else "سینمایی")
  if row["media_type"]=="series":out+=" فصل "+str(row["season"])+" قسمت "+str(row["episode"])
  if row["year"]:out+=" ("+str(row["year"])+")"
 return out

def latest(c,uid,kind,scope):
 if scope=="mine":
  rows=c.execute("SELECT * FROM media_items WHERE kind=? AND owner_id=? AND status='published' ORDER BY id DESC LIMIT 20",(kind,uid)).fetchall()
 elif scope=="top":
  rows=c.execute("""SELECT m.* FROM media_items m LEFT JOIN media_likes l ON m.id=l.media_id
    WHERE m.kind=? AND m.status='published' GROUP BY m.id ORDER BY COUNT(l.user_id) DESC,m.id DESC LIMIT 20""",(kind,)).fetchall()
 else:
  rows=c.execute("SELECT * FROM media_items WHERE kind=? AND status='published' ORDER BY id DESC LIMIT 20",(kind,)).fetchall()
 buttons=[[(_media_summary(r)[:55],"med:play:"+str(r["id"]))] for r in rows]
 buttons.append([("↩️ رسانه‌ها","med:"+kind)])
 send(c,uid,("📂 رسانه‌های من" if scope=="mine" else "🔥 برترین رسانه‌ها" if scope=="top" else "🆕 ۲۰ رسانه آخر")+
      ("\nهنوز موردی ثبت نشده است." if not rows else ""),buttons)

def _positive_int(x,ceiling=200):
 v=clean(x,10)
 if not v.isdigit() or not 1<=int(v)<=ceiling:raise ValueError("شماره واردشده معتبر نیست")
 return int(v)

def _url(value):
 candidate=clean(value,1800)
 p=urlparse(candidate)
 if p.scheme!="https" or not p.netloc or p.username or p.password or p.fragment:
  raise ValueError("فقط لینک مستقیم HTTPS معتبر پذیرفته می‌شود")
 if p.hostname in ("localhost",) or not p.hostname or p.hostname.endswith((".local",".internal",".localhost")):
  raise ValueError("لینک داخلی قابل قبول نیست")
 return candidate

def _guess(message,expected=None):
 """Telegram-native file only; reject arbitrary documents & animated GIFs."""
 for key,method,kind in (("audio","sendAudio","music"),("voice","sendVoice","music"),
                          ("video","sendVideo","film"),("video_note","sendVideoNote","film")):
  part=message.get(key)
  if isinstance(part,dict) and part.get("file_id") and part.get("file_unique_id"):
   if expected and expected!=kind:raise ValueError("فرمت فایل با بخش انتخابی مطابقت ندارد")
   return kind,method,part
 part=message.get("document") or {}
 if part.get("file_id") and part.get("file_unique_id"):
  mime=part.get("mime_type","").lower()
  extension=str(part.get("file_name","")).lower().rsplit(".",1)[-1]
  if mime.startswith("audio/") or extension in ("mp3","m4a","flac","ogg","wav","aac"):
   kind="music";method="sendDocument"
  elif mime.startswith("video/") or extension in ("mp4","mkv","mov","webm","avi","m4v"):
   kind="film";method="sendDocument"
  else:return None
  if expected and expected!=kind:raise ValueError("فرمت فایل با بخش انتخابی مطابقت ندارد")
  return kind,method,part
 return None

def is_media_message(m):
 return bool(_guess(m))

def _prompt(c,uid,row,step=0):
 fields=FIELDS[row["kind"]]
 if step>=len(fields):
  _state(c,uid,"med:cover:"+str(row["id"]))
  send(c,uid,"🖼 در صورت تمایل تصویر کاور فیلم/سریال را ارسال کنید. برای موزیک نیز کاور اختیاری است.",
       [[("✅ ثبت بدون کاور","med:publish:"+str(row["id"])),
         ("❌ انصراف و حذف پیش‌نویس","med:cancel:"+str(row["id"]))],
        [("↩️ بازگشت","med:"+row["kind"])]])
  return
 field,label=fields[step]
 _state(c,uid,"med:meta:"+str(row["id"])+":"+str(step))
 send(c,uid,"اطلاعات اختیاری ("+str(step+1)+"/"+str(len(fields))+")\n"+label+
        "\nبرای هر فیلد واقعی و غیرتکراری امتیاز می‌گیرید. اگر نمی‌دانید، رد کنید.",
      [[("⏭ رد این فیلد","med:skip:"+str(row["id"])+":"+str(step)),
        ("⏩ رد همه و انتشار","med:finish:"+str(row["id"]))],
       [("↩️ کتابخانه","med:"+row["kind"])]])
 
def accept(c,uid,message,expected=None):
 kind_method=_guess(message,expected)
 if not kind_method:
  # An external HTTPS URL is an index entry, not a downloaded Telegram file.
  body=message.get("text","")
  if not expected or not body.strip().startswith("https://"):
   raise ValueError("لطفاً فایل صوتی/تصویری یا لینک HTTPS مستقیم ارسال کنید")
  url=_url(body)
  kind=expected;method="link"
  part={"file_id":"","file_unique_id":hashlib.sha256(url.encode()).hexdigest(),
        "file_name":url.rsplit("/",1)[-1],"title":""}
 else:kind,method,part=kind_method;url=""
 unique=part["file_unique_id"]
 existing=c.execute("SELECT id,owner_id,status FROM media_items WHERE kind=? AND unique_id=?",(kind,unique)).fetchone()
 if existing:
  if existing["owner_id"]==uid and existing["status"]=="draft":
   row=c.execute("SELECT * FROM media_items WHERE id=?",(existing["id"],)).fetchone()
   return _prompt(c,uid,row)
  raise ValueError("این فایل قبلاً در کتابخانه ثبت شده است؛ ارسال تکراری امتیاز ندارد")
 name=clean(part.get("title") or str(part.get("file_name","")).rsplit(".",1)[0],160)
 if not name:name="بدون نام"
 # Pending submissions are not public until rights checkbox and publish.
 mid=c.execute("""INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,
     source_url,title,artist,created) VALUES(?,?,?,?,?,?,?,?,?)""",
     (uid,kind,method,part.get("file_id",""),unique,url,name,
      clean(part.get("performer",""),120),_sales().now())).lastrowid
 row=c.execute("SELECT * FROM media_items WHERE id=?",(mid,)).fetchone()
 return _prompt(c,uid,row)

def _daily_points(c,uid,kind):
 start=(_sales().now()//86400)*86400
 return c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=? AND kind=? AND created>=?",
                  (uid,kind,start)).fetchone()[0]

def _mark_metadata(c,mid,field):
 # Merely records a completed field; points are NOT awarded for draft content.
 c.execute("INSERT OR IGNORE INTO media_metadata_reward(media_id,field) VALUES(?,?)",(mid,field))

def _reward_published_metadata(c,uid,mid):
 from gamification import award
 points=setting(c,"field_points")
 if not points:return
 fields=c.execute("SELECT field FROM media_metadata_reward WHERE media_id=?",(mid,)).fetchall()
 for (field,) in fields:
  available=DAILY_METADATA_LIMIT-_daily_points(c,uid,"media_metadata")
  if available<=0:break
  award(c,uid,min(points,available),"media_metadata","metadata:"+str(mid)+":"+field,_sales().now())

def set_field(c,uid,mid,step,value):
 row=_owner(c,uid,mid,True)
 if row["status"]!="draft":raise ValueError("اطلاعات این فایل منتشر شده است")
 fields=FIELDS[row["kind"]]
 if not 0<=step<len(fields):raise ValueError("مرحله اطلاعات نامعتبر است")
 key,label=fields[step];value=clean(value,1800 if key in ("lyrics","description") else 250)
 if value:
  if key in ("year","season","episode"):
   value=_positive_int(value,2100 if key=="year" else 1000)
  if key=="media_type":
   v=value.casefold()
   if v in ("سینمایی","film","movie"):value="movie"
   elif v in ("سریال","series","tv"):value="series"
   else:raise ValueError("فقط «سریال» یا «سینمایی» وارد کنید")
  if key in ("dubbed","subtitles"):
   if value not in ("بله","خیر","yes","no","true","false"):raise ValueError("بله یا خیر وارد کنید")
   value="بله" if value in ("بله","yes","true") else "خیر"
  c.execute("UPDATE media_items SET "+key+"=? WHERE id=?",(value,mid))
  _mark_metadata(c,mid,key)
 nextrow=c.execute("SELECT * FROM media_items WHERE id=?",(mid,)).fetchone()
 _prompt(c,uid,nextrow,step+1)

def _cover(c,uid,mid,message):
 row=_owner(c,uid,mid,True)
 if row["status"]!="draft":raise ValueError("این فایل قبلاً منتشر شده است")
 photos=message.get("photo") or []
 if not photos or not photos[-1].get("file_id"):
  raise ValueError("تصویر کاور را به‌صورت عکس تلگرام بفرستید یا «ثبت بدون کاور» را بزنید")
 c.execute("UPDATE media_items SET cover_id=? WHERE id=?",(photos[-1]["file_id"],mid))
 _mark_metadata(c,mid,"cover")
 return _ready(c,uid,mid)

def _ready(c,uid,mid):
 row=_owner(c,uid,mid,True)
 if row["status"]!="draft":raise ValueError("فایل قبلاً ثبت شده است")
 _state(c,uid,"med:rights:"+str(mid))
 send(c,uid,"⚖️ لطفاً تأیید کنید که حق انتشار/اشتراک این رسانه را دارید و مسئول رعایت حقوق صاحبان اثر هستید. بدون تأیید، فایل در فهرست عمومی نمایش داده نمی‌شود.",
      [[("✅ حق انتشار دارم؛ منتشر شود","med:confirm:"+str(mid))],
       [("❌ انصراف و حذف پیش‌نویس","med:cancel:"+str(mid))]])
 
def _preview(c,uid,row):
 likes=_count(c,"media_likes","media_id",row["id"])
 comments=c.execute("SELECT COUNT(*) FROM media_comments WHERE media_id=? AND removed=0",(row["id"],)).fetchone()[0]
 caption=_media_summary(row)+"\n❤️ "+str(likes)+" | 💬 "+str(comments)
 if row["genre"]:caption+="\nسبک: "+row["genre"]
 if row["kind"]=="music" and row["lyrics"]:caption+="\nمتن: "+row["lyrics"][:330]
 if row["kind"]=="film":
  if row["actors"]:caption+="\nبازیگران: "+row["actors"][:180]
  if row["director"]:caption+="\nکارگردان: "+row["director"]
  if row["description"]:caption+="\n"+row["description"][:230]
 return caption

def play(c,uid,mid):
 row=_media(c,mid)
 buttons=[
  [("❤️ لایک","med:like:"+str(mid)),("💬 نظرات","med:comments:"+str(mid))],
  [("✍️ نظر جدید","med:comment:"+str(mid)),("➕ به پلی‌لیست من","med:addmenu:"+str(mid))],
  [("🚩 گزارش","med:report:"+str(mid))]]
 if uid==row["owner_id"]:buttons.append([("🗑 حذف فایل من","med:remove:"+str(mid))])
 buttons.append([("↩️ "+GENRE_LABEL[row["kind"]],"med:"+row["kind"])])
 description=_preview(c,uid,row)
 if row["telegram_method"]=="link":
  send(c,uid,description+"\n🔗 لینک رسانه:\n"+row["source_url"],buttons)
 else:
  _queue_file(c,uid,row["telegram_method"],row["file_id"],description,buttons)
 if row["cover_id"]:
  c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
     (secrets.token_hex(12),uid,"sendPhoto",json.dumps({
      "chat_id":uid,"photo":row["cover_id"],"caption":"🖼 کاور "+row["title"][:100]})))

def playlists(c,uid,kind,scope):
 if scope=="mine":
  rows=c.execute("SELECT * FROM playlists WHERE kind=? AND owner_id=? AND removed=0 ORDER BY id DESC LIMIT 20",(kind,uid)).fetchall()
 elif scope=="top":
  rows=c.execute("""SELECT p.* FROM playlists p LEFT JOIN playlist_likes l ON l.playlist_id=p.id
   WHERE p.kind=? AND p.private=0 AND p.removed=0 GROUP BY p.id ORDER BY COUNT(l.user_id) DESC,p.id DESC LIMIT 20""",(kind,)).fetchall()
 else:
  rows=c.execute("SELECT * FROM playlists WHERE kind=? AND private=0 AND removed=0 ORDER BY id DESC LIMIT 5",(kind,)).fetchall()
 send(c,uid,"📚 "+("پلی‌لیست‌های من" if scope=="mine" else "محبوب‌ترین پلی‌لیست‌ها" if scope=="top" else "۵ پلی‌لیست عمومی تازه")+
  ("\nهنوز پلی‌لیستی وجود ندارد" if not rows else ""),
  [[("📋 "+r["name"][:42]+(" 🔒" if r["private"] else ""),"med:pl:"+str(r["id"]))] for r in rows]+
  [[("↩️ پلی‌لیست‌ها","med:lists:"+kind)]])

def playlist_view(c,uid,pid):
 p=_playlist(c,uid,pid)
 likes=_count(c,"playlist_likes","playlist_id",pid)
 entries=c.execute("""SELECT m.* FROM playlist_entries e JOIN media_items m ON m.id=e.media_id
   WHERE e.playlist_id=? AND m.status='published' ORDER BY e.created DESC,m.id DESC LIMIT 40""",(pid,)).fetchall()
 actions=[[( _media_summary(x)[:52],"med:play:"+str(x["id"]))] for x in entries]
 actions += [[("❤️ لایک پلی‌لیست","med:pllike:"+str(pid)),("💬 نظرات","med:plcomments:"+str(pid))],
            [("✍️ نظر جدید","med:plcomment:"+str(pid))]]
 if uid==p["owner_id"]:
  actions.append([("🔗 لینک دعوت" if p["private"] else "📢 اشتراک‌گذاری","med:invite:"+str(pid)),
                  ("🗑 حذف پلی‌لیست","med:plremove:"+str(pid))])
 actions.append([("↩️ پلی‌لیست‌ها","med:lists:"+p["kind"])])
 send(c,uid,("🔒 " if p["private"] else "🌎 ")+"پلی‌لیست: "+p["name"]+
   "\n❤️ "+str(likes)+" | 🎞 "+str(len(entries))+" رسانه"+
   ("\n"+p["description"] if p["description"] else ""),actions)

def _like(c,uid,kind,id):
 table="media_likes" if kind=="media" else "playlist_likes"
 target="media_id" if kind=="media" else "playlist_id"
 owner=c.execute("SELECT owner_id FROM "+("media_items" if kind=="media" else "playlists")+" WHERE id=?",(id,)).fetchone()
 if not owner:raise ValueError("موردی برای پسندیدن وجود ندارد")
 if kind=="media":_media(c,id)
 else:_playlist(c,uid,id)
 if owner["owner_id"]==uid:raise ValueError("نمی‌توانید رسانه یا پلی‌لیست خودتان را لایک کنید")
 if c.execute("SELECT 1 FROM "+table+" WHERE "+target+"=? AND user_id=?",(id,uid)).fetchone():
  c.execute("DELETE FROM "+table+" WHERE "+target+"=? AND user_id=?",(id,uid))
  return False
 c.execute("INSERT INTO "+table+"("+target+",user_id,created) VALUES(?,?,?)",(id,uid,_sales().now()))
 # Reward content creators only, capped daily and uniquely by (liker, item).
 from gamification import award
 if setting(c,"like_points") and _daily_points(c,owner["owner_id"],"media_like")<DAILY_LIKE_LIMIT:
  award(c,owner["owner_id"],min(setting(c,"like_points"),DAILY_LIKE_LIMIT-_daily_points(c,owner["owner_id"],"media_like")),
        "media_like","liked:"+kind+":"+str(id)+":"+str(uid),_sales().now())
 return True

def _comment(c,uid,kind,id,body):
 body=clean(body,750)
 if len(body)<2:raise ValueError("نظر حداقل دو حرف باشد")
 if kind=="media":_media(c,id);table="media_comments";fk="media_id"
 else:_playlist(c,uid,id);table="playlist_comments";fk="playlist_id"
 last=c.execute("SELECT created FROM "+table+" WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
 if last and _sales().now()-last["created"]<60:raise ValueError("بین دو نظر حداقل یک دقیقه فاصله بگذارید")
 c.execute("INSERT INTO "+table+"("+fk+",user_id,body,created) VALUES(?,?,?,?)",(id,uid,body,_sales().now()))

def comments(c,uid,kind,id):
 if kind=="media":_media(c,id);table="media_comments";fk="media_id"
 else:_playlist(c,uid,id);table="playlist_comments";fk="playlist_id"
 rows=c.execute("SELECT user_id,body FROM "+table+" WHERE "+fk+"=? AND removed=0 ORDER BY id DESC LIMIT 12",(id,)).fetchall()
 back="med:play:"+str(id) if kind=="media" else "med:pl:"+str(id)
 send(c,uid,"💬 تازه‌ترین نظرات:\n"+("\n\n".join("👤 "+str(r["user_id"])+": "+r["body"][:250] for r in rows) or "نظری ثبت نشده است"),
      [[("✍️ ثبت نظر","med:"+("comment:" if kind=="media" else "plcomment:")+str(id))],
       [("↩️ بازگشت",back)]])

def leaders(c,uid,kind):
 rows=c.execute("""SELECT p.owner_id,u.name,COUNT(DISTINCT p.id) AS playlists,
   COUNT(l.user_id) AS likes FROM playlists p
   JOIN users u ON u.id=p.owner_id
   LEFT JOIN playlist_likes l ON l.playlist_id=p.id
   WHERE p.kind=? AND p.private=0 AND p.removed=0
   GROUP BY p.owner_id ORDER BY likes DESC,playlists DESC LIMIT 20""",(kind,)).fetchall()
 listing="\n".join(str(i)+". "+(r["name"] or "کاربر")+" | "+str(r["playlists"])+
                   " پلی‌لیست | ❤️ "+str(r["likes"]) for i,r in enumerate(rows,1))
 send(c,uid,"🏆 برترین سازندگان پلی‌لیست\n"+(listing or "هنوز رتبه‌بندی موجود نیست"),
      [[("↩️ رسانه‌ها","med:"+kind)]])

def search(c,uid,kind,term):
 term=clean(term,120)
 if len(term)<2:raise ValueError("عبارت جست‌وجو حداقل ۲ حرف باشد")
 # Escape user-provided SQL LIKE wildcards; bound parameter only.
 escaped=term.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
 like="%"+escaped+"%"
 rows=c.execute("""SELECT * FROM media_items WHERE kind=? AND status='published'
  AND (title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR actors LIKE ? ESCAPE '\\'
       OR genre LIKE ? ESCAPE '\\' OR lyrics LIKE ? ESCAPE '\\' OR director LIKE ? ESCAPE '\\'
       OR description LIKE ? ESCAPE '\\')
  ORDER BY id DESC LIMIT 20""",(kind,*([like]*7))).fetchall()
 playlists=c.execute("""SELECT * FROM playlists WHERE kind=? AND removed=0 AND (private=0 OR owner_id=? OR EXISTS(
   SELECT 1 FROM playlist_members WHERE playlist_id=playlists.id AND user_id=?))
   AND name LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 10""",(kind,uid,uid,like)).fetchall()
 buttons=[[(_media_summary(x)[:51],"med:play:"+str(x["id"]))] for x in rows]
 buttons += [[("📚 "+p["name"][:47],"med:pl:"+str(p["id"]))] for p in playlists]
 send(c,uid,"🔎 نتیجه جست‌وجو: "+term+"\n"+("چیزی پیدا نشد." if not buttons else str(len(buttons))+" مورد"),
      buttons+[[("↩️ رسانه‌ها","med:"+kind)]])

def callback(c,uid,cmd):
 parts=cmd.split(":")
 if len(parts)<2 or parts[0]!="med":raise ValueError("فرمان رسانه نامعتبر است")
 op=parts[1];x=parts[2] if len(parts)>2 else ""
 if op in ("music","film","latest","mine","top","lists","plfeed","plmine","pltop",
           "leaders","play","comments","plcomments","pl","addmenu"):
  _state(c,uid,"")
 if op in ("music","film"):return kind_menu(c,uid,op)
 if op=="upload" and x in FIELDS:
  _state(c,uid,"med:upload:"+x)
  return send(c,uid,"➕ فایل موزیک/فیلم را آپلود یا از کانال دیگری فوروارد کنید؛ یا یک لینک مستقیم HTTPS ارسال کنید.\nفایل روی دیسک سرور ذخیره نمی‌شود. پس از دریافت، اطلاعات اختیاری پرسیده می‌شود.",
    [[("↩️ بازگشت","med:"+x)]])
 if op in ("latest","mine","top") and x in FIELDS:return latest(c,uid,x,op)
 if op=="lists" and x in FIELDS:return playlist_menu(c,uid,x)
 if op in ("plfeed","plmine","pltop") and x in FIELDS:
  return playlists(c,uid,x,{"plfeed":"feed","plmine":"mine","pltop":"top"}[op])
 if op=="leaders" and x in FIELDS:return leaders(c,uid,x)
 if op=="search" and x in FIELDS:
  _state(c,uid,"med:search:"+x)
  return send(c,uid,"🔎 نام موزیک، فیلم، خواننده، پلی‌لیست یا بخشی از متن را ارسال کنید.",
          [[("↩️ بازگشت","med:"+x)]])
 if op=="play":return play(c,uid,int(x))
 if op=="like":
  liked=_like(c,uid,"media",int(x))
  send(c,uid,"❤️ لایک شد." if liked else "لایک شما برداشته شد.",[[("↩️ بازگشت به رسانه","med:play:"+x)]])
  return
 if op=="comments":return comments(c,uid,"media",int(x))
 if op=="comment":
  _media(c,int(x));_state(c,uid,"med:comment:"+x)
  return send(c,uid,"✍️ نظر خود را ارسال کنید.",[[("↩️ بازگشت","med:play:"+x)]])
 if op=="plcomments":return comments(c,uid,"playlist",int(x))
 if op=="plcomment":
  _playlist(c,uid,int(x));_state(c,uid,"med:plcomment:"+x)
  return send(c,uid,"✍️ نظر خود را درباره پلی‌لیست ارسال کنید.",[[("↩️ بازگشت","med:pl:"+x)]])
 if op=="listsel" and x in FIELDS:return playlists(c,uid,x,"mine")
 if op=="pl":return playlist_view(c,uid,int(x))
 if op=="pllike":
  liked=_like(c,uid,"playlist",int(x))
  send(c,uid,"❤️ لایک شد." if liked else "لایک برداشته شد.",[[("↩️ بازگشت به پلی‌لیست","med:pl:"+x)]])
  return
 if op=="plnew" and x in FIELDS:
  visibility=parts[3] if len(parts)>3 else ""
  if visibility not in ("0","1"):raise ValueError("نوع پلی‌لیست نامعتبر است")
  _state(c,uid,"med:plnew:"+x+":"+visibility)
  return send(c,uid,"اسم پلی‌لیست "+("خصوصی 🔒" if visibility=="1" else "عمومی 🌎")+" را ارسال کنید.",
    [[("↩️ بازگشت","med:lists:"+x)]])
 if op=="invite":
  p=_playlist(c,uid,int(x))
  if p["owner_id"]!=uid:raise ValueError("فقط سازنده می‌تواند لینک دعوت بگیرد")
  bot=_sales().settings(c).get("bot_username","")
  if not bot:raise ValueError("نام کاربری ربات ثبت نشده است")
  url="https://t.me/"+bot+"?start=mpl_"+p["invite_token"]
  return send(c,uid,("🔒 دعوت‌نامه خصوصی؛ فقط دارنده لینک می‌تواند عضویت را بپذیرد." if p["private"] else "لینک اشتراک‌گذاری پلی‌لیست عمومی")+
      "\n"+url,[[("↩️ بازگشت","med:pl:"+x)]])
 if op=="join":
  token=clean(x,32)
  p=c.execute("SELECT * FROM playlists WHERE invite_token=? AND removed=0",(token,)).fetchone()
  if not p:raise ValueError("لینک دعوت نامعتبر یا منقضی است")
  c.execute("INSERT OR IGNORE INTO playlist_members(playlist_id,user_id,created) VALUES(?,?,?)",(p["id"],uid,_sales().now()))
  return playlist_view(c,uid,p["id"])
 if op=="addmenu":
  row=_media(c,int(x))
  rows=c.execute("SELECT * FROM playlists WHERE owner_id=? AND kind=? AND removed=0 ORDER BY id DESC LIMIT 30",(uid,row["kind"])).fetchall()
  send(c,uid,"این رسانه را به کدام پلی‌لیست خودت اضافه کنیم؟",
       [[(p["name"][:40],"med:add:"+str(p["id"])+":"+x)] for p in rows]+
       [[("➕ ساخت پلی‌لیست","med:lists:"+row["kind"]),("↩️ رسانه","med:play:"+x)]])
  return
 if op=="add":
  pid=int(x);mid=int(parts[3])
  p=_playlist(c,uid,pid);row=_media(c,mid)
  if p["owner_id"]!=uid or p["kind"]!=row["kind"]:raise ValueError("فقط مالک می‌تواند رسانه هم‌نوع را اضافه کند")
  c.execute("INSERT OR IGNORE INTO playlist_entries(playlist_id,media_id,created) VALUES(?,?,?)",(pid,mid,_sales().now()))
  return playlist_view(c,uid,pid)
 if op=="remove":
  row=_owner(c,uid,int(x))
  c.execute("UPDATE media_items SET status='removed' WHERE id=?",(row["id"],))
  return send(c,uid,"فایل از فهرست عمومی حذف شد.",[[("↩️ کتابخانه","med:"+row["kind"])]])
 if op=="plremove":
  p=_playlist(c,uid,int(x))
  if p["owner_id"]!=uid:raise ValueError("فقط مالک می‌تواند پلی‌لیست را حذف کند")
  c.execute("UPDATE playlists SET removed=1 WHERE id=?",(p["id"],))
  return send(c,uid,"پلی‌لیست حذف شد.",[[("↩️ پلی‌لیست‌ها","med:lists:"+p["kind"])]])
 if op=="report":
  _media(c,int(x));_state(c,uid,"med:report:"+x)
  return send(c,uid,"🚩 دلیل گزارش این رسانه را بنویسید (حق نشر، محتوای نامناسب یا مشکل دیگر).",
    [[("↩️ رسانه","med:play:"+x)]])
 if op=="skip":
  row=_owner(c,uid,int(x),True);index=int(parts[3])
  if row["status"]!="draft":raise ValueError("فایل دیگر پیش‌نویس نیست")
  return _prompt(c,uid,row,index+1)
 if op=="finish":
  row=_owner(c,uid,int(x),True)
  return _prompt(c,uid,row,len(FIELDS[row["kind"]]))
 if op=="publish":
  return _ready(c,uid,int(x))
 if op=="confirm":
  row=_owner(c,uid,int(x),True)
  if row["status"]!="draft":raise ValueError("رسانه قبلاً منتشر شده")
  c.execute("UPDATE media_items SET status='published',rights_ok=1 WHERE id=?",(row["id"],))
  _reward_published_metadata(c,uid,row["id"])
  _state(c,uid,"")
  send(c,uid,"✅ "+GENRE_LABEL[row["kind"]]+" با موفقیت منتشر شد. برای اطلاعات اختیاری هر فیلد پاداش جداگانه دریافت می‌کنید.",
       [[("▶️ پخش رسانه","med:play:"+x),("➕ به پلی‌لیست","med:addmenu:"+x)],
        [("↩️ کتابخانه","med:"+row["kind"])]])
  return
 if op=="cancel":
  row=_owner(c,uid,int(x),True)
  if row["status"]=="draft":c.execute("UPDATE media_items SET status='removed' WHERE id=?",(row["id"],))
  _state(c,uid,"")
  return kind_menu(c,uid,row["kind"])
 raise ValueError("گزینه رسانه پیدا نشد")

def text_input(c,uid,state,message,update_id):
 parts=state.split(":")
 op=parts[1] if len(parts)>1 else ""
 if op=="upload":
  return accept(c,uid,message,parts[2])
 if op=="meta":
  if not message.get("text"):raise ValueError("متن فیلد را ارسال کنید یا رد کنید")
  return set_field(c,uid,int(parts[2]),int(parts[3]),message["text"])
 if op=="cover":
  return _cover(c,uid,int(parts[2]),message)
 if op=="rights":
  return send(c,uid,"برای انتشار، دکمه تأیید حق نشر را بزنید.",
      [[("✅ تأیید حق انتشار","med:confirm:"+parts[2]),("❌ انصراف","med:cancel:"+parts[2])]])
 if op=="plnew":
  kind=parts[2];visibility=int(parts[3])
  name=clean(message.get("text",""),95)
  if len(name)<2:raise ValueError("نام پلی‌لیست حداقل دو حرف لازم دارد")
  p=c.execute("INSERT INTO playlists(owner_id,kind,name,private,invite_token,created) VALUES(?,?,?,?,?,?)",
    (uid,kind,name,visibility,secrets.token_urlsafe(12),_sales().now())).lastrowid
  _state(c,uid,"")
  return playlist_view(c,uid,p)
 if op=="comment":
  mid=int(parts[2]);_comment(c,uid,"media",mid,message.get("text",""))
  _state(c,uid,"")
  return comments(c,uid,"media",mid)
 if op=="plcomment":
  pid=int(parts[2]);_comment(c,uid,"playlist",pid,message.get("text",""))
  _state(c,uid,"")
  return comments(c,uid,"playlist",pid)
 if op=="search":
  kind=parts[2];_state(c,uid,"")
  return search(c,uid,kind,message.get("text",""))
 if op=="report":
  mid=int(parts[2]);reason=clean(message.get("text",""),500)
  if not reason:raise ValueError("دلیل گزارش را بنویسید")
  c.execute("INSERT OR IGNORE INTO media_reports(media_id,reporter_id,reason,created) VALUES(?,?,?,?)",
            (mid,uid,reason,_sales().now()))
  _state(c,uid,"")
  return send(c,uid,"✅ گزارش شما ثبت شد و توسط مدیر بررسی می‌شود.",
       [[("↩️ رسانه","med:play:"+str(mid))]])
 raise ValueError("عملیات رسانه نامعتبر است")

def invite_start(c,uid,body):
 if not body.startswith("/start mpl_"):return False
 token=body.split("mpl_",1)[1].strip()
 if not re.fullmatch("[A-Za-z0-9_-]{12,32}",token):raise ValueError("دعوت‌نامه نامعتبر است")
 callback(c,uid,"med:join:"+token)
 return True
