"""Offline tests for licensed catalog metadata, duplicate prevention, and pagination."""
import os
import sqlite3
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
tmp=tempfile.TemporaryDirectory(prefix="masiha-rozmusic-test-")
os.environ["MASIHA_SALES_DIR"]=tmp.name
import sales
import media_library
import rozmusic_importer as imp
import roz_telegram_upload as uploader

HTML='''<html><h1 class="title"><a>دانلود آهنگ علی رضایی به نام یاد تو</a></h1>
<div class="matnmusic"><div class="text"><h3>متن آهنگ</h3>
<p>من اینجا هستم<br/>تو آنجایی</p></div></div>
<div class="dl"><a href="https://dl.rozmusic.com/Music/ali-yade-to.mp3">کیفیت 320</a></div>
</html>'''

class RozCatalogTest(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for name in ("roz_catalog_sources","roz_catalog_queue","playlist_entries",
                "playlists","media_items","outbox"):
    try:c.execute("DELETE FROM "+name)
    except Exception:pass
   c.execute("DELETE FROM users WHERE id=0")

 def test_parse_and_https_domain_filter(self):
  item=imp.parse_article(HTML)
  self.assertEqual((item["artist"],item["title"]),("علی رضایی","یاد تو"))
  self.assertIn("تو آنجایی",item["lyrics"])
  self.assertEqual(item["mp3"],"https://dl.rozmusic.com/Music/ali-yade-to.mp3")
  for bad in ("http://rozmusic.com/sitemap.xml",
              "https://rozmusic.com.evil.com/sitemap.xml",
              "https://localhost/private"):
   with self.assertRaises(ValueError):imp.trusted(bad)

 def test_queue_and_playlist_are_idempotent(self):
  c,pid=imp.database()
  try:
   src="https://rozmusic.com/test-track.html"
   with patch.object(imp,"get",side_effect=[
    "<sitemapindex><sitemap><loc>https://rozmusic.com/sitemap-pt-post-2026-10.xml</loc></sitemap></sitemapindex>",
    "<urlset><url><loc>"+src+"</loc></url></urlset>"]):
    count,added=imp.index(c,0)
   self.assertEqual((count,added),(1,1))
   item=imp.parse_article(HTML)
   imp.save(c,pid,src,item,"TELEGRAM_AUDIO_FILE_123456")
   c.execute("UPDATE roz_catalog_queue SET state='done' WHERE url=?",(src,))
   c.commit()
   imp.save(c,pid,src,item,"TELEGRAM_AUDIO_FILE_123456");c.commit()
   self.assertEqual(imp.stats(c,pid)["playlist_all"],1)
   row=c.execute("SELECT m.artist,m.title,m.lyrics,m.telegram_method,m.owner_id FROM media_items m JOIN playlist_entries e ON e.media_id=m.id WHERE e.playlist_id=?",(pid,)).fetchone()
   self.assertEqual((row["artist"],row["title"],row["telegram_method"],row["owner_id"]),
                    ("علی رضایی","یاد تو","sendAudio",0))
   self.assertIn("من اینجا هستم",row["lyrics"])
   self.assertEqual(c.execute("SELECT file_id FROM media_items WHERE id IN (SELECT media_id FROM playlist_entries WHERE playlist_id=?)",(pid,)).fetchone()[0],"TELEGRAM_AUDIO_FILE_123456")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM media_items WHERE owner_id=0 AND source_url<>''").fetchone()[0],0)

  finally:c.close()

 def test_rollback_legacy_links_only_resets_music_not_films(self):
  c=sqlite3.connect(":memory:")
  try:
   c.executescript("""
    CREATE TABLE media_items(
     id INTEGER PRIMARY KEY,owner_id INTEGER,kind TEXT,telegram_method TEXT,
     file_id TEXT,status TEXT,source_url TEXT);
    CREATE TABLE roz_catalog_sources(article_url TEXT,media_id INTEGER);
    CREATE TABLE roz_catalog_queue(
     url TEXT PRIMARY KEY,state TEXT,attempts INTEGER,last_error TEXT);
   """)
   c.execute("INSERT INTO media_items VALUES(1,0,'music','sendDocument','','published','legacy')")
   c.execute("INSERT INTO media_items VALUES(2,0,'film','sendVideo','telegram-video-id','published','')")
   c.execute("INSERT INTO media_items VALUES(3,0,'music','sendAudio','telegram-audio-id','published','')")
   c.execute("INSERT INTO roz_catalog_sources VALUES('https://rozmusic.com/old-track.html',1)")
   c.execute("INSERT INTO roz_catalog_sources VALUES('https://example.com/film.mp4',2)")
   c.execute("INSERT INTO roz_catalog_queue VALUES('https://rozmusic.com/old-track.html','done',3,'old-error')")
   c.execute("INSERT INTO roz_catalog_queue VALUES('https://example.com/film.mp4','done',4,'film-error')")
   changed=imp.rollback_legacy_links(c)
   self.assertEqual(changed,1)
   music=c.execute("SELECT status,source_url FROM media_items WHERE id=1").fetchone()
   film=c.execute("SELECT status,source_url FROM media_items WHERE id=2").fetchone()
   good_music=c.execute("SELECT status FROM media_items WHERE id=3").fetchone()
   self.assertEqual(tuple(music),("draft",""))
   self.assertEqual(tuple(film),("published",""))
   self.assertEqual(good_music[0],"published")
   music_job=c.execute("SELECT state,attempts,last_error FROM roz_catalog_queue WHERE url='https://rozmusic.com/old-track.html'").fetchone()
   film_job=c.execute("SELECT state,attempts,last_error FROM roz_catalog_queue WHERE url='https://example.com/film.mp4'").fetchone()
   self.assertEqual(tuple(music_job),("pending",0,""))
   self.assertEqual(tuple(film_job),("done",4,"film-error"))
  finally:
   c.close()


if __name__=="__main__":unittest.main()
