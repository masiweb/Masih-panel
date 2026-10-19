"""Offline tests for licensed catalog metadata, duplicate prevention, and pagination."""
import os
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

if __name__=="__main__":unittest.main()
