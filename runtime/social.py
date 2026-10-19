#!/usr/bin/env python3
"""Masiha Social: opt-in, Telegram-user-ID-private community profiles.

All public references use immutable generated codes, NEVER users.id or
Telegram usernames. Contact/phone, exact coordinates and private messages
are never part of public profile serialization.
"""
import json, math, re, secrets, time
from collections import Counter

INTERESTS=("🎵 موسیقی","🎬 فیلم و سریال","🎮 بازی","⚽ ورزش","📚 کتاب",
           "✈️ سفر","💻 فناوری","🎨 هنر","🍳 آشپزی","🌿 طبیعت")
PROVINCES=("آذربایجان شرقی","آذربایجان غربی","اردبیل","اصفهان","البرز","ایلام",
"بوشهر","تهران","چهارمحال و بختیاری","خراسان جنوبی","خراسان رضوی","خراسان شمالی",
"خوزستان","زنجان","سمنان","سیستان و بلوچستان","فارس","قزوین","قم","کردستان",
"کرمان","کرمانشاه","کهگیلویه و بویراحمد","گلستان","گیلان","لرستان",
"مازندران","مرکزی","هرمزگان","همدان","یزد")
MEDALS=(("💎 الماس",1,1),("🥇 طلا",2,3),("🥈 نقره",4,10),("🥉 برنز",11,30))
DEFAULT_POINTS={
 "social_post_music":10,"social_post_movie":20,"social_post_series":15,
 "social_post_playlist":5,"social_like_music":1,"social_like_movie":2,
 "social_like_series":2,"social_like_playlist":2,"social_like_profile":1
}

def setup(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS social_profiles(
  user_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  gender TEXT NOT NULL DEFAULT '',
  age INTEGER NOT NULL DEFAULT 0,
  province TEXT NOT NULL DEFAULT '',
  interests TEXT NOT NULL DEFAULT '[]',
  photos TEXT NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','friends','hidden')),
  show_visitors INTEGER NOT NULL DEFAULT 1,
  discoverable INTEGER NOT NULL DEFAULT 1,
  created INTEGER NOT NULL, updated INTEGER NOT NULL
 );
 CREATE INDEX IF NOT EXISTS social_profiles_visible ON social_profiles(visibility,discoverable);
 CREATE TABLE IF NOT EXISTS social_follows(
  follower INTEGER NOT NULL, followed INTEGER NOT NULL, created INTEGER NOT NULL,
  PRIMARY KEY(follower,followed),CHECK(follower!=followed)
 );
 CREATE INDEX IF NOT EXISTS social_follows_reverse ON social_follows(followed);
 CREATE TABLE IF NOT EXISTS social_profile_views(
  owner INTEGER NOT NULL, viewer INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  views INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(owner,viewer),CHECK(owner!=viewer)
 );
 CREATE TABLE IF NOT EXISTS social_reactions(
  kind TEXT NOT NULL CHECK(kind IN ('profile','music','movie','series','playlist')),
  item_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  vote INTEGER NOT NULL CHECK(vote IN (-1,1)),updated INTEGER NOT NULL,
  PRIMARY KEY(kind,item_id,user_id)
 );
 CREATE INDEX IF NOT EXISTS social_reactions_scores ON social_reactions(kind,item_id,vote);
 CREATE TABLE IF NOT EXISTS social_profile_comments(
  id INTEGER PRIMARY KEY,owner INTEGER NOT NULL,author INTEGER NOT NULL,
  body TEXT NOT NULL,created INTEGER NOT NULL,removed INTEGER NOT NULL DEFAULT 0
 );
 CREATE INDEX IF NOT EXISTS social_comment_by_profile ON social_profile_comments(owner,created DESC);
 CREATE TABLE IF NOT EXISTS social_chat_pairs(
  id INTEGER PRIMARY KEY,first_user INTEGER NOT NULL,second_user INTEGER NOT NULL,
  created INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0,
  type TEXT NOT NULL DEFAULT 'random'
 );
 CREATE TABLE IF NOT EXISTS social_chat_messages(
  id INTEGER PRIMARY KEY,conversation INTEGER NOT NULL,sender INTEGER NOT NULL,
  body TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL
 );
 CREATE INDEX IF NOT EXISTS social_messages_by_chat ON social_chat_messages(conversation,id);
 CREATE TABLE IF NOT EXISTS social_chat_queue(user_id INTEGER PRIMARY KEY,
   mode TEXT NOT NULL,joined INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS social_chat_invites(
 requester INTEGER NOT NULL,recipient INTEGER NOT NULL,created INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 PRIMARY KEY(requester,recipient),CHECK(requester!=recipient));

 CREATE TABLE IF NOT EXISTS social_blocks(blocker INTEGER NOT NULL,blocked INTEGER NOT NULL,
   created INTEGER NOT NULL,PRIMARY KEY(blocker,blocked),CHECK(blocker!=blocked));
 CREATE TABLE IF NOT EXISTS social_geo(user_id INTEGER PRIMARY KEY,lat REAL NOT NULL,
   lon REAL NOT NULL,updated INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE IF NOT EXISTS social_reports(
   id INTEGER PRIMARY KEY,reporter INTEGER NOT NULL,target INTEGER NOT NULL,
   reason TEXT NOT NULL,created INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
 """)
 c.executemany("INSERT OR IGNORE INTO media_settings(k,v) VALUES(?,?)",DEFAULT_POINTS.items())
 c.execute("INSERT OR IGNORE INTO media_settings(k,v) VALUES('social_chat_retention_days',90)")
 c.commit()

def _sales():
 import sales
 return sales

def _now():return int(time.time())

def _send(c,uid,text,buttons=None):
 _sales().enqueue(c,uid,text,buttons or [[("🏠 منوی اصلی","home")]])

def _state(c,uid,s):
 c.execute("UPDATE users SET state=? WHERE id=?",(s,uid))

def profile(c,uid):
 row=c.execute("SELECT * FROM social_profiles WHERE user_id=?",(uid,)).fetchone()
 if row:return row
 for _ in range(8):
  code="MS-"+secrets.token_hex(4).upper()
  if not c.execute("SELECT 1 FROM social_profiles WHERE code=?",(code,)).fetchone():break
 else:raise RuntimeError("Could not allocate opaque public profile handle")
 c.execute("INSERT INTO social_profiles(user_id,code,created,updated) VALUES(?,?,?,?)",
           (uid,code,_now(),_now()))
 return c.execute("SELECT * FROM social_profiles WHERE user_id=?",(uid,)).fetchone()

def _public_name(c,uid):
 if uid==0:return "🎼 آرشیو رسمی مسیحا"
 r=c.execute("SELECT display_name FROM social_profiles WHERE user_id=?",(uid,)).fetchone()
 return r["display_name"][:48] if r and r["display_name"] else "کاربر مسیحا"

def public_name(c,uid):
 return _public_name(c,uid)

def code(c,uid):
 return profile(c,uid)["code"]

def complete(p):
 return bool(p["display_name"] and p["gender"] and p["age"]>=18 and
             p["province"] in PROVINCES and json.loads(p["interests"]))

def _is_friend(c,a,b):
 return bool(c.execute("SELECT 1 FROM social_follows WHERE follower=? AND followed=?",
                       (a,b)).fetchone() and
             c.execute("SELECT 1 FROM social_follows WHERE follower=? AND followed=?",
                       (b,a)).fetchone())

def _can_view(c,viewer,p):
 if viewer==p["user_id"] or viewer in _sales().settings(c)["admins"]:return True
 if p["visibility"]=="hidden":return False
 return p["visibility"]=="public" or _is_friend(c,viewer,p["user_id"])

def _blocked(c,a,b):
 return bool(c.execute("SELECT 1 FROM social_blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",
                       (a,b,b,a)).fetchone())

def _score(c,kind,item):
 row=c.execute("SELECT COALESCE(SUM(vote),0),SUM(vote=1),SUM(vote=-1) FROM social_reactions WHERE kind=? AND item_id=?",
               (kind,item)).fetchone()
 return (row[0] or 0,row[1] or 0,row[2] or 0)

def notify_followers(c,uid,kind,item,title):
 """One opt-in follow update per published user media, never system imports."""
 if uid<=0:return 0
 key="broadcast_"+kind
 global_on=c.execute("SELECT v FROM media_settings WHERE k=?",(key,)).fetchone()
 if global_on and global_on[0]:return 0  # Avoid duplicate all-user broadcast.
 p=profile(c,uid)
 if p["visibility"]!="public":return 0
 followers=c.execute("""SELECT f.follower FROM social_follows f JOIN users u
   ON u.id=f.follower WHERE f.followed=? AND u.blocked=0""",(uid,)).fetchall()
 count=0
 for row in followers:
  follower=row["follower"]
  if _blocked(c,uid,follower):continue
  payload={"chat_id":follower,
    "text":"🔔 "+_public_name(c,uid)+" محتوای جدید منتشر کرد: "+str(title)[:100],
    "reply_markup":{"inline_keyboard":[[{"text":"▶️ مشاهده اثر",
      "callback_data":"med:play:"+str(item)}]]}}
  result=c.execute("INSERT OR IGNORE INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
    ("social:follow:post:"+str(item)+":"+str(follower),follower,"sendMessage",
      json.dumps(payload,ensure_ascii=False)))
  count+=result.rowcount
 return count

def reward_post(c,uid,kind,item):
 if uid<=0 or kind not in ("music","movie","series","playlist"):return False
 from gamification import award
 points=c.execute("SELECT v FROM media_settings WHERE k=?",("social_post_"+kind,)).fetchone()[0]
 day=(_now()//86400)*86400
 spent=c.execute("""SELECT COALESCE(SUM(points),0) FROM loyalty_points
    WHERE user_id=? AND kind='social_post' AND created>=?""",(uid,day)).fetchone()[0]
 points=min(points,max(0,50-spent))
 return award(c,uid,points,"social_post","social:post:"+kind+":"+str(item),_now())

def reward_like(c,uid,owner,kind,item):
 if owner<=0 or owner==uid:return False
 from gamification import award
 points=c.execute("SELECT v FROM media_settings WHERE k=?",("social_like_"+kind,)).fetchone()[0]
 # Account/ownership verified before this call. Capped against spam.
 day_start=(_now()//86400)*86400
 daily=c.execute("""SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=?
    AND kind IN ('social_like','media_like') AND created>=?""",(owner,day_start)).fetchone()[0]
 remaining=max(0,20-daily)
 if not remaining:return False
 return award(c,owner,min(points,remaining),"social_like",
  "liked:"+("media" if kind in ("music","movie","series") else kind)+":"+str(item)+":"+str(uid),_now())

def reaction(c,uid,kind,item,vote):
 if kind not in ("profile","music","movie","series","playlist") or vote not in (-1,1):
  raise ValueError("واکنش نامعتبر است")
 if kind=="profile":
  target=c.execute("SELECT user_id FROM social_profiles WHERE user_id=?",(item,)).fetchone()
  if not target:raise ValueError("پروفایل یافت نشد")
  owner=item
 elif kind=="playlist":
  target=c.execute("SELECT owner_id,private,removed FROM playlists WHERE id=?",(item,)).fetchone()
  if not target or target["removed"] or target["private"]:raise ValueError("پلی‌لیست عمومی یافت نشد")
  owner=target["owner_id"]
 else:
  target=c.execute("SELECT owner_id,kind,media_type,status FROM media_items WHERE id=?",(item,)).fetchone()
  if not target or target["status"]!="published" or (
   kind=="music" and target["kind"]!="music") or (
   kind!="music" and (target["kind"]!="film" or target["media_type"]!=kind)):
   raise ValueError("رسانه یافت نشد")
  owner=target["owner_id"]
 if owner==uid:raise ValueError("نمی‌توانید به پروفایل یا اثر خودتان رأی بدهید")
 if owner>0 and _blocked(c,uid,owner):raise ValueError("این کاربر در فهرست مسدودشده‌هاست")
 old=c.execute("SELECT vote FROM social_reactions WHERE kind=? AND item_id=? AND user_id=?",
               (kind,item,uid)).fetchone()
 actual=0 if old and old["vote"]==vote else vote
 if actual:
  c.execute("""INSERT INTO social_reactions(kind,item_id,user_id,vote,updated)
               VALUES(?,?,?,?,?) ON CONFLICT(kind,item_id,user_id)
               DO UPDATE SET vote=excluded.vote,updated=excluded.updated""",
            (kind,item,uid,actual,_now()))
 else:
  c.execute("DELETE FROM social_reactions WHERE kind=? AND item_id=? AND user_id=?",
            (kind,item,uid))
 # Keep legacy positive reactions in sync (and backwards compatible).
 table=("media_likes" if kind in ("music","movie","series") else
        "playlist_likes" if kind=="playlist" else None)
 if table:
  col="media_id" if table=="media_likes" else "playlist_id"
  c.execute("DELETE FROM "+table+" WHERE "+col+"=? AND user_id=?",(item,uid))
  if actual==1:
   c.execute("INSERT OR IGNORE INTO "+table+"("+col+",user_id,created) VALUES(?,?,?)",
             (item,uid,_now()))
 if actual==1:reward_like(c,uid,owner,kind,item)
 return actual,_score(c,kind,item)

def vote(c,uid,kind,item,val,back):
 actual,score=reaction(c,uid,kind,item,val)
 _send(c,uid,("❤️ پسندیدید" if actual==1 else
               "👎 نپسندیدید" if actual==-1 else "واکنش حذف شد")+
       "\n❤️ "+str(score[1])+" | 👎 "+str(score[2]),
       [[("↩️ بازگشت",back)]])

def medals(c,uid):
 # Counts existing positive likes, with negative votes subtracted from the
 # corresponding public ranking (no duplicate credit for legacy likes).
 vals={}
 vals["playlist"]=c.execute("""SELECT COUNT(*) FROM playlist_likes l JOIN playlists p
 ON p.id=l.playlist_id WHERE p.owner_id=? AND p.private=0 AND p.removed=0""",(uid,)).fetchone()[0]
 vals["playlist"]=max(0,vals["playlist"]-c.execute("""SELECT COUNT(*) FROM social_reactions r
  JOIN playlists p ON p.id=r.item_id WHERE r.kind='playlist' AND r.vote=-1
  AND p.owner_id=? AND p.private=0 AND p.removed=0""",(uid,)).fetchone()[0])
 for kind in ("music","movie","series"):
  vals[kind]=c.execute("""SELECT COUNT(*) FROM media_likes l JOIN media_items m ON m.id=l.media_id
   WHERE m.owner_id=? AND m.status='published' AND
   ((?='music' AND m.kind='music') OR (?!='music' AND m.kind='film' AND m.media_type=?))""",
   (uid,kind,kind,kind)).fetchone()[0]
  dislikes=c.execute("""SELECT COUNT(*) FROM social_reactions r JOIN media_items m ON m.id=r.item_id
   WHERE r.kind=? AND r.vote=-1 AND m.owner_id=? AND m.status='published'""",
   (kind,uid)).fetchone()[0]
  vals[kind]=max(0,vals[kind]-dislikes)
 vals["points"]=c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=?",(uid,)).fetchone()[0]
 return vals

def medal_badges(c,uid):
 """Award position medals per category, independent of social user numeric ID."""
 users=c.execute("""SELECT user_id FROM social_profiles WHERE
   display_name<>'' AND visibility='public' AND discoverable=1
   ORDER BY user_id LIMIT 5000""").fetchall()
 ranks={"points":[],"playlist":[],"music":[],"movie":[],"series":[]}
 for row in users:
  score=medals(c,row["user_id"])
  for k in ranks:
   if score[k]>0:ranks[k].append((score[k],row["user_id"]))
 labels={"points":"امتیاز","playlist":"پلی‌لیست","music":"موزیک","movie":"فیلم","series":"سریال"}
 badges=[]
 for key,items in ranks.items():
  items.sort(key=lambda x:(-x[0],x[1]))
  for rank,(_,other) in enumerate(items,1):
   if rank>30:break
   if other==uid:
    medal=next((med for med,lo,hi in MEDALS if lo<=rank<=hi),"")
    if medal:badges.append(medal+" "+labels[key]+" (رتبه "+str(rank)+")")
    break
 return badges

def my_profile(c,uid):
 p=profile(c,uid); tags=json.loads(p["interests"]); photos=json.loads(p["photos"])
 followers=c.execute("SELECT COUNT(*) FROM social_follows WHERE followed=?",(uid,)).fetchone()[0]
 score=medals(c,uid)
 badges=medal_badges(c,uid)
 label="\n".join("• "+name+": "+str(score[k]) for k,name in
  (("points","⭐ امتیاز"),("playlist","📚 پلی‌لیست"),("music","🎵 موزیک"),
   ("movie","🎬 فیلم"),("series","📺 سریال")))
 text=("👤 پروفایل مسیحا\nنام: "+(p["display_name"] or "تکمیل نشده")+
      "\nشناسه عمومی ثابت: "+p["code"]+("\nجنسیت: "+p["gender"] if p["gender"] else "")+
      ("\nسن: "+str(p["age"]) if p["age"] else "")+
      ("\nاستان: "+p["province"] if p["province"] else "")+
      "\nعلاقه‌مندی‌ها: "+("، ".join(tags) if tags else "ثبت نشده")+
      "\n🖼 تصاویر: "+str(len(photos))+"/۵"+
      "\n👥 دنبال‌کنندگان: "+str(followers)+
      "\n\n🏆 امتیازات افتخارات:\n"+label+
      ("\n\n🎖 مدال‌های فعلی:\n"+"\n".join(badges) if badges else ""))
 if not complete(p):text+="\n\n⚠️ برای استفاده از چت و شبکه اجتماعی، پروفایل را کامل کنید."
 _send(c,uid,text,[
  [("✏️ تغییر نام","soc:edit:name"),("🎂 سن","soc:edit:age")],
  [("👤 جنسیت","soc:gender"),("📍 استان","soc:provinces:0")],
  [("❤️ علاقه‌مندی‌ها","soc:interests"),("🖼 مدیریت عکس‌ها","soc:photos")],
  [("🖼 مشاهده گالری خودم","soc:mygallery")],
  [("🌐 تنظیمات حریم خصوصی","soc:privacy"),("🔗 لینک دعوت","soc:invite")],
  [("👁 بازدیدکنندگان","soc:visitors"),("👥 دنبال‌کنندگان","soc:followers")],
  [("🏠 بازگشت به مسیحا سوشال","soc:home")]])

def _public_card(c,viewer,p):
 if not _can_view(c,viewer,p) or _blocked(c,viewer,p["user_id"]):
  raise ValueError("این پروفایل قابل نمایش نیست")
 if viewer!=p["user_id"]:
  # Viewer identity is visible only if they consent to appearing.
  c.execute("""INSERT INTO social_profile_views(owner,viewer,last_seen,views)
    VALUES(?,?,?,1) ON CONFLICT(owner,viewer) DO UPDATE SET
    last_seen=excluded.last_seen,views=social_profile_views.views+1""",
    (p["user_id"],viewer,_now()))
 score=_score(c,"profile",p["user_id"])
 followers=c.execute("SELECT COUNT(*) FROM social_follows WHERE followed=?",(p["user_id"],)).fetchone()[0]
 tags=json.loads(p["interests"])
 msg=("👤 "+(p["display_name"] or "کاربر مسیحا")+"\n🆔 "+p["code"]+
     "\n❤️ علاقه‌مندی‌ها: "+("، ".join(tags) if tags else "نامشخص")+
     "\n👥 دنبال‌کننده: "+str(followers)+"\n❤️ "+str(score[1])+" | 👎 "+str(score[2]))
 if p["province"]:msg+="\n📍 "+p["province"]
 if p["age"]:msg+="\n🎂 "+str(p["age"])+" سال"
 return msg

def public_card(c,uid,target):
 p=profile(c,target)
 if target==uid:return my_profile(c,uid)
 msg=_public_card(c,uid,p)
 badges=medal_badges(c,target)
 if badges:msg+="\n\n🏅 افتخارات:\n"+"\n".join(badges)
 following=bool(c.execute("SELECT 1 FROM social_follows WHERE follower=? AND followed=?",
                          (uid,target)).fetchone())
 buttons=[[("❤️ لایک","soc:vote:like:"+p["code"]),("👎 دیسلایک","soc:vote:dislike:"+p["code"])],
  [("💬 نظرات","soc:comments:"+p["code"]),("➕ نظر","soc:comment:"+p["code"])],
  [("🖼 تصاویر پروفایل","soc:gallery:"+p["code"])],
  [(("➖ لغو دنبال‌کردن" if following else "➕ دنبال‌کردن"),"soc:follow:"+p["code"])],
  [("💬 درخواست چت دوستانه","soc:friendchat:"+p["code"])],
  [("🚩 گزارش","soc:report:"+p["code"]),("⛔ مسدودکردن","soc:block:"+p["code"])],
  [("↩️ مسیحا سوشال","soc:home")]]
 _send(c,uid,msg,buttons)

def by_code(c,code):
 if not re.fullmatch(r"MS-[0-9A-F]{8}",code):raise ValueError("شناسه عمومی نامعتبر است")
 row=c.execute("SELECT user_id FROM social_profiles WHERE code=?",(code,)).fetchone()
 if not row:raise ValueError("کاربر پیدا نشد")
 return row["user_id"]

def menu(c,uid):
 _leave(c,uid)
 p=profile(c,uid)
 ready=complete(p)
 buttons=[[("👤 پروفایل من","soc:profile"),("🏆 برترین‌ها","soc:leaders")],
  [("🎲 چت ناشناس تصادفی","soc:random"),("🎯 چت با فیلتر","soc:filtered")],
  [("📍 کاربران نزدیک من","soc:near"),("🔎 جستجوی کاربر","soc:search")],
  [("👥 دنبال‌کنندگان","soc:followers"),("👀 دنبال می‌کنم","soc:following")],
  [("👁 بازدید پروفایل","soc:visitors")],
  [("🔗 لینک دعوت","soc:invite"),("⚙️ حریم خصوصی","soc:privacy")],
  [("🏠 منوی اصلی","home")]]
 if not ready:
  buttons.insert(0,[("✅ تکمیل مرحله‌ای پروفایل اجتماعی","soc:setup")])
 _send(c,uid,"🌐 مسیحا سوشال\nشناسه عمومی شما: "+p["code"]+
  ("\n✅ پروفایل آماده است." if ready else "\n⚠️ ابتدا پروفایل خود را تکمیل کنید."),
  buttons)

def interests(c,uid):
 p=profile(c,uid); chosen=set(json.loads(p["interests"]))
 buttons=[]
 for i in range(0,len(INTERESTS),2):
  buttons.append([(("✅ " if INTERESTS[j] in chosen else "▫️ ")+INTERESTS[j],
                   "soc:interest:"+str(j)) for j in range(i,min(i+2,len(INTERESTS)))])
 buttons.extend([[("✅ پایان انتخاب (حداکثر ۵)","soc:profile")]])
 _send(c,uid,"❤️ علاقه‌مندی‌ها: "+str(len(chosen))+"/۵\nحداکثر پنج موضوع انتخاب کنید.",buttons)

def provinces(c,uid,page):
 start=page*10
 if page<0 or start>=len(PROVINCES):raise ValueError("صفحه نامعتبر است")
 buttons=[[(p,"soc:province:"+str(start+i))] for i,p in enumerate(PROVINCES[start:start+10])]
 nav=[]
 if page>0:nav.append(("⬅️ قبلی","soc:provinces:"+str(page-1)))
 if start+10<len(PROVINCES):nav.append(("بعدی ➡️","soc:provinces:"+str(page+1)))
 if nav:buttons.append(nav)
 buttons.append([("↩️ پروفایل","soc:profile")])
 _send(c,uid,"📍 استان محل زندگی خود را انتخاب کنید.",buttons)

def _require_complete(c,uid):
 if not complete(profile(c,uid)):
  my_profile(c,uid)
  return False
 return True

def _leaders(c,uid):
 labels=(("points","⭐ امتیاز کل"),("playlist","📚 پلی‌لیست"),("music","🎵 موزیک"),
         ("movie","🎬 فیلم"),("series","📺 سریال"))
 rows=c.execute("SELECT user_id,code,display_name FROM social_profiles WHERE display_name<>'' AND visibility='public' AND discoverable=1 LIMIT 1000").fetchall()
 buttons=[]
 for key,label in labels:
  top=sorted(((medals(c,r["user_id"])[key],r) for r in rows),key=lambda v:-v[0])[:3]
  if top and top[0][0]>0:
   buttons.append([(label+" | "+top[0][1]["display_name"][:19],
                    "soc:view:"+top[0][1]["code"])])
 _send(c,uid,"🏆 برترین‌ها و مدال‌ها\n💎 نفر اول، 🥇 دوم و سوم، 🥈 چهارم تا دهم، 🥉 یازدهم تا سی‌ام\n"+
  "رتبه‌بندی بر پایه امتیاز و محبوبیت آثار عمومی کاربران است.",buttons+
  [[("↩️ مسیحا سوشال","soc:home")]])

def _visitors(c,uid):
 p=profile(c,uid)
 rows=c.execute("""SELECT v.viewer,v.last_seen,p.show_visitors,p.display_name,p.code
  FROM social_profile_views v JOIN social_profiles p ON p.user_id=v.viewer
  WHERE v.owner=? ORDER BY v.last_seen DESC LIMIT 20""",(uid,)).fetchall()
 names=[("👤 "+r["display_name"] if r["show_visitors"] and r["display_name"] else
         "🔒 بازدیدکننده ناشناس") for r in rows]
 _send(c,uid,"👁 آخرین بازدیدکنندگان پروفایل\n"+
       ("\n".join(names) if names else "هنوز بازدیدی ثبت نشده است."),
       [[("↩️ پروفایل","soc:profile")]])

def _comments(c,uid,owner):
 p=profile(c,owner)
 if not _can_view(c,uid,p):raise ValueError("پروفایل خصوصی است")
 rows=c.execute("""SELECT author,body FROM social_profile_comments WHERE owner=?
                    AND removed=0 ORDER BY id DESC LIMIT 10""",(owner,)).fetchall()
 _send(c,uid,"💬 نظرات پروفایل "+(p["display_name"] or p["code"])+
  "\n"+("\n".join("• "+_public_name(c,r["author"])+": "+r["body"] for r in rows)
        if rows else "هنوز نظری ثبت نشده است."),
  [[("✍️ نظر جدید","soc:comment:"+p["code"])],[("↩️ پروفایل","soc:view:"+p["code"])]])

def _active_chat(c,uid):
 return c.execute("""SELECT * FROM social_chat_pairs
 WHERE closed=0 AND (first_user=? OR second_user=?)
 ORDER BY id DESC LIMIT 1""",(uid,uid)).fetchone()

def _leave(c,uid,announce=True):
 c.execute("DELETE FROM social_chat_queue WHERE user_id=?",(uid,))
 chat=_active_chat(c,uid)
 if chat:
  c.execute("UPDATE social_chat_pairs SET closed=? WHERE id=? AND closed=0",(_now(),chat["id"]))
  peer=chat["second_user"] if chat["first_user"]==uid else chat["first_user"]
  if announce:_send(c,peer,"گفتگو توسط طرف مقابل پایان یافت.",
                    [[("🌐 بازگشت به مسیحا سوشال","soc:home")]])
 _state(c,uid,"")
 return chat

def start_matching(c,uid,mode):
 if mode not in ("random","filtered"):raise ValueError("نوع اتصال ناشناس نامعتبر است")
 if not _require_complete(c,uid):return
 _leave(c,uid)
 profile(c,uid)
 mine=profile(c,uid)
 candidates=c.execute("""SELECT q.user_id,p.interests,p.province FROM social_chat_queue q
 JOIN social_profiles p ON p.user_id=q.user_id
 WHERE q.mode=? AND q.user_id!=? AND p.discoverable=1
 ORDER BY q.joined LIMIT 100""",(mode,uid)).fetchall()
 interests=set(json.loads(mine["interests"]))
 for row in candidates:
  peer=row["user_id"]
  if _blocked(c,uid,peer) or not complete(profile(c,peer)):continue
  if _active_chat(c,peer):continue
  if mode=="filtered" and not (
    interests.intersection(json.loads(row["interests"]))):continue
  cursor=c.execute("DELETE FROM social_chat_queue WHERE user_id=? AND mode=?",(peer,mode))
  if cursor.rowcount!=1:continue
  new=c.execute("""INSERT INTO social_chat_pairs(first_user,second_user,created,type)
      VALUES(?,?,?,?)""",(uid,peer,_now(),mode)).lastrowid
  _state(c,uid,"soc:chat:"+str(new));_state(c,peer,"soc:chat:"+str(new))
  safety=("🔒 چت ناشناس برقرار شد. هویت تلگرام و شماره تلفن شما به طرف مقابل نمایش "
   "داده نمی‌شود. گفتگو از طریق ربات منتقل و برای امنیت و رسیدگی به گزارش‌ها "
   "در سامانه شرکت ثبت می‌شود و مدیران مجاز می‌توانند آن را بررسی کنند. "
   "برای پایان، «پایان چت» را بزنید. شماره تماس و اطلاعات حساس نفرستید.")
  for person in (uid,peer):
   _send(c,person,safety,[[("⛔ پایان چت","soc:leave"),("🚩 گزارش گفتگو","soc:chatreport")]])
  return new
 c.execute("""INSERT INTO social_chat_queue(user_id,mode,joined) VALUES(?,?,?)
      ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,joined=excluded.joined""",
      (uid,mode,_now()))
 _state(c,uid,"soc:waiting")
 _send(c,uid,("🎯 در انتظار فرد هم‌علاقه…" if mode=="filtered" else
               "🎲 در انتظار اتصال به کاربر دیگری…")+
       "\nچت تنها زمانی شروع می‌شود که هر دو نفر برای گفتگو درخواست داده باشند. "
       "گفتگوها برای رسیدگی به گزارش‌ها نزد شرکت ثبت می‌شوند.",
       [[("❌ خروج از صف","soc:leave")]])
 return None

def end_chat(c,uid):
 had=_leave(c,uid)
 _send(c,uid,"گفتگو پایان یافت." if had else "از صف گفتگو خارج شدید.",
       [[("🌐 مسیحا سوشال","soc:home"),("🎲 گفتگوی جدید","soc:random")]])

def _chat_message(c,uid,m,chat_id):
 chat=_active_chat(c,uid)
 if not chat or chat["id"]!=chat_id:raise ValueError("گفتگو پایان یافته است")
 peer=chat["second_user"] if chat["first_user"]==uid else chat["first_user"]
 if _blocked(c,uid,peer):return end_chat(c,uid)
 body=(m.get("text") or "").strip()
 if body in ("/stop","/end","پایان چت"):return end_chat(c,uid)
 if not 1<=len(body)<=1200:raise ValueError("فقط پیام متنی حداکثر ۱۲۰۰ حرف ارسال کنید.")
 last=c.execute("SELECT created FROM social_chat_messages WHERE sender=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
 if last and _now()-last["created"]<1:raise ValueError("ارسال پیام خیلی سریع است")
 c.execute("INSERT INTO social_chat_messages(conversation,sender,body,created) VALUES(?,?,?,?)",
           (chat_id,uid,body,_now()))
 visible_name=_public_name(c,uid) if chat["type"]=="friend" else "طرف مقابل"
 _send(c,peer,"💬 "+visible_name+"\n"+body,
       [[("⛔ پایان چت","soc:leave"),("🚩 گزارش گفتگو","soc:chatreport")]])
 return True

def near(c,uid):
 if not _require_complete(c,uid):return
 me=c.execute("SELECT lat,lon,enabled,updated FROM social_geo WHERE user_id=?",(uid,)).fetchone()
 if not me or not me["enabled"] or _now()-me["updated"]>30*86400:
  return _send(c,uid,"📍 برای نمایش نزدیک‌ترین کاربران، یک‌بار موقعیت خودتان را "
    "به‌صورت اختیاری ارسال کنید. مختصات دقیق شما به دیگر کاربران نمایش داده نمی‌شود. "
    "پس از ۳۰ روز، موقعیت نیاز به به‌روزرسانی دارد.",
    [[("📍 ارسال موقعیت","soc:geoshare"),("↩️ بازگشت","soc:home")]])
 others=c.execute("""SELECT g.user_id,g.lat,g.lon FROM social_geo g
 JOIN social_profiles p ON p.user_id=g.user_id
 JOIN users u ON u.id=g.user_id
 WHERE g.user_id!=? AND g.enabled=1 AND g.updated>=? AND p.visibility='public'
 AND p.discoverable=1 AND p.display_name!='' AND u.blocked=0""",
 (uid,_now()-30*86400)).fetchall()
 found=[]
 for row in others:
  target=row["user_id"]
  if _blocked(c,uid,target):continue
  a1,a2=math.radians(me["lat"]),math.radians(row["lat"])
  da=a2-a1;db=math.radians(row["lon"]-me["lon"])
  h=math.sin(da/2)**2+math.cos(a1)*math.cos(a2)*math.sin(db/2)**2
  distance=6371*2*math.asin(min(1,math.sqrt(h)))
  found.append((distance,target))
 found.sort(key=lambda r:r[0])
 buttons=[]
 for distance,target in found[:15]:
  # Coarsen to protect precise residential locations.
  approx=int(math.ceil(distance/5)*5)
  buttons.append([(_public_name(c,target)+" • تا حدود "+str(max(5,approx))+" کیلومتر",
                  "soc:view:"+code(c,target))])
 _send(c,uid,"📍 کاربران نزدیک من\nفاصله‌ها تقریبی و بدون نمایش مختصات دقیق هستند. "+
    ("کاربر نزدیک دارای موقعیت فعال یافت نشد." if not buttons else ""),
    buttons+[[("🔄 به‌روزرسانی موقعیت","soc:geoshare"),
              ("🗑 حذف موقعیت من","soc:geodelete")],
             [("↩️ مسیحا سوشال","soc:home")]])

def geo_input(c,uid,m):
 point=m.get("location")
 if not isinstance(point,dict):raise ValueError("موقعیت را با گزینه Location تلگرام ارسال کنید.")
 lat=point.get("latitude");lon=point.get("longitude")
 if (type(lat) not in (int,float) or type(lon) not in (int,float)
    or not math.isfinite(lat) or not math.isfinite(lon)
    or not -90<=lat<=90 or not -180<=lon<=180):
  raise ValueError("مختصات جغرافیایی نامعتبر است")
 c.execute("""INSERT INTO social_geo(user_id,lat,lon,updated,enabled) VALUES(?,?,?,?,1)
 ON CONFLICT(user_id) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,
 updated=excluded.updated,enabled=1""",(uid,float(lat),float(lon),_now()))
 _state(c,uid,"")
 _send(c,uid,"✅ موقعیت ثبت شد و فقط برای محاسبه فاصله استفاده می‌شود.")
 near(c,uid)

def callback(c,uid,cmd):
 parts=cmd.split(":");op=parts[1] if len(parts)>1 else ""
 p=profile(c,uid)
 if op not in ("leave","chatreport") and (
     _active_chat(c,uid) or
     c.execute("SELECT 1 FROM social_chat_queue WHERE user_id=?",(uid,)).fetchone()):
  _leave(c,uid)
 if op=="home":return menu(c,uid)
 if op=="profile":return my_profile(c,uid)
 if op=="setup":
  pp=profile(c,uid)
  if not pp["display_name"]:return callback(c,uid,"soc:edit:name")
  if pp["age"]<18:return callback(c,uid,"soc:edit:age")
  if not pp["gender"]:return callback(c,uid,"soc:gender")
  if not pp["province"]:return callback(c,uid,"soc:provinces:0")
  if not json.loads(pp["interests"]):return interests(c,uid)
  return my_profile(c,uid)
 if op=="interests":return interests(c,uid)
 if op=="interest":
  i=int(parts[2])
  if not 0<=i<len(INTERESTS):raise ValueError("گزینه علاقه‌مندی نامعتبر است")
  v=INTERESTS[i];sel=json.loads(p["interests"])
  if v in sel:sel.remove(v)
  elif len(sel)<5:sel.append(v)
  else:raise ValueError("حداکثر ۵ علاقه‌مندی می‌توانید انتخاب کنید")
  c.execute("UPDATE social_profiles SET interests=?,updated=? WHERE user_id=?",
            (json.dumps(sel,ensure_ascii=False),_now(),uid))
  return interests(c,uid)
 if op=="edit" and parts[2] in ("name","age"):
  _state(c,uid,"soc:edit:"+parts[2])
  return _send(c,uid,"✍️ "+("نام نمایشی (۲ تا ۴۰ حرف)" if parts[2]=="name" else
   "سن شما به سال (۱۸ تا ۱۰۰)")+" را وارد کنید.",[[("↩️ بازگشت","soc:profile")]])
 if op=="gender":
  return _send(c,uid,"جنسیت خود را انتخاب کنید.",[
   [("زن","soc:setgender:زن"),("مرد","soc:setgender:مرد")],
   [("مایلم نگویم","soc:setgender:مایلم نگویم")],[("↩️ پروفایل","soc:profile")]])
 if op=="setgender":
  if parts[2] not in ("زن","مرد","مایلم نگویم"):raise ValueError("جنسیت نامعتبر است")
  c.execute("UPDATE social_profiles SET gender=?,updated=? WHERE user_id=?",
            (parts[2],_now(),uid))
  return callback(c,uid,"soc:setup")
 if op=="provinces":return provinces(c,uid,int(parts[2]))
 if op=="province":
  i=int(parts[2])
  if not 0<=i<len(PROVINCES):raise ValueError("استان نامعتبر است")
  c.execute("UPDATE social_profiles SET province=?,updated=? WHERE user_id=?",
            (PROVINCES[i],_now(),uid))
  return callback(c,uid,"soc:setup")
 if op in ("gallery","mygallery"):
  target=uid if op=="mygallery" else by_code(c,parts[2])
  pp=profile(c,target)
  if not _can_view(c,uid,pp) or _blocked(c,uid,target):
   raise ValueError("گالری خصوصی است")
  images=json.loads(pp["photos"])
  for file_id in images:
   payload={"chat_id":uid,"photo":file_id}
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
      (secrets.token_hex(12),uid,"sendPhoto",json.dumps(payload)))
  _send(c,uid,"🖼 "+str(len(images))+" تصویر در گالری وجود دارد.",
     [[("↩️ پروفایل","soc:profile" if uid==target else "soc:view:"+pp["code"])]])
  return
 if op=="photos":
  images=json.loads(p["photos"])
  return _send(c,uid,"🖼 تصاویر پروفایل: "+str(len(images))+"/۵\n"+
   "برای افزودن، عکس را ارسال کنید. تصاویر به‌صورت امن در تلگرام باقی می‌مانند.",
   [[("➕ افزودن تصویر","soc:addphoto"),("🗑 پاک‌کردن همه تصاویر","soc:clearphotos")],
    [("↩️ پروفایل","soc:profile")]])
 if op=="addphoto":
  _state(c,uid,"soc:photo")
  return _send(c,uid,"یک عکس تلگرام ارسال کنید (حداکثر ۵ عکس).",
                [[("↩️ انصراف","soc:photos")]])
 if op=="clearphotos":
  c.execute("UPDATE social_profiles SET photos='[]',updated=? WHERE user_id=?",(_now(),uid))
  return my_profile(c,uid)
 if op=="privacy":
  return _send(c,uid,"⚙️ پروفایل شما: "+p["visibility"]+
    "\nنمایش بازدید شما به سایرین: "+("بله" if p["show_visitors"] else "خیر")+
    "\nحضور در جستجوی کاربران: "+("بله" if p["discoverable"] else "خیر"),[
    [("🌎 عمومی","soc:visibility:public"),("👥 فقط دوستان","soc:visibility:friends")],
    [("🔒 مخفی","soc:visibility:hidden")],
    [("👁 تغییر نمایش بازدید من","soc:toggle:show_visitors")],
    [("🔎 تغییر حضور در جستجو","soc:toggle:discoverable")],
    [("↩️ پروفایل","soc:profile")]])
 if op=="visibility":
  if parts[2] not in ("public","friends","hidden"):raise ValueError("حریم خصوصی نامعتبر است")
  c.execute("UPDATE social_profiles SET visibility=?,updated=? WHERE user_id=?",
            (parts[2],_now(),uid))
  return callback(c,uid,"soc:privacy")
 if op=="toggle":
  col=parts[2]
  if col not in ("show_visitors","discoverable"):raise ValueError("تنظیم نامعتبر است")
  c.execute("UPDATE social_profiles SET "+col+"=1-"+col+" WHERE user_id=?",(uid,))
  return callback(c,uid,"soc:privacy")
 if op=="invite":
  username=_sales().settings(c).get("bot_username","")
  if not username:raise ValueError("آدرس ربات آماده نیست")
  return _send(c,uid,"🔗 لینک پروفایل من:\nhttps://t.me/"+username+"?start=soc_"+p["code"]+
   "\nشناسه عمومی ثابت: "+p["code"]+
   "\nشناسه عددی تلگرام در لینک قرار نمی‌گیرد.",
   [[("↩️ پروفایل","soc:profile")]])
 if op=="view":return public_card(c,uid,by_code(c,parts[2]))
 if op=="vote":
  if parts[2] not in ("like","dislike"):raise ValueError("واکنش نامعتبر است")
  target=by_code(c,parts[3])
  p2=profile(c,target)
  if not _can_view(c,uid,p2):raise ValueError("پروفایل خصوصی است")
  return vote(c,uid,"profile",target,1 if parts[2]=="like" else -1,
              "soc:view:"+parts[3])
 if op=="follow":
  target=by_code(c,parts[2]);other=profile(c,target)
  if target==uid:raise ValueError("نمی‌توانید خودتان را دنبال کنید")
  if not _can_view(c,uid,other) or _blocked(c,uid,target):raise ValueError("این پروفایل در دسترس نیست")
  if c.execute("SELECT 1 FROM social_follows WHERE follower=? AND followed=?",(uid,target)).fetchone():
   c.execute("DELETE FROM social_follows WHERE follower=? AND followed=?",(uid,target))
  else:
   c.execute("INSERT INTO social_follows VALUES(?,?,?)",(uid,target,_now()))
  return public_card(c,uid,target)
 if op=="friendchat":
  target=by_code(c,parts[2])
  if not _require_complete(c,uid):return
  if target==uid or not _is_friend(c,uid,target):
   raise ValueError("برای گفتگوی دوستانه باید یکدیگر را دنبال کرده باشید")
  if _blocked(c,uid,target):raise ValueError("کاربر مسدود است")
  if _active_chat(c,uid):raise ValueError("ابتدا گفتگوی فعلی را پایان دهید")
  c.execute("""INSERT INTO social_chat_invites(requester,recipient,created,status)
      VALUES(?,?,?,'pending') ON CONFLICT(requester,recipient) DO UPDATE SET
      created=excluded.created,status='pending'""",(uid,target,_now()))
  _send(c,target,"💬 "+_public_name(c,uid)+" درخواست گفتگوی دوستانه فرستاده است. "
       "پیام‌ها از طریق ربات منتقل و برای رسیدگی به گزارش‌ها در سامانه شرکت ثبت و "
       "توسط مدیران مجاز قابل بررسی هستند.",
       [[("✅ پذیرش گفتگو","soc:acceptfriend:"+p["code"]),
         ("❌ رد درخواست","soc:rejectfriend:"+p["code"])]])
  return _send(c,uid,"✅ درخواست گفتگو ارسال شد.",
    [[("↩️ پروفایل دوست","soc:view:"+parts[2])]])
 if op in ("acceptfriend","rejectfriend"):
  origin=by_code(c,parts[2])
  row=c.execute("""SELECT created FROM social_chat_invites WHERE requester=? AND
      recipient=? AND status='pending'""",(origin,uid)).fetchone()
  if not row or _now()-row["created"]>86400:raise ValueError("دعوت گفتگو منقضی یا نامعتبر است")
  if op=="rejectfriend":
   c.execute("UPDATE social_chat_invites SET status='rejected' WHERE requester=? AND recipient=?",
             (origin,uid))
   return _send(c,uid,"درخواست گفتگو رد شد.")
  if not _require_complete(c,uid):return
  if not _is_friend(c,origin,uid) or _blocked(c,origin,uid):
   raise ValueError("دوستی فعال نیست")
  if _active_chat(c,uid) or _active_chat(c,origin):
   raise ValueError("یکی از کاربران در گفتگوی دیگری است")
  c.execute("UPDATE social_chat_invites SET status='accepted' WHERE requester=? AND recipient=?",
            (origin,uid))
  _leave(c,uid);_leave(c,origin)
  cid=c.execute("""INSERT INTO social_chat_pairs(first_user,second_user,created,type)
                   VALUES(?,?,?,'friend')""",(origin,uid,_now())).lastrowid
  for receiver in (uid,origin):
   _state(c,receiver,"soc:chat:"+str(cid))
   _send(c,receiver,"✅ گفتگو با "+_public_name(c,origin if receiver==uid else uid)+
     " شروع شد. گفتگو در ربات ذخیره می‌شود و مدیران مجاز برای رسیدگی به گزارش‌ها "
     "می‌توانند آن را بررسی کنند.",
     [[("⛔ پایان گفتگو","soc:leave"),("🚩 گزارش","soc:chatreport")]])
  return
 if op=="following":
  rows=c.execute("SELECT followed FROM social_follows WHERE follower=? ORDER BY created DESC LIMIT 30",(uid,)).fetchall()
  buttons=[]
  for row in rows:
   pp=profile(c,row["followed"])
   if _can_view(c,uid,pp) and not _blocked(c,uid,row["followed"]):
    buttons.append([(_public_name(c,row["followed"]),"soc:view:"+pp["code"])])
  return _send(c,uid,"👀 کاربرانی که دنبال می‌کنید:",buttons+[[("↩️ مسیحا سوشال","soc:home")]])
 if op=="followers":
  rows=c.execute("SELECT follower FROM social_follows WHERE followed=? ORDER BY created DESC LIMIT 30",(uid,)).fetchall()
  buttons=[]
  for row in rows:
   pp=profile(c,row["follower"])
   if _can_view(c,uid,pp):
    buttons.append([(_public_name(c,row["follower"]),"soc:view:"+pp["code"])])
  return _send(c,uid,"👥 دنبال‌کنندگان شما:",buttons+[[("↩️ پروفایل","soc:profile")]])
 if op=="visitors":return _visitors(c,uid)
 if op=="comments":return _comments(c,uid,by_code(c,parts[2]))
 if op=="comment":
  target=by_code(c,parts[2]);q=profile(c,target)
  if not _can_view(c,uid,q) or _blocked(c,uid,target):raise ValueError("پروفایل خصوصی است")
  _state(c,uid,"soc:comment:"+q["code"])
  return _send(c,uid,"✍️ نظر خود را درباره این پروفایل بنویسید (حداکثر ۵۰۰ حرف).",
        [[("↩️ انصراف","soc:view:"+q["code"])]])
 if op=="search":
  _state(c,uid,"soc:search")
  return _send(c,uid,"🔎 نام یا شناسه عمومی مثل MS-1234ABCD را بنویسید.",
                [[("↩️ بازگشت","soc:home")]])
 if op=="leaders":return _leaders(c,uid)
 if op=="block":
  target=by_code(c,parts[2])
  if target==uid:raise ValueError("نمی‌توانید خودتان را مسدود کنید")
  c.execute("INSERT OR IGNORE INTO social_blocks VALUES(?,?,?)",(uid,target,_now()))
  c.execute("DELETE FROM social_follows WHERE (follower=? AND followed=?) OR (follower=? AND followed=?)",
            (uid,target,target,uid))
  return _send(c,uid,"⛔ کاربر مسدود شد.",[[("↩️ مسیحا سوشال","soc:home")]])
 if op=="report":
  target=by_code(c,parts[2])
  _state(c,uid,"soc:report:"+parts[2])
  return _send(c,uid,"دلیل گزارش را بنویسید.",
   [[("↩️ بازگشت","soc:view:"+parts[2])]])
 if op in ("random","filtered"):
  return start_matching(c,uid,op)
 if op=="near":return near(c,uid)
 if op=="leave":return end_chat(c,uid)
 if op=="chatreport":
  chat=_active_chat(c,uid)
  if not chat:raise ValueError("گفتگو فعالی وجود ندارد")
  target=chat["second_user"] if chat["first_user"]==uid else chat["first_user"]
  c.execute("INSERT INTO social_reports(reporter,target,reason,created) VALUES(?,?,?,?)",
      (uid,target,"گزارش از چت ناشناس: گفتگو "+str(chat["id"]),_now()))
  return _send(c,uid,"🚩 گزارش گفتگو ثبت شد. مدیر مجاز می‌تواند سوابق این گفتگو را بررسی کند.",
    [[("⛔ پایان چت","soc:leave")]])
 if op=="geoshare":
  if not _require_complete(c,uid):return
  _state(c,uid,"soc:geo")
  return _send(c,uid,"📍 با ارسال Location تلگرام، به نمایش کاربران نزدیک رضایت می‌دهید. "
     "آدرس و مختصات دقیق شما به سایر کاربران نشان داده نمی‌شود؛ "
    "مدیر مجاز شرکت می‌تواند اطلاعات دقیق ثبت‌شده را برای رسیدگی بررسی کند.",
    [[("↩️ انصراف","soc:home")]])
 if op=="geodelete":
  c.execute("DELETE FROM social_geo WHERE user_id=?",(uid,))
  return _send(c,uid,"موقعیت شما حذف شد.",
                [[("↩️ مسیحا سوشال","soc:home")]])
 raise ValueError("دستور اجتماعی نامعتبر است")

def input_text(c,uid,state,m,update_id):
 parts=state.split(":");op=parts[1]
 if op=="chat":return _chat_message(c,uid,m,int(parts[2]))
 if op=="waiting":
  return _send(c,uid,"⏳ هنوز در صف گفتگو هستید.",
    [[("❌ خروج از صف","soc:leave")]])
 if op=="geo":return geo_input(c,uid,m)
 if op=="edit":
  value=(m.get("text") or "").strip()
  if parts[2]=="name":
   if not 2<=len(value)<=40 or any(ord(ch)<32 for ch in value):
    raise ValueError("نام باید ۲ تا ۴۰ کاراکتر باشد")
   c.execute("UPDATE social_profiles SET display_name=?,updated=? WHERE user_id=?",
     (value,_now(),uid))
  elif parts[2]=="age":
   if not re.fullmatch(r"[0-9]{2,3}",value) or not 18<=int(value)<=100:
    raise ValueError("استفاده از بخش اجتماعی برای افراد ۱۸ تا ۱۰۰ سال مجاز است")
   c.execute("UPDATE social_profiles SET age=?,updated=? WHERE user_id=?",
     (int(value),_now(),uid))
  else:raise ValueError("فیلد نامعتبر است")
  _state(c,uid,"")
  return callback(c,uid,"soc:setup")
 if op=="photo":
  photos=m.get("photo") or []
  if not photos or not isinstance(photos,list) or not photos[-1].get("file_id"):
   raise ValueError("لطفاً عکس تلگرام بفرستید")
  p=profile(c,uid);current=json.loads(p["photos"])
  if len(current)>=5:raise ValueError("حداکثر پنج تصویر برای هر پروفایل مجاز است")
  file_id=photos[-1]["file_id"]
  if not isinstance(file_id,str) or not 10<=len(file_id)<=400:raise ValueError("شناسه عکس نامعتبر است")
  if file_id not in current:current.append(file_id)
  c.execute("UPDATE social_profiles SET photos=?,updated=? WHERE user_id=?",
      (json.dumps(current),_now(),uid))
  _state(c,uid,"")
  return callback(c,uid,"soc:photos")
 if op=="comment":
  target=by_code(c,parts[2]);p=profile(c,target)
  if not _can_view(c,uid,p) or _blocked(c,uid,target):raise ValueError("پروفایل در دسترس نیست")
  text=(m.get("text") or "").strip()
  if not 2<=len(text)<=500:raise ValueError("نظر باید بین ۲ تا ۵۰۰ کاراکتر باشد")
  last=c.execute("SELECT created FROM social_profile_comments WHERE author=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
  if last and _now()-last["created"]<60:raise ValueError("بین دو نظر حداقل یک دقیقه فاصله باشد")
  c.execute("INSERT INTO social_profile_comments(owner,author,body,created) VALUES(?,?,?,?)",
   (target,uid,text,_now()))
  _state(c,uid,"")
  return _comments(c,uid,target)
 if op=="search":
  query=(m.get("text") or "").strip()
  if not 2<=len(query)<=45:raise ValueError("عبارت جستجو باید ۲ تا ۴۵ کاراکتر باشد")
  if query.upper().startswith("MS-"):
   try:
    target=by_code(c,query.upper());p=profile(c,target)
    rows=[p] if p["discoverable"] else []
   except ValueError:rows=[]
  else:
   escape=query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
   rows=c.execute("""SELECT * FROM social_profiles WHERE discoverable=1
    AND display_name LIKE ? ESCAPE '\\' AND visibility='public'
    AND display_name<>'' ORDER BY updated DESC LIMIT 15""",("%"+escape+"%",)).fetchall()
  rows=[r for r in rows if _can_view(c,uid,r) and not _blocked(c,uid,r["user_id"])]
  _state(c,uid,"")
  return _send(c,uid,"🔎 نتیجه جستجو: "+query+
       ("\nکاربری پیدا نشد." if not rows else ""),
       [[((r["display_name"] or "کاربر")+" • "+r["code"],"soc:view:"+r["code"])] for r in rows]+
       [[("↩️ مسیحا سوشال","soc:home")]])
 if op=="report":
  target=by_code(c,parts[2]);reason=(m.get("text") or "").strip()
  if not 5<=len(reason)<=500:raise ValueError("دلیل گزارش باید ۵ تا ۵۰۰ کاراکتر باشد")
  c.execute("INSERT INTO social_reports(reporter,target,reason,created) VALUES(?,?,?,?)",
            (uid,target,reason,_now()))
  _state(c,uid,"")
  return _send(c,uid,"✅ گزارش ثبت و برای مدیریت ارسال شد.",[[("🏠 مسیحا سوشال","soc:home")]])
 raise ValueError("ورودی نامعتبر است")

def admin_view(c,req):
 """For authenticated management API ONLY. NEVER route via bot callbacks."""
 mode=req.get("view","overview")
 if mode=="overview":
  metrics={
   "profiles":c.execute("SELECT COUNT(*) FROM social_profiles").fetchone()[0],
   "completed":c.execute("""SELECT COUNT(*) FROM social_profiles
       WHERE display_name<>'' AND age>=18 AND gender<>'' AND province<>'' AND interests!='[]'""").fetchone()[0],
   "active_chats":c.execute("SELECT COUNT(*) FROM social_chat_pairs WHERE closed=0").fetchone()[0],
   "chat_total":c.execute("SELECT COUNT(*) FROM social_chat_pairs").fetchone()[0],
   "messages":c.execute("SELECT COUNT(*) FROM social_chat_messages").fetchone()[0],
   "pending_reports":c.execute("SELECT COUNT(*) FROM social_reports WHERE status='pending'").fetchone()[0],
   "geo_optins":c.execute("SELECT COUNT(*) FROM social_geo WHERE enabled=1").fetchone()[0]
  }
  metrics["rewards"]={k:c.execute("SELECT v FROM media_settings WHERE k=?",(k,)).fetchone()[0]
      for k in (*DEFAULT_POINTS,"social_chat_retention_days")}
  return metrics
 if mode=="users":
  rows=c.execute("""SELECT p.user_id,p.code,p.display_name,p.gender,p.age,p.province,p.interests,
       p.visibility,p.discoverable,u.name,u.phone,u.telegram_username,u.blocked,
       g.lat AS location_lat,g.lon AS location_lon,g.updated AS location_updated
    FROM social_profiles p JOIN users u ON u.id=p.user_id
    LEFT JOIN social_geo g ON g.user_id=p.user_id AND g.enabled=1
    ORDER BY p.created DESC LIMIT 200""").fetchall()
  return [dict(r) for r in rows]
 if mode=="chats":
  rows=c.execute("""SELECT t.id,t.first_user,t.second_user,t.type,t.created,t.closed,
    COALESCE(a.code,'') AS first_code,COALESCE(a.display_name,'') AS first_name,
    COALESCE(b.code,'') AS second_code,COALESCE(b.display_name,'') AS second_name,
    (SELECT COUNT(*) FROM social_chat_messages m WHERE m.conversation=t.id) AS messages
    FROM social_chat_pairs t LEFT JOIN social_profiles a ON a.user_id=t.first_user
    LEFT JOIN social_profiles b ON b.user_id=t.second_user
    ORDER BY t.id DESC LIMIT 100""").fetchall()
  return [dict(r) for r in rows]
 if mode=="messages":
  chat_id=req.get("id")
  if type(chat_id) is not int or not 1<=chat_id<=2**31-1:
   raise ValueError("شناسه گفتگو نامعتبر است")
  exists=c.execute("SELECT 1 FROM social_chat_pairs WHERE id=?",(chat_id,)).fetchone()
  if not exists:raise ValueError("گفتگو وجود ندارد")
  rows=c.execute("""SELECT m.id,m.created,m.sender,m.body,
    COALESCE(p.code,'') AS public_code,
    COALESCE(p.display_name,'') AS display_name
    FROM social_chat_messages m LEFT JOIN social_profiles p ON p.user_id=m.sender
    WHERE conversation=? ORDER BY m.id DESC LIMIT 200""",(chat_id,)).fetchall()
  return [dict(r) for r in reversed(rows)]
 if mode=="reports":
  rows=c.execute("""SELECT r.id,r.reporter,r.target,r.reason,r.created,r.status,
   COALESCE(a.code,'') AS reporter_code,COALESCE(b.code,'') AS target_code
   FROM social_reports r LEFT JOIN social_profiles a ON a.user_id=r.reporter
   LEFT JOIN social_profiles b ON b.user_id=r.target
   ORDER BY r.id DESC LIMIT 150""").fetchall()
  return [dict(r) for r in rows]
 raise ValueError("نمای مدیریت اجتماعی ناشناخته است")

def invite_start(c,uid,body):
 if not body.startswith("/start soc_"):return False
 token=body.split("soc_",1)[1].strip().split()[0]
 # Only a generated opaque public ID appears in the invitation.
 try:target=by_code(c,token)
 except ValueError:return False
 public_card(c,uid,target)
 return True
