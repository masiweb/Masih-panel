#!/usr/bin/env python3
"""Telegram-native music/video library; only metadata and Telegram file_ids in SQLite.

Every reader enforces ownership/playlist membership. All media is user-submitted
and requires a rights confirmation before public listing. No media bytes are
downloaded to, or persisted on, the VPN host.
"""
import hashlib
import ipaddress
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
# Telegram can deliver the same file as audio/video or as a document.
# Keep a common allowlist for both direct uploads and HTTPS media links.
AUDIO_EXTENSIONS=frozenset(("mp3","m4a","aac","flac","wav","wave","ogg","oga","opus","wma","aiff","aif","alac","amr","mp2","m4b","3ga"))
VIDEO_EXTENSIONS=frozenset(("mp4","m4v","mkv","mov","avi","webm","wmv","mpeg","mpg","3gp","3g2","ts","m2ts","mts","flv","vob"))
AUDIO_MIMES=frozenset(("application/ogg","application/x-ogg","application/vnd.apple.mpegurl"))
VIDEO_MIMES=frozenset(("application/x-matroska","application/mp4","application/x-mpegurl"))
UNSAFE_EXTENSIONS=frozenset(("exe","dll","bat","cmd","sh","msi","apk","jar","js","html","svg","php","py","ps1","scr"))
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
  ('field_points',2),('like_points',1),('max_media_mb',0),
  ('broadcast_music',0),('broadcast_movie',0),('broadcast_series',0);
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

BROADCAST_KEYS = {
 "music":"broadcast_music",
 "movie":"broadcast_movie",
 "series":"broadcast_series",
}

def broadcast_new_media(c,mid):
 """Broadcast a NEWLY PUBLISHED Telegram file, once per user via durable outbox.

 No URL-based media, no unpaid/draft items, no system or blocked users.
 This is triggered only at the first publication, not on old catalog entries
 or after changing the switch. All messages go through Telegram's retry queue.
 """
 row=c.execute("SELECT * FROM media_items WHERE id=? AND status='published'",(mid,)).fetchone()
 if not row:return 0
 media_kind="music" if row["kind"]=="music" else row["media_type"]
 setting_key=BROADCAST_KEYS.get(media_kind)
 if not setting_key or not setting(c,setting_key):return 0
 methods={"sendAudio":"audio","sendVideo":"video","sendDocument":"document",
          "sendVoice":"voice","sendVideoNote":"video_note"}
 field=methods.get(row["telegram_method"])
 if not field or not row["file_id"]:return 0
 # Never upload/send arbitrary source URLs in auto-notifications.
 caption=("🎵 موزیک جدید" if media_kind=="music" else
          "📺 قسمت جدید سریال" if media_kind=="series" else "🎬 فیلم سینمایی جدید")
 caption+="\n"+_media_summary(row)[:600]
 button={"inline_keyboard":[[{"text":"▶️ جزئیات و پخش","callback_data":"med:play:"+str(mid)}]]}
 sent=0
 recipients=c.execute("SELECT id FROM users WHERE id>0 AND blocked=0 ORDER BY id").fetchall()
 for (recipient,) in recipients:
  payload={"chat_id":recipient,field:row["file_id"],"reply_markup":button}
  if row["telegram_method"]!="sendVideoNote":payload["caption"]=caption[:950]
  # Prefixed stable idempotency key survives retries, restarts, and duplicate
  # publication callbacks. Never modifies messages already delivered.
  key="media-broadcast:"+media_kind+":"+str(mid)+":"+str(recipient)
  c.execute("INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
            (key,recipient,row["telegram_method"],json.dumps(payload,ensure_ascii=False)))
  sent+=c.execute("SELECT changes()").fetchone()[0]
 return sent

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
 payload={"chat_id":uid,field:file_id,"reply_markup":markup}
 if method!="sendVideoNote":
  # Telegram video_note does not support captions; the information card
  # has already been queued as the preceding message.
  payload["caption"]=caption[:950]
 c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
           (secrets.token_hex(12),uid,method,json.dumps(payload,ensure_ascii=False)))

def kind_menu(c,uid,kind):
 send(c,uid,GENRE_LABEL[kind]+" | فایل را بفرستید یا فوروارد کنید.",
  [[("➕ ارسال رسانه","med:upload:"+kind),("🆕 آخرین ۲۰ مورد","med:latest:"+kind)],
   [("📂 رسانه‌های من","med:mine:"+kind),("📚 پلی‌لیست‌ها","med:lists:"+kind)],
   [("🔎 جست‌وجو","med:search:"+kind),("🔥 برترین‌ها","med:top:"+kind)],
   *([[("🎙 جستجوی صوتی موزیک","med:voicefind")]] if kind=="music" else []),
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
 try:
  ip=ipaddress.ip_address(p.hostname)
 except ValueError:pass
 else:
  if not ip.is_global:raise ValueError("لینک به آدرس خصوصی قابل قبول نیست")
 return candidate

def _format_kind(filename="",mime=""):
 """Recognize common audio/video documents by extension and Telegram MIME."""
 filename=str(filename or "").strip().lower().split("?",1)[0]
 mime=str(mime or "").split(";",1)[0].strip().lower()
 ext=filename.rsplit(".",1)[-1] if "." in filename else ""
 if ext in UNSAFE_EXTENSIONS:return None
 by_extension=("music" if ext in AUDIO_EXTENSIONS else
               "film" if ext in VIDEO_EXTENSIONS else None)
 by_mime=("music" if mime.startswith("audio/") or mime in AUDIO_MIMES else
          "film" if mime.startswith("video/") or mime in VIDEO_MIMES else None)
 if by_extension and by_mime and by_extension!=by_mime:return None
 return by_extension or by_mime

def _guess(message,expected=None):
 """Detect native Telegram audio/video and common document-backed media."""
 for key,method,kind in (("audio","sendAudio","music"),("video","sendVideo","film"),
                          ("voice","sendVoice","music"),("video_note","sendVideoNote","film")):
  part=message.get(key)
  if isinstance(part,dict) and part.get("file_id") and part.get("file_unique_id"):
   if expected and expected!=kind:raise ValueError("نوع فایل با بخش انتخاب‌شده سازگار نیست")
   return kind,method,part
 part=message.get("document") or {}
 if isinstance(part,dict) and part.get("file_id") and part.get("file_unique_id"):
  kind=_format_kind(part.get("file_name"),part.get("mime_type"))
  if not kind:return None
  if expected and expected!=kind:raise ValueError("نوع فایل با بخش انتخاب‌شده سازگار نیست")
  return kind,"sendDocument",part
 return None

def _link_kind(value):
 if not isinstance(value,str) or not value.startswith("https://"):return None
 return _format_kind(urlparse(value).path)

def is_media_message(m):
 # Voice messages and circular video notes often occur in normal chat;
 # accept them only after pressing Upload, not as unsolicited library media.
 return bool(m.get("audio") or m.get("video") or (
   m.get("document") and _guess(m)) or _link_kind(m.get("text","")))

def _prompt(c,uid,row,step=0):
 if row["kind"]=="film" and row["media_type"] not in ("movie","series"):
  _state(c,uid,"med:type:"+str(row["id"]))
  send(c,uid,"🎬 سینمایی است یا سریال؟",
       [[("🎞 سینمایی","med:type:"+str(row["id"])+":movie"),
         ("📺 سریال","med:type:"+str(row["id"])+":series")]])
  return
 if row["kind"]=="film" and row["media_type"]=="series" and not row["season"]:
  _state(c,uid,"med:season:"+str(row["id"]))
  send(c,uid,"📺 شماره فصل را ارسال کنید.")
  return
 if row["kind"]=="film" and row["media_type"]=="series" and not row["episode"]:
  _state(c,uid,"med:episode:"+str(row["id"]))
  send(c,uid,"🎬 شماره قسمت را ارسال کنید.")
  return
 if row["kind"]=="film" and step in (1,2,3):step=4
 fields=FIELDS[row["kind"]]
 if step>=len(fields):
  _state(c,uid,"med:cover:"+str(row["id"]))
  send(c,uid,"🖼 کاور بفرستید یا بدون کاور ثبت کنید.",
       [[("✅ ثبت بدون کاور","med:publish:"+str(row["id"]))],
        [("❌ انصراف","med:cancel:"+str(row["id"]))]])
  return
 field,label=fields[step]
 _state(c,uid,"med:meta:"+str(row["id"])+":"+str(step))
 send(c,uid,label+" ("+str(step+1)+"/"+str(len(fields))+") — اختیاری",
      [[("⏭ رد کردن","med:skip:"+str(row["id"])+":"+str(step)),
        ("⏩ رفتن به ثبت","med:finish:"+str(row["id"]))],
       [("↩️ کتابخانه","med:"+row["kind"])]])
 
def accept(c,uid,message,expected=None):
 kind_method=_guess(message,expected)
 if not kind_method:
  # An external HTTPS URL is an index entry, not a downloaded Telegram file.
  body=message.get("text","")
  if not body.strip().startswith("https://"):
   raise ValueError("لطفاً فایل صوتی/تصویری یا لینک HTTPS مستقیم ارسال کنید")
  url=_url(body)
  detected=_link_kind(url)
  if expected and detected and expected!=detected:
   raise ValueError("نوع فایل لینک با بخش انتخاب‌شده مطابقت ندارد")
  kind=expected or detected
  if not kind:raise ValueError("نوع فایل لینک نامشخص است؛ اول دکمه آپلود موزیک یا فیلم را انتخاب کنید")
  method="link"
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
 # Drafts are private until the last optional metadata/cover step finishes.
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
 return _publish(c,uid,mid)

def _publish(c,uid,mid):
 row=_owner(c,uid,mid,True)
 if row["status"]!="draft":raise ValueError("رسانه قبلاً ثبت شده است")
 if row["kind"]=="film" and (row["media_type"] not in ("movie","series") or
      row["media_type"]=="series" and (row["season"]<1 or row["episode"]<1)):
  return _prompt(c,uid,row)
 # No separate publishing-permission prompt. rights_ok remains false:
 # publication must not be mistaken for an explicit rights certification.
 c.execute("UPDATE media_items SET status='published' WHERE id=?",(row["id"],))
 broadcast_new_media(c,row["id"])
 _reward_published_metadata(c,uid,row["id"])
 import social
 social.reward_post(c,uid,"music" if row["kind"]=="music" else row["media_type"],row["id"])
 social.notify_followers(c,uid,"music" if row["kind"]=="music" else row["media_type"],
                         row["id"],row["title"])
 _state(c,uid,"")
 send(c,uid,"✅ "+GENRE_LABEL[row["kind"]]+" ثبت شد.",
      [[("▶️ پخش","med:play:"+str(mid)),("➕ پلی‌لیست","med:addmenu:"+str(mid))],
       [("↩️ کتابخانه","med:"+row["kind"])]])


def _preview(c,uid,row):
 likes=_count(c,"media_likes","media_id",row["id"])
 comments=c.execute("SELECT COUNT(*) FROM media_comments WHERE media_id=? AND removed=0",(row["id"],)).fetchone()[0]
 dislikes=c.execute("SELECT COUNT(*) FROM social_reactions WHERE item_id=? AND kind=? AND vote=-1",
   (row["id"],"music" if row["kind"]=="music" else (row["media_type"] or "movie"))).fetchone()[0]
 caption=_media_summary(row)+"\n❤️ "+str(likes)+" | 👎 "+str(dislikes)+" | 💬 "+str(comments)
 if row["genre"]:caption+="\nسبک: "+row["genre"]
 if row["kind"]=="music" and row["lyrics"]:caption+="\nمتن: "+row["lyrics"][:100]
 if row["kind"]=="film":
  if row["actors"]:caption+="\nبازیگران: "+row["actors"][:95]
  if row["director"]:caption+="\nکارگردان: "+row["director"]
  if row["description"]:caption+="\n"+row["description"][:120]
 return caption

def play(c,uid,mid):
 row=_media(c,mid)
 buttons=[
  [("❤️ لایک","med:like:"+str(mid)),("👎 دیسلایک","med:dislike:"+str(mid))],
  [("💬 نظرات","med:comments:"+str(mid))],
  [("✍️ نظر جدید","med:comment:"+str(mid)),("➕ به پلی‌لیست من","med:addmenu:"+str(mid))],
  [("🚩 گزارش","med:report:"+str(mid))]]
 if row["kind"]=="music" and row["lyrics"]:
  buttons.append([("🎼 متن کامل آهنگ","med:lyrics:"+str(mid))])
 if row["owner_id"]>0:
  import social
  buttons.append([("👤 پروفایل منتشرکننده","soc:view:"+social.code(c,row["owner_id"]))])
 if uid==row["owner_id"] and uid>0:
  buttons.append([("🗑 حذف فایل من","med:remove:"+str(mid))])
 buttons.append([("↩️ "+GENRE_LABEL[row["kind"]],"med:"+row["kind"])])
 # Telegram renders a photo's caption BELOW the photo. Enqueue the cover
 # and full information as the first message, without action buttons.
 description=_preview(c,uid,row)
 if row["cover_id"]:
  method="sendPhoto"
  payload={"chat_id":uid,"photo":row["cover_id"],"caption":description[:1000]}
 else:
  method="sendMessage"
  payload={"chat_id":uid,"text":description[:4000]}
 c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
      (secrets.token_hex(12),uid,method,json.dumps(payload,ensure_ascii=False)))
 # Message two is the Telegram-hosted music/video itself, with ALL buttons.
 if row["telegram_method"]=="link":
  send(c,uid,"🔗 "+row["source_url"],buttons)
 else:
  _queue_file(c,uid,row["telegram_method"],row["file_id"],row["title"][:100],buttons)

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

def playlist_view(c,uid,pid,page=0):
 p=_playlist(c,uid,pid)
 if type(page)!=int or not 0<=page<=100000:raise ValueError("صفحه نامعتبر است")
 likes=_count(c,"playlist_likes","playlist_id",pid)
 dislikes=c.execute("SELECT COUNT(*) FROM social_reactions WHERE item_id=? AND kind='playlist' AND vote=-1",
                    (pid,)).fetchone()[0]
 total=c.execute("""SELECT COUNT(*) FROM playlist_entries e JOIN media_items m ON m.id=e.media_id
   WHERE e.playlist_id=? AND m.status='published'""",(pid,)).fetchone()[0]
 if page*40>=total and page>0:raise ValueError("صفحه وجود ندارد")
 entries=c.execute("""SELECT m.* FROM playlist_entries e JOIN media_items m ON m.id=e.media_id
   WHERE e.playlist_id=? AND m.status='published' ORDER BY e.created DESC,m.id DESC
   LIMIT 40 OFFSET ?""",(pid,page*40)).fetchall()
 actions=[[( _media_summary(x)[:52],"med:play:"+str(x["id"]))] for x in entries]
 navigation=[]
 if page>0:navigation.append(("⬅️ قبلی","med:plpage:"+str(pid)+":"+str(page-1)))
 if (page+1)*40<total:navigation.append(("بعدی ➡️","med:plpage:"+str(pid)+":"+str(page+1)))
 if navigation:actions.append(navigation)
 actions += [[("❤️ لایک پلی‌لیست","med:pllike:"+str(pid)),("👎 دیسلایک","med:pldislike:"+str(pid))],
            [("💬 نظرات","med:plcomments:"+str(pid))],
            [("✍️ نظر جدید","med:plcomment:"+str(pid))]]
 if p["owner_id"]>0:
  import social
  actions.append([("👤 پروفایل سازنده","soc:view:"+social.code(c,p["owner_id"]))])
 if uid==p["owner_id"]:
  actions.append([("🔗 لینک دعوت" if p["private"] else "📢 اشتراک‌گذاری","med:invite:"+str(pid)),
                  ("🗑 حذف پلی‌لیست","med:plremove:"+str(pid))])
 actions.append([("↩️ پلی‌لیست‌ها","med:lists:"+p["kind"])])
 send(c,uid,("🔒 " if p["private"] else "🌎 ")+"پلی‌لیست: "+p["name"]+
   "\n❤️ "+str(likes)+" | 👎 "+str(dislikes)+" | 🎞 "+str(total)+" رسانه | صفحه "+str(page+1)+"/"+str(max(1,(total+39)//40))+
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
 if owner["owner_id"]>0:
  row=c.execute("SELECT kind,media_type FROM media_items WHERE id=?",(id,)).fetchone() if kind=="media" else None
  label=("music" if row and row["kind"]=="music" else (row["media_type"] or "movie") if row else "playlist")
  points=setting(c,"social_like_"+label)
  if points and _daily_points(c,owner["owner_id"],"media_like")<DAILY_LIKE_LIMIT:
   award(c,owner["owner_id"],min(points,DAILY_LIKE_LIMIT-_daily_points(c,owner["owner_id"],"media_like")),
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
 rows=c.execute("SELECT user_id,body FROM "+table+" WHERE "+fk+"=? AND removed=0 ORDER BY id DESC LIMIT 5",(id,)).fetchall()
 back="med:play:"+str(id) if kind=="media" else "med:pl:"+str(id)
 import social
 send(c,uid,"💬 نظرات:\n"+("\n\n".join("👤 "+social.public_name(c,r["user_id"])+": "+r["body"][:120] for r in rows) or "نظری ثبت نشده است"),
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
           "leaders","play","comments","plcomments","pl","plpage","addmenu"):
  _state(c,uid,"")
 if op in ("music","film"):return kind_menu(c,uid,op)
 if op=="type":
  if len(parts)!=4 or parts[3] not in ("movie","series"):
   raise ValueError("نوع ویدیو نامعتبر است")
  row=_owner(c,uid,int(x),True)
  if row["kind"]!="film" or row["status"]!="draft":
   raise ValueError("این ویدیو قابل ویرایش نیست")
  c.execute("UPDATE media_items SET media_type=?,season=0,episode=0 WHERE id=?",
            (parts[3],row["id"]))
  return _prompt(c,uid,c.execute("SELECT * FROM media_items WHERE id=?",
                                 (row["id"],)).fetchone())
 if op=="voicefind":
  _state(c,uid,"med:voicefind")
  return send(c,uid,
   "🎙 ۵ تا ۴۵ ثانیه از *صدای واقعی موزیک* را ضبط یا ارسال کنید. "
   "ربات آن را با اثرانگشت آهنگ‌های موجود در کتابخانه مقایسه می‌کند. "
   "زمزمه‌کردن ملودی فعلاً پشتیبانی نمی‌شود.",
   [[("❌ انصراف","med:music")]])
 if op=="upload" and x in FIELDS:
  _state(c,uid,"med:upload:"+x)
  return send(c,uid,"📤 موزیک، فیلم یا لینک مستقیم HTTPS بفرستید.",
    [[("↩️ بازگشت","med:"+x)]])
 if op in ("latest","mine","top") and x in FIELDS:return latest(c,uid,x,op)
 if op=="lists" and x in FIELDS:return playlist_menu(c,uid,x)
 if op in ("plfeed","plmine","pltop") and x in FIELDS:
  return playlists(c,uid,x,{"plfeed":"feed","plmine":"mine","pltop":"top"}[op])
 if op=="leaders" and x in FIELDS:return leaders(c,uid,x)
 if op=="search" and x in FIELDS:
  _state(c,uid,"med:search:"+x)
  return send(c,uid,"🔎 نام، خواننده یا بخشی از متن را بنویسید.",
          [[("↩️ بازگشت","med:"+x)]])
 if op=="play":return play(c,uid,int(x))
 if op=="like":
  liked=_like(c,uid,"media",int(x))
  import social
  row=_media(c,int(x))
  genre="music" if row["kind"]=="music" else (row["media_type"] or "movie")
  c.execute("DELETE FROM social_reactions WHERE kind=? AND item_id=? AND user_id=?",(genre,int(x),uid))
  if liked:c.execute("INSERT INTO social_reactions VALUES(?,?,?,?,?)",(genre,int(x),uid,1,_sales().now()))
  send(c,uid,"❤️ لایک شد." if liked else "لایک شما برداشته شد.",[[("↩️ بازگشت به رسانه","med:play:"+x)]])
  return
 if op=="dislike":
  row=_media(c,int(x))
  import social
  genre="music" if row["kind"]=="music" else (row["media_type"] or "movie")
  return social.vote(c,uid,genre,int(x),-1,"med:play:"+x)
 if op=="lyrics":
  row=_media(c,int(x))
  if row["kind"]!="music" or not row["lyrics"]:raise ValueError("متن ترانه ثبت نشده است")
  body=row["lyrics"][:30000]
  for i in range(0,len(body),3000):
   send(c,uid,"🎼 متن آهنگ "+row["title"]+"\n\n"+body[i:i+3000],
        [[("↩️ بازگشت به موزیک","med:play:"+x)]])
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
 if op=="plpage":
  if len(parts)!=4:raise ValueError("صفحه نامعتبر است")
  return playlist_view(c,uid,int(x),int(parts[3]))
 if op=="pllike":
  liked=_like(c,uid,"playlist",int(x))
  c.execute("DELETE FROM social_reactions WHERE kind='playlist' AND item_id=? AND user_id=?",(int(x),uid))
  if liked:c.execute("INSERT INTO social_reactions VALUES(?,?,?,?,?)",("playlist",int(x),uid,1,_sales().now()))
  send(c,uid,"❤️ لایک شد." if liked else "لایک برداشته شد.",[[("↩️ بازگشت به پلی‌لیست","med:pl:"+x)]])
  return
 if op=="pldislike":
  import social
  return social.vote(c,uid,"playlist",int(x),-1,"med:pl:"+x)
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
  send(c,uid,"📚 انتخاب پلی‌لیست:",
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
  return send(c,uid,"🗑 رسانه حذف شود؟",
    [[("بله، حذف رسانه","med:removeok:"+x),("نه، بازگشت","med:play:"+x)]])
 if op=="removeok":
  row=_owner(c,uid,int(x))
  c.execute("UPDATE media_items SET status='removed' WHERE id=?",(row["id"],))
  return send(c,uid,"فایل از فهرست عمومی حذف شد.",[[("↩️ کتابخانه","med:"+row["kind"])]])
 if op=="plremove":
  p=_playlist(c,uid,int(x))
  if p["owner_id"]!=uid:raise ValueError("فقط مالک می‌تواند پلی‌لیست را حذف کند")
  return send(c,uid,"🗑 پلی‌لیست حذف شود؟",
    [[("بله، حذف پلی‌لیست","med:plremoveok:"+x),("نه، بازگشت","med:pl:"+x)]])
 if op=="plremoveok":
  p=_playlist(c,uid,int(x))
  if p["owner_id"]!=uid:raise ValueError("فقط مالک می‌تواند پلی‌لیست را حذف کند")
  c.execute("UPDATE playlists SET removed=1 WHERE id=?",(p["id"],))
  return send(c,uid,"پلی‌لیست حذف شد.",[[("↩️ پلی‌لیست‌ها","med:lists:"+p["kind"])]])
 if op=="report":
  _media(c,int(x));_state(c,uid,"med:report:"+x)
  return send(c,uid,"🚩 دلیل گزارش را بنویسید.",
    [[("↩️ رسانه","med:play:"+x)]])
 if op=="skip":
  row=_owner(c,uid,int(x),True);index=int(parts[3])
  if row["status"]!="draft":raise ValueError("فایل دیگر پیش‌نویس نیست")
  return _prompt(c,uid,row,index+1)
 if op=="finish":
  row=_owner(c,uid,int(x),True)
  return _prompt(c,uid,row,len(FIELDS[row["kind"]]))
 if op in ("publish","confirm"):
  # "confirm" is retained solely for buttons sent by older bot versions.
  return _publish(c,uid,int(x))
 if op=="cancel":
  row=_owner(c,uid,int(x),True)
  if row["status"]=="draft":c.execute("UPDATE media_items SET status='removed' WHERE id=?",(row["id"],))
  _state(c,uid,"")
  return kind_menu(c,uid,row["kind"])
 raise ValueError("گزینه رسانه پیدا نشد")

def text_input(c,uid,state,message,update_id):
 parts=state.split(":")
 op=parts[1] if len(parts)>1 else ""
 if op=="voicefind":
  audio=message.get("voice") or message.get("audio")
  if audio is None:raise ValueError("🎙 لطفاً فایل صوتی یا Voice حاوی ۵ تا ۴۵ ثانیه موزیک ارسال کنید")
  import audio_fingerprint
  audio_fingerprint.enqueue(c,uid,update_id,audio)
  _state(c,uid,"")
  return send(c,uid,"🔎 نمونه صوتی دریافت شد. موتور محلی در حال جستجو در آرشیو موزیک است. نتیجه به همین گفتگو ارسال می‌شود.",
    [[("🎙 شناسایی صوتی جدید","med:voicefind"),("🎵 بازگشت به موزیک","med:music")]])
 if op=="upload":
  return accept(c,uid,message,parts[2])
 if op in ("season","episode"):
  row=_owner(c,uid,int(parts[2]),True)
  if row["status"]!="draft" or row["kind"]!="film" or row["media_type"]!="series":
   raise ValueError("این مرحله فقط برای سریال است")
  if op=="episode" and not row["season"]:
   return _prompt(c,uid,row)
  number=_positive_int(message.get("text",""),1000)
  c.execute("UPDATE media_items SET "+op+"=? WHERE id=?",(number,row["id"]))
  return _prompt(c,uid,c.execute("SELECT * FROM media_items WHERE id=?",(row["id"],)).fetchone())
 if op=="meta":
  if not message.get("text"):raise ValueError("متن فیلد را ارسال کنید یا رد کنید")
  return set_field(c,uid,int(parts[2]),int(parts[3]),message["text"])
 if op=="cover":
  return _cover(c,uid,int(parts[2]),message)
 if op=="rights":
  # Resume unfinished submissions from older bot versions without another gate.
  return _publish(c,uid,int(parts[2]))
 if op=="plnew":
  kind=parts[2];visibility=int(parts[3])
  name=clean(message.get("text",""),95)
  if len(name)<2:raise ValueError("نام پلی‌لیست حداقل دو حرف لازم دارد")
  p=c.execute("INSERT INTO playlists(owner_id,kind,name,private,invite_token,created) VALUES(?,?,?,?,?,?)",
    (uid,kind,name,visibility,secrets.token_urlsafe(12),_sales().now())).lastrowid
  _state(c,uid,"")
  import social
  social.reward_post(c,uid,"playlist",p)
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
  return send(c,uid,"✅ گزارش ثبت شد.",
       [[("↩️ رسانه","med:play:"+str(mid))]])
 raise ValueError("عملیات رسانه نامعتبر است")

def admin_reports(c,uid):
 s=_sales().settings(c)
 if uid not in s["admins"]:raise ValueError("مدیریت رسانه فقط برای مدیر ربات است")
 rows=c.execute("""SELECT r.id,r.media_id,r.reason,m.title FROM media_reports r
   JOIN media_items m ON m.id=r.media_id WHERE r.status='pending'
   ORDER BY r.id DESC LIMIT 25""").fetchall()
 buttons=[[("🚩 "+str(r["id"])+": "+(r["title"] or str(r["media_id"]))[:35],
           "medadmin:report:"+str(r["id"]))] for r in rows]
 buttons.append([("↩️ مدیریت فروش","a:home")])
 send(c,uid,"گزارش‌های رسانه‌ای در انتظار: "+str(len(rows)),buttons)

def admin_action(c,uid,cmd):
 s=_sales().settings(c)
 if uid not in s["admins"]:raise ValueError("دسترسی مدیر رسانه لازم است")
 p=cmd.split(":")
 if cmd=="medadmin:reports":return admin_reports(c,uid)
 if len(p)!=3 or p[0]!="medadmin":raise ValueError("فرمان مدیریت رسانه نامعتبر است")
 operation,report_id=p[1],_positive_int(p[2],10**9)
 r=c.execute("SELECT * FROM media_reports WHERE id=? AND status='pending'",(report_id,)).fetchone()
 if not r:raise ValueError("گزارش یافت نشد یا قبلاً بررسی شده است")
 if operation=="report":
  row=c.execute("SELECT * FROM media_items WHERE id=?",(r["media_id"],)).fetchone()
  return send(c,uid,"گزارش شماره "+str(report_id)+"\nرسانه: "+
     (row["title"] if row else "حذف‌شده")+"\nدلیل: "+r["reason"][:400],
     [[("▶️ بررسی فایل","med:play:"+str(r["media_id"]))],
      [("🗑 حذف رسانه و بستن گزارش","medadmin:remove:"+str(report_id))],
      [("✅ رد گزارش، حفظ رسانه","medadmin:dismiss:"+str(report_id))],
      [("↩️ گزارش‌ها","medadmin:reports")]])
 if operation=="remove":
  c.execute("UPDATE media_items SET status='removed' WHERE id=?",(r["media_id"],))
  c.execute("UPDATE media_reports SET status='removed' WHERE media_id=? AND status='pending'",(r["media_id"],))
 elif operation=="dismiss":
  c.execute("UPDATE media_reports SET status='dismissed' WHERE id=?",(report_id,))
 else:raise ValueError("عملیات نامعتبر است")
 return admin_reports(c,uid)

def invite_start(c,uid,body):
 if not body.startswith("/start mpl_"):return False
 token=body.split("mpl_",1)[1].strip()
 if not re.fullmatch("[A-Za-z0-9_-]{12,32}",token):raise ValueError("دعوت‌نامه نامعتبر است")
 callback(c,uid,"med:join:"+token)
 return True
