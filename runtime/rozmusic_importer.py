#!/usr/bin/env python3
"""Authorized RozMusic catalog importer: incremental, polite, crash-resumable.

Only catalog metadata, lyrics and publisher-hosted MP3 URLs are stored.
No music files are downloaded to or served from the VPN server.
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
 media=""
 for candidate in candidates:
  try:media=trusted(html.unescape(candidate),"mp3");break
  except ValueError:continue
 if not media:return None
 ly=LyricsParser()
 ly.feed(body)
 lyrics="\n".join(re.sub(r"[ \t]+"," ",line).strip() for line in
                  "".join(ly.output).splitlines() if line.strip())
 return {"artist":artist,"title":title,"lyrics":lyrics[:MAX_LYRICS],"mp3":media}

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
 playlist=c.execute("""SELECT id FROM playlists WHERE owner_id=0 AND kind='music'
                       AND private=0 AND name='all' AND removed=0""").fetchone()
 if not playlist:
  playlist_id=c.execute("""INSERT INTO playlists(owner_id,kind,name,description,
    private,invite_token,created) VALUES(0,'music','all',?,0,?,?)""",
    ("مجموعه مجاز موسیقی RozMusic",__import__("secrets").token_urlsafe(16),int(time.time()))).lastrowid
 else:playlist_id=playlist[0]
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

def save(c,playlist_id,article_url,row):
 import hashlib
 stamp=int(time.time())
 stable_id=hashlib.sha256(("rozmusic:"+article_url).encode()).hexdigest()
 existing=c.execute("SELECT id FROM media_items WHERE kind='music' AND unique_id=?",
                    (stable_id,)).fetchone()
 if existing:
  mid=existing[0]
  c.execute("""UPDATE media_items SET title=?,artist=?,lyrics=?,source_url=?,
    status='published',rights_ok=1 WHERE id=? AND owner_id=0""",
    (row["title"],row["artist"],row["lyrics"],row["mp3"],mid))
 else:
  mid=c.execute("""INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,
   source_url,title,artist,lyrics,status,rights_ok,created)
   VALUES(0,'music','link','',?,?,?,?,?,'published',1,?)""",
   (stable_id,row["mp3"],row["title"],row["artist"],row["lyrics"],stamp)).lastrowid
 c.execute("""INSERT OR IGNORE INTO playlist_entries(playlist_id,media_id,created)
              VALUES(?,?,?)""",(playlist_id,mid,stamp))
 c.execute("""INSERT INTO roz_catalog_sources(article_url,media_id,imported,updated)
   VALUES(?,?,?,?) ON CONFLICT(article_url) DO UPDATE SET media_id=excluded.media_id,
   updated=excluded.updated""",(article_url,mid,stamp,stamp))

def stats(c,playlist_id):
 state={r[0]:r[1] for r in c.execute("SELECT state,COUNT(*) FROM roz_catalog_queue GROUP BY state")}
 n=c.execute("SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=?",(playlist_id,)).fetchone()[0]
 return {"queued":sum(state.values()),"pending":state.get("pending",0),
         "done":state.get("done",0),"skipped":state.get("skipped",0),
         "error":state.get("error",0),"playlist_all":n}

def run(c,pid,delay=1.2,max_posts=0):
 processed=0
 while True:
  item=c.execute("""SELECT url,attempts FROM roz_catalog_queue
   WHERE state='pending' OR state='error' AND attempts<3
   ORDER BY CASE WHEN state='pending' THEN 0 ELSE 1 END,url LIMIT 1""").fetchone()
  if not item:break
  url=item["url"]
  try:
   data=parse_article(get(url))
   if not data:
    c.execute("""UPDATE roz_catalog_queue SET state='skipped',last_error='Not a standalone MP3 page',updated=?
                 WHERE url=?""",(int(time.time()),url))
   else:
    save(c,pid,url,data)
    c.execute("""UPDATE roz_catalog_queue SET state='done',last_error='',updated=?
                 WHERE url=?""",(int(time.time()),url))
  except HTTPError as e:
   if e.code in (429,503):
    print("RATE_LIMIT_PAUSE",e.code,url[-80:],flush=True)
    c.commit()
    time.sleep(180)
    continue
   c.execute("""UPDATE roz_catalog_queue SET state='error',attempts=attempts+1,
                last_error=?,updated=? WHERE url=?""",
             ("HTTP "+str(e.code),int(time.time()),url))
  except (URLError,TimeoutError,ValueError,ET.ParseError) as e:
   c.execute("""UPDATE roz_catalog_queue SET state='error',attempts=attempts+1,
                last_error=?,updated=? WHERE url=?""",
             (str(e)[:140],int(time.time()),url))
  c.commit()
  processed+=1
  if processed==1 or processed%25==0:
   print("PROGRESS",json.dumps(stats(c,pid)),flush=True)
  if max_posts and processed>=max_posts:break
  if delay:time.sleep(delay)
 return processed

def main():
 p=argparse.ArgumentParser(description="Authorized RozMusic -> public playlist all sync")
 p.add_argument("action",choices=("index","run","status"))
 p.add_argument("--max-sitemaps",type=int,default=0)
 p.add_argument("--max-posts",type=int,default=0)
 p.add_argument("--delay",type=float,default=1.2)
 a=p.parse_args()
 if a.max_sitemaps<0 or a.max_posts<0 or not 0.5<=a.delay<=30:
  p.error("Invalid import bounds: use delay 0.5 to 30 seconds")
 c,pid=database()
 try:
  if a.action in ("index","run"):
   count,added=index(c,a.delay,a.max_sitemaps)
   print("SITEMAPS_INDEXED",count,"NEW_URLS",added,flush=True)
  if a.action=="run":
   n=run(c,pid,a.delay,a.max_posts)
   print("POSTS_PROCESSED_THIS_RUN",n,flush=True)
  print("CATALOG_STATUS",json.dumps(stats(c,pid)),flush=True)
 finally:c.close()

if __name__=="__main__":main()
