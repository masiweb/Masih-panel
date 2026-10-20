#!/usr/bin/env python3
"""Authorized RozMusic catalog importer. Stream audio to Telegram, then discard
temporary MP3. Only Telegram file IDs and catalog metadata are publicly used.
"""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import roz_telegram_upload as telegram_upload
import xml.etree.ElementTree as ET

ROOT="https://rozmusic.com/sitemap.xml"
UA="MasihaCatalogSync/1.0 (licensed RozMusic catalog integration)"
MAX_HTTP=3_000_000
MAX_LYRICS=30_000

def trusted(url,kind="page"):
 p=urlsplit(url)
 if p.scheme!="https" or p.username or p.password or p.port not in (None,443):
  raise ValueError("HTTPS-only RozMusic source required")
 host=(p.hostname or "").lower()
 if kind in ("page","sitemap") and host not in ("rozmusic.com","www.rozmusic.com"):
  raise ValueError("Untrusted RozMusic page domain")
 if kind=="mp3" and not (host=="rozmusic.com" or host.endswith(".rozmusic.com")):
  raise ValueError("Untrusted music file domain")
 if kind=="mp3" and not p.path.lower().endswith(".mp3"):
  raise ValueError("Expected MP3 file path")
 return url

def get(url,kind="page"):
 trusted(url,kind)
 req=Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xml;q=0.9,*/*;q=0.5"})
 with urlopen(req,timeout=20) as rsp:
  trusted(rsp.url,kind)
  raw=rsp.read(MAX_HTTP+1)
  if len(raw)>MAX_HTTP:raise ValueError("Oversized catalog response")
  return raw.decode("utf-8","replace")

def urls_in_xml(raw):
 root=ET.fromstring(raw)
 return [el.text.strip() for el in root.findall(".//{*}loc") if el.text]

class LyricsParser(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True)
  self.depth=0
  self.level=0
  self.output=[]
  self.inside_p=0
 def handle_starttag(self,tag,attrs):
  cls=dict(attrs).get("class","")
  if tag=="div" and not self.depth and "matnmusic" in cls.split():
   self.depth=1
   return
  if self.depth:
   if tag=="div":self.depth+=1
   if tag=="p" and self.inside_p==0:self.inside_p=1;self.output.append("\n")
   elif tag=="p" and self.inside_p:self.inside_p+=1
   if self.inside_p and tag in ("br","hr"):self.output.append("\n")
 def handle_endtag(self,tag):
  if self.depth:
   if tag=="p" and self.inside_p:
    self.inside_p-=1
    self.output.append("\n")
   if tag=="div":self.depth-=1
 def handle_data(self,data):
  if self.depth and self.inside_p:self.output.append(data)

def text(fragment):
 return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",fragment))).strip()

def parse_article(body):
 h=re.search(r"<h1\b[^>]*>(.*?)</h1>",body,re.I|re.S)
 heading=text(h.group(1)) if h else ""
 # Artist and song title are from structured Iranian music page headings.
 match=re.search(r"(?:دانلود\s+)?آهنگ(?:\s+جدید)?\s+(.+?)\s+به\s+نام\s+(.+)",heading,re.I)
 if not match:return None
 artist,title=[t.strip(" \n\t-|،")[:160] for t in match.groups()]
 if len(title)<2 or len(artist)<2:return None
 # Prefer published 320-kbit MP3 download href (audio source may be 128).
 candidates=re.findall(r'<a\b[^>]*href=["\x27]([^"\x27]+\.mp3(?:\?[^"\x27]*)?)["\x27][^>]*>',body,re.I)
 if not candidates:
  candidates=re.findall(r'<source\b[^>]*src=["\x27]([^"\x27]+\.mp3(?:\?[^"\x27]*)?)["\x27]',body,re.I)
 valid_mp3=[]
 for candidate in candidates:
  try:
   path=trusted(html.unescape(candidate),"mp3")
   if path not in valid_mp3:valid_mp3.append(path)
  except ValueError:continue
 if not valid_mp3:return None
 ly=LyricsParser()
 ly.feed(body)
 lyrics="\n".join(re.sub(r"[ \t]+"," ",line).strip() for line in
                  "".join(ly.output).splitlines() if line.strip())
 # Prefer the article's own featured image to generic (sometimes stale) OG art.
 cover=""
 image_sources=re.findall(r'<img\b[^>]*?data-src=["\x27]([^"\x27]+)["\x27]',body,re.I)
 image_sources+=re.findall(r'<meta\b[^>]*property=["\x27]og:image["\x27][^>]*content=["\x27]([^"\x27]+)["\x27]',body,re.I)
 for img in image_sources:
  try:
   cover=telegram_upload.validate_cover_url(html.unescape(img))
   break
  except ValueError:continue
 return {"artist":artist,"title":title,"lyrics":lyrics[:MAX_LYRICS],
         "mp3":valid_mp3[0],"mp3_fallbacks":valid_mp3[1:3],"cover":cover}

def database():
 import sales
 c=sales.conn()
 c.executescript("""
 CREATE TABLE IF NOT EXISTS roz_catalog_queue(
  url TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated INTEGER NOT NULL DEFAULT 0
 );
 CREATE INDEX IF NOT EXISTS roz_catalog_progress ON roz_catalog_queue(state,attempts);
 CREATE TABLE IF NOT EXISTS roz_catalog_sources(
  article_url TEXT PRIMARY KEY, media_id INTEGER NOT NULL,
  imported INTEGER NOT NULL, updated INTEGER NOT NULL
 );
 """)
 c.execute("""INSERT OR IGNORE INTO users(id,name,balance,created)
              VALUES(0,'RozMusic Licensed Catalog',0,?)""",(int(time.time()),))
 # Reuse an existing PUBLIC all playlist created by the bot administrator.
 # Do not create an unwanted second playlist named all on their behalf.
 cfg=sales.settings(c)
 admins=[a for a in cfg.get("admins",[]) if type(a) is int and a>0]
 preferred=None
 if admins:
  binds=",".join("?" for _ in admins)
  preferred=c.execute("""SELECT id FROM playlists WHERE kind='music' AND
        name='all' AND private=0 AND removed=0 AND owner_id IN ("""+binds+
        """) ORDER BY id ASC LIMIT 1""",admins).fetchone()
 legacy=c.execute("""SELECT id FROM playlists WHERE owner_id=0 AND kind='music'
                     AND private=0 AND name='all' AND removed=0""").fetchone()
 if preferred:
  playlist_id=preferred[0]
  if legacy and legacy[0]!=playlist_id:
   c.execute("""INSERT OR IGNORE INTO playlist_entries(playlist_id,media_id,created)
          SELECT ?,media_id,created FROM playlist_entries WHERE playlist_id=?""",
          (playlist_id,legacy[0]))
   c.execute("UPDATE playlists SET removed=1 WHERE id=? AND owner_id=0",(legacy[0],))
 elif legacy:
  playlist_id=legacy[0]
 else:
  playlist_id=c.execute("""INSERT INTO playlists(owner_id,kind,name,description,
    private,invite_token,created) VALUES(0,'music','all',?,0,?,?)""",
    ("مجموعه مجاز موسیقی RozMusic",__import__("secrets").token_urlsafe(16),int(time.time()))).lastrowid
 c.commit()
 return c,playlist_id

def index(c,delay=1.0,max_sitemaps=0):
 xml=get(ROOT,"sitemap")
 files=[u for u in urls_in_xml(xml) if re.search(r'/sitemap-pt-post-\d{4}-\d\d\.xml$',u)]
 files=list(dict.fromkeys(files))
 if max_sitemaps:files=files[:max_sitemaps]
 added=0
 for j,file in enumerate(files,1):
  urls=urls_in_xml(get(file,"sitemap"))
  batch=[]
  for u in urls:
   try:batch.append((trusted(u),))
   except ValueError:continue
  before=c.total_changes
  c.executemany("INSERT OR IGNORE INTO roz_catalog_queue(url) VALUES(?)",batch)
  added+=c.total_changes-before
  c.commit()
  if j==1 or j%10==0 or j==len(files):
   print("INDEX",j,"OF",len(files),"NEW",added,"URLS",len(batch),flush=True)
  if delay and j<len(files):time.sleep(delay)
 return len(files),added

def save(c,playlist_id,article_url,row,telegram_file_id,cover_id=""):
 """Publish only AFTER verified Telegram upload. No external URL in public rows."""
 if not isinstance(telegram_file_id,str) or not 10<=len(telegram_file_id)<=1024:
  raise ValueError("Missing verified Telegram audio file_id")
 stamp=int(time.time())
 stable_id=hashlib.sha256(("rozmusic:"+article_url).encode()).hexdigest()
 existing=c.execute("SELECT id,status FROM media_items WHERE kind='music' AND unique_id=?",
                    (stable_id,)).fetchone()
 newly_published=not existing or existing["status"]!="published"
 if existing:
  mid=existing[0]
  cur=c.execute("""UPDATE media_items SET title=?,artist=?,lyrics=?,source_url='',
    telegram_method='sendAudio',file_id=?,cover_id=?,status='published',rights_ok=1
    WHERE id=? AND owner_id=0""",
    (row["title"],row["artist"],row["lyrics"],telegram_file_id,cover_id,mid))
  if cur.rowcount!=1:raise RuntimeError("Imported media does not belong to system owner")
 else:
  mid=c.execute("""INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,
   source_url,title,artist,lyrics,cover_id,status,rights_ok,created)
   VALUES(0,'music','sendAudio',?,?,'',?,?,?,?,'published',1,?)""",
   (telegram_file_id,stable_id,row["title"],row["artist"],row["lyrics"],cover_id,stamp)).lastrowid
 c.execute("""INSERT OR IGNORE INTO playlist_entries(playlist_id,media_id,created)
              VALUES(?,?,?)""",(playlist_id,mid,stamp))
 # Announce to subscribed bot users only when a Telegram file first
 # becomes published; old URL-only draft records are migrated safely.
 if newly_published:
  import media_library
  media_library.broadcast_new_media(c,mid)
 c.execute("""INSERT INTO roz_catalog_sources(article_url,media_id,imported,updated)
   VALUES(?,?,?,?) ON CONFLICT(article_url) DO UPDATE SET media_id=excluded.media_id,
   updated=excluded.updated""",(article_url,mid,stamp,stamp))

def rollback_legacy_links(c):
 """Old link-only imports must be requeued, never shown to listeners."""
 n=c.execute("""UPDATE roz_catalog_queue SET state='pending',attempts=0,last_error=''
    WHERE url IN (
      SELECT s.article_url FROM roz_catalog_sources s JOIN media_items m
      ON m.id=s.media_id WHERE m.owner_id=0 AND
        (m.telegram_method!='sendAudio' OR m.file_id=''))""").rowcount
 c.execute("""UPDATE media_items SET status='draft',source_url=''
              WHERE owner_id=0 AND (telegram_method!='sendAudio' OR file_id='')""")
 c.commit()
 return n

def confirmed(c,article_url):
 row=c.execute("""SELECT m.id FROM roz_catalog_sources s JOIN media_items m
  ON m.id=s.media_id WHERE s.article_url=? AND m.owner_id=0
  AND m.telegram_method='sendAudio' AND m.status='published'
  AND LENGTH(m.file_id)>=10""",(article_url,)).fetchone()
 return bool(row)

def stats(c,playlist_id):
 state={r[0]:r[1] for r in c.execute("SELECT state,COUNT(*) FROM roz_catalog_queue GROUP BY state")}
 n=c.execute("""SELECT COUNT(*) FROM playlist_entries e JOIN media_items m ON m.id=e.media_id
                   WHERE e.playlist_id=? AND m.status='published'
                   AND m.telegram_method='sendAudio' AND LENGTH(m.file_id)>9""",
               (playlist_id,)).fetchone()[0]
 return {"queued":sum(state.values()),"pending":state.get("pending",0),
         "done":state.get("done",0),"skipped":state.get("skipped",0),
         "error":state.get("error",0),"playlist_all":n}

def run(c,pid,delay=3.0,max_posts=0,prepare=True):
 """The only public publishing path is confirmed sendAudio + Telegram file_id."""
 import http.client
 import sales
 cfg=sales.settings(c)
 if not cfg.get("enabled") or not cfg.get("token"):
  raise RuntimeError("Bot must be configured and enabled before music uploads")
 telegram_upload.verified_storage_chat(cfg)
 if prepare:rollback_legacy_links(c)
 processed=0
 while True:
  c.execute("BEGIN IMMEDIATE")
  try:
   item=c.execute("""SELECT q.url,q.attempts FROM roz_catalog_queue q
   WHERE q.state='pending' OR q.state='error' AND q.attempts<3
   ORDER BY CASE WHEN EXISTS(SELECT 1 FROM roz_catalog_sources s WHERE s.article_url=q.url)
                 THEN 0 ELSE 1 END,
            CASE WHEN q.state='pending' THEN 0 ELSE 1 END,q.url LIMIT 1""").fetchone()
   if item:
    c.execute("UPDATE roz_catalog_queue SET state='processing',updated=? WHERE url=?",
              (int(time.time()),item["url"]))
   c.commit()
  except BaseException:
   c.rollback()
   raise
  if not item:break
  url=item["url"]
  try:
   if confirmed(c,url):
    c.execute("""UPDATE roz_catalog_queue SET state='done',updated=?
                 WHERE url=?""",(int(time.time()),url))
   else:
    data=parse_article(get(url))
    if not data:
     c.execute("""UPDATE roz_catalog_queue SET state='skipped',last_error='Not an MP3 music page',updated=?
                  WHERE url=?""",(int(time.time()),url))
    else:
     sources=[data["mp3"]]+data.get("mp3_fallbacks",[])
     last_error=None
     for source in sources:
      try:
       file_id,unique_id,size,deleted=telegram_upload.stream_once(
           source,data["artist"],data["title"],cfg,article_url=url)
       break
      except (ValueError,HTTPError) as exc:
       # e.g. old 320kbps version is over 47MiB or no longer available.
       # Try authorized 128kbps alternative without losing the article.
       if isinstance(exc,ValueError) and "50 MB" not in str(exc) and "MP3" not in str(exc):
        raise
       if isinstance(exc,HTTPError) and exc.code not in (404,410):
        raise
       last_error=exc
     else:
      raise last_error or ValueError("No downloadable MP3 candidate")
     cover_id=""
     if data.get("cover"):
      try:
       cover_id=telegram_upload.upload_cover(data["cover"],cfg)
      except (ValueError,HTTPError,URLError,TimeoutError,OSError,RuntimeError) as cover_exc:
       print("COVER_RETRY_LATER",type(cover_exc).__name__,flush=True)
     save(c,pid,url,data,file_id,cover_id)
     c.execute("""UPDATE roz_catalog_queue SET state='done',last_error='',updated=?
                  WHERE url=?""",(int(time.time()),url))
     print("TELEGRAM_FILE_SAVED bytes",size,"TEMP_FILE_REMOVED yes",
           "STORAGE_MESSAGE_REMOVED",deleted,flush=True)
  except HTTPError as exc:
   if exc.code in (429,503):
    print("SOURCE_RATE_LIMIT_PAUSE",exc.code,flush=True)
    c.execute("UPDATE roz_catalog_queue SET state='pending',updated=0 WHERE url=?",(url,))
    c.commit()
    time.sleep(180)
    continue
   c.execute("""UPDATE roz_catalog_queue SET state='error',attempts=attempts+1,
                last_error=?,updated=? WHERE url=?""",
             ("Source HTTP "+str(exc.code),int(time.time()),url))
  except (URLError,TimeoutError,ValueError,OSError,RuntimeError,
          http.client.HTTPException,ET.ParseError) as exc:
   # Never log Telegram token, MP3 URL, secrets, or a binary request body.
   detail=type(exc).__name__
   if isinstance(exc,ValueError) and "50 MB" in str(exc):detail="MP3 exceeds Telegram upload limit"
   if isinstance(exc,RuntimeError) and "HTTP " in str(exc):
    detail="Telegram HTTP "+str(exc).rsplit("HTTP ",1)[1][:3]
   c.execute("""UPDATE roz_catalog_queue SET state='error',attempts=attempts+1,
                last_error=?,updated=? WHERE url=?""",
             (detail,int(time.time()),url))
   print("IMPORT_RETRY",detail,flush=True)
   if "Telegram HTTP 429" in detail:time.sleep(180)
  c.commit()
  processed+=1
  if processed==1 or processed%25==0:
   print("PROGRESS",json.dumps(stats(c,pid)),flush=True)
  if max_posts and processed>=max_posts:break
  if delay:time.sleep(delay)
 return processed

def run_parallel(c,pid,delay=0.6,workers=5):
 """Run bounded jobs concurrently with separate SQLite connections.

 Claiming is atomic; a terminated process leaves jobs recoverable.
 The fingerprint engine retains its existing inter-process lock.
 """
 import fcntl
 from concurrent.futures import ThreadPoolExecutor
 import sales
 if not 1<=workers<=6:raise ValueError("Invalid worker count")
 lockpath=sales.BASE/"rozmusic-import.lock"
 lockpath.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 with lockpath.open("a+b") as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:
   raise RuntimeError("A music importer is already running")
  # No other importer holds the flock; restore jobs interrupted by stop.
  c.execute("UPDATE roz_catalog_queue SET state='pending',updated=0 WHERE state='processing'")
  c.commit()
  rollback_legacy_links(c)
  dbfile=c.execute("PRAGMA database_list").fetchone()[2]
  def worker(index):
   worker_db=sqlite3.connect(dbfile,timeout=45)
   worker_db.row_factory=sqlite3.Row
   worker_db.execute("PRAGMA busy_timeout=45000")
   try:
    processed=run(worker_db,pid,delay,prepare=False)
    print("UPLOAD_WORKER",index,"PROCESSED",processed,flush=True)
    return processed
   finally:worker_db.close()
  with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="music-import") as pool:
   results=list(pool.map(worker,range(workers)))
  return sum(results)

def main():
 p=argparse.ArgumentParser(description="Authorized RozMusic -> public playlist all sync")
 p.add_argument("action",choices=("index","run","upload","status"))
 p.add_argument("--max-sitemaps",type=int,default=0)
 p.add_argument("--max-posts",type=int,default=0)
 p.add_argument("--delay",type=float,default=3.0)
 p.add_argument("--workers",type=int,default=1)
 a=p.parse_args()
 if a.max_sitemaps<0 or a.max_posts<0 or not 0.5<=a.delay<=30 or not 1<=a.workers<=6:
  p.error("Invalid import bounds: use delay 0.5 to 30 seconds")
 c,pid=database()
 try:
  if a.action in ("index","run"):
   count,added=index(c,a.delay,a.max_sitemaps)
   print("SITEMAPS_INDEXED",count,"NEW_URLS",added,flush=True)
  if a.action in ("run","upload"):
   n=(run_parallel(c,pid,a.delay,a.workers)
      if a.workers>1 and a.max_posts==0 else run(c,pid,a.delay,a.max_posts))
   print("POSTS_PROCESSED_THIS_RUN",n,flush=True)
  print("CATALOG_STATUS",json.dumps(stats(c,pid)),flush=True)
 finally:c.close()

if __name__=="__main__":main()