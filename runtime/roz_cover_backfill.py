#!/usr/bin/python3
"""Resume-safe after-catalog cover migration. Do not duplicate audio or broadcasts."""
import html
import re
import sqlite3
import time
from urllib.error import HTTPError,URLError

import rozmusic_importer as music
import roz_telegram_upload as uploader

def cover_from_page(source_html):
    """Find a trusted picture independently of MP3 availability on old pages."""
    if not isinstance(source_html,str):
        return ""
    # Original article thumbnail first, then social sharing image.
    candidate_patterns=(
        r'<img\b[^>]*?data-src=["\x27]([^"\x27]+)["\x27]',
        r'<meta\b[^>]*property=["\x27]og:image["\x27][^>]*content=["\x27]([^"\x27]+)["\x27]',
    )
    for pattern in candidate_patterns:
        for candidate in re.findall(pattern,source_html,re.I):
            try:
                return uploader.validate_cover_url(html.unescape(candidate))
            except ValueError:
                continue
    return ""

def setup(c):
 c.execute("""CREATE TABLE IF NOT EXISTS roz_cover_backfill (
  media_id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'pending', last_error TEXT NOT NULL DEFAULT '',
  updated INTEGER NOT NULL DEFAULT 0
 )""")
 c.commit()

def next_item(c):
 return c.execute("""SELECT m.id, s.article_url FROM media_items m
  JOIN roz_catalog_sources s ON s.media_id=m.id
  LEFT JOIN roz_cover_backfill b ON b.media_id=m.id
  WHERE m.owner_id=0 AND m.kind='music' AND m.status='published'
    AND m.cover_id='' AND (b.media_id IS NULL OR
        (b.state='error' AND b.attempts<3))
  ORDER BY m.id LIMIT 1""").fetchone()

def run(c,sales_settings=None,delay=0.6,max_items=0):
 import sales
 setup(c)
 if c.execute("""SELECT 1 FROM roz_catalog_queue WHERE
   state IN ('pending','processing') OR
   (state='error' AND attempts<3) LIMIT 1""").fetchone():
  return 0  # Do not compete with foreground imports.
 cfg=sales_settings or sales.settings(c)
 uploader.verified_storage_chat(cfg)
 total=0
 while True:
  item=next_item(c)
  if not item or (max_items and total>=max_items):break
  mid=item["id"]
  try:
   cover=cover_from_page(music.get(item["article_url"]))
   if not cover:
    c.execute("""INSERT INTO roz_cover_backfill(media_id,attempts,state,last_error,updated)
       VALUES(?,3,'skipped','no_cover',?) ON CONFLICT(media_id) DO UPDATE SET
       attempts=3,state='skipped',last_error='no_cover',updated=excluded.updated""",
       (mid,int(time.time())))
   else:
    fid=uploader.upload_cover(cover,cfg)
    c.execute("UPDATE media_items SET cover_id=? WHERE id=? AND cover_id=''",
              (fid,mid))
    c.execute("""INSERT INTO roz_cover_backfill(media_id,attempts,state,updated)
       VALUES(?,0,'done',?) ON CONFLICT(media_id) DO UPDATE SET
       state='done',attempts=0,last_error='',updated=excluded.updated""",
       (mid,int(time.time())))
    print("COVER_BACKFILLED",mid,flush=True)
  except (HTTPError,URLError,OSError,RuntimeError,ValueError,TimeoutError) as ex:
   if isinstance(ex,HTTPError) and ex.code in (429,503):
    # Retry later without consuming attempts. Avoid hammering a rate-limited source.
    print("COVER_RATE_LIMIT_PAUSE",ex.code,flush=True)
    c.commit()
    time.sleep(180)
    continue
   attempts=c.execute("SELECT attempts FROM roz_cover_backfill WHERE media_id=?",(mid,)).fetchone()
   count=(attempts["attempts"] if attempts else 0)+1
   c.execute("""INSERT INTO roz_cover_backfill(media_id,attempts,state,last_error,updated)
      VALUES(?,?,'error',?,?) ON CONFLICT(media_id) DO UPDATE SET
      attempts=excluded.attempts,state='error',last_error=excluded.last_error,
      updated=excluded.updated""",(mid,count,type(ex).__name__,int(time.time())))
  c.commit()
  total+=1
  if delay:time.sleep(max(delay,0.5))
 return total