"""Pure offline music search and post-import maintenance tests."""
import os,sys,tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch
os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-music-tests-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales,media_library as media,rozmusic_importer as importer,roz_cover_backfill as covers,audio_fingerprint as fp
HTML="<h1>دانلود آهنگ علی رضایی به نام یاد تو</h1><div class='matnmusic'><p>متن مهتاب</p></div><img data-src='https://rozmusic.com/wp-content/uploads/2026/10/cover.jpg'><a href='https://dl.rozmusic.com/Music/test.mp3'>mp3</a>"

class MusicFeatures(unittest.TestCase):
 def setUp(self):
  self.c,self.pid=importer.database()
  for name in ("roz_catalog_queue","roz_catalog_sources","media_items","playlist_entries","playlists","audio_fp_index","outbox","roz_cover_backfill"):
   try:self.c.execute("DELETE FROM "+name)
   except Exception:pass
  self.uid=123456
  self.c.execute("INSERT OR IGNORE INTO users(id,name,created) VALUES(?,?,0)",(self.uid,"tester"))
  self.c.commit()
 def tearDown(self):self.c.close()
 def item(self):
  item=importer.parse_article(HTML)
  mid=importer.save(self.c,self.pid,"https://rozmusic.com/post-1.html",item,"TEST_TELEGRAM_AUDIO_FILE_123456")
  self.c.execute("INSERT INTO roz_catalog_queue(url,state) VALUES(?,'done')",("https://rozmusic.com/post-1.html",))
  self.c.commit()
  return mid
 def test_menu_and_fields(self):
  mid=self.item()
  media.callback(self.c,self.uid,"med:searchmenu:music")
  msg=json.loads(self.c.execute("SELECT payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()[0])
  buttons={b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row}
  for text in ["med:searchfield:music:title","med:searchfield:music:artist","med:searchfield:music:lyrics","med:voicefind"]:
   self.assertIn(text,buttons)
  for field,term,should_match in [("title","یاد تو",True),("artist","علی رضایی",True),("lyrics","مهتاب",True),
      ("title","مهتاب",False),("artist","یاد تو",False)]:
   media.search(self.c,self.uid,"music",term,field)
   out=json.loads(self.c.execute("SELECT payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()[0])
   buttons={b["callback_data"] for row in out["reply_markup"]["inline_keyboard"] for b in row}
   self.assertEqual("med:play:"+str(mid) in buttons,should_match)
  media.callback(self.c,self.uid,"med:search:music")
  media.callback(self.c,self.uid,"med:searchfield:music:lyrics")
  media.text_input(self.c,self.uid,"med:searchfield:music:lyrics",{"text":"مهتاب"},100)
  self.assertEqual(self.c.execute("SELECT state FROM users WHERE id=?",(self.uid,)).fetchone()[0],"")
 def test_cover_recovery_when_old_audio_links_are_missing(self):
  page='<h1>آهنگ قدیمی</h1><meta property="og:image" content="https://rozmusic.com/wp-content/uploads/old-cover.jpg">'
  self.assertIsNone(importer.parse_article(page))
  self.assertEqual(covers.cover_from_page(page),
      "https://rozmusic.com/wp-content/uploads/old-cover.jpg")
  self.assertEqual(covers.cover_from_page("<html>بدون تصویر</html>"),"")
  self.assertEqual(covers.cover_from_page('<img data-src="https://evil.example.com/cover.jpg">'),"")

 def test_backfill_respects_import_and_is_idempotent(self):
  mid=self.item()
  self.c.execute("INSERT INTO roz_catalog_queue(url,state) VALUES(?,'pending')",("https://rozmusic.com/new.html",))
  self.c.commit()
  opts={"enabled":True,"token":"fake","admins":[self.uid]}
  with patch.object(covers.music,"get",return_value=HTML),patch.object(covers.uploader,"upload_cover",return_value="TG_PHOTO_ID_123456"),patch.object(covers.uploader,"verified_storage_chat",return_value=self.uid):
   self.assertEqual(covers.run(self.c,opts,delay=0,max_items=1),0)
   self.c.execute("DELETE FROM roz_catalog_queue WHERE state='pending'")
   self.c.commit()
   self.assertEqual(covers.run(self.c,opts,delay=0,max_items=1),1)
   self.assertEqual(covers.run(self.c,opts,delay=0,max_items=1),0)
  self.assertEqual(self.c.execute("SELECT cover_id FROM media_items WHERE id=?",(mid,)).fetchone()[0],"TG_PHOTO_ID_123456")
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],1)
 def test_automatic_fingerprint_tracking(self):
  link="https://rozmusic.com/another.html"
  self.c.execute("INSERT INTO roz_catalog_queue(url) VALUES(?)",(link,))
  sales.put(self.c,"enabled",True)
  sales.put(self.c,"token","test-token")
  self.c.commit()
  def transfer(*args,**kw):
   kw["fingerprint_status"].append(True)
   return ("TEST_TELEGRAM_AUDIO_FILE_123456","unique",1234,True)
  with patch.object(importer,"get",return_value=HTML),patch.object(importer.telegram_upload,"verified_storage_chat",return_value=self.uid),patch.object(importer.telegram_upload,"stream_once",side_effect=transfer),patch.object(importer.telegram_upload,"upload_cover",return_value="TG_PHOTO_ID_123456"),patch("media_library.broadcast_new_media"):
   importer.run(self.c,self.pid,delay=0,max_posts=1)
  self.assertEqual(tuple(self.c.execute("SELECT indexed,state FROM audio_fp_index").fetchone()),(1,"done"))
  ref=self.c.execute("SELECT fingerprint_id FROM audio_fp_index").fetchone()[0]
  self.assertTrue(ref.startswith("a") and len(ref)==25)
 def test_reference_backfill_waits_for_catalog(self):
  self.item()
  self.c.execute("INSERT INTO roz_catalog_queue(url,state) VALUES(?,'pending')",("https://rozmusic.com/new.html",))
  self.c.commit()
  with patch.object(fp,"available",return_value=True),patch.object(fp,"process_reference",side_effect=AssertionError):
   self.assertEqual(fp.run_once(),"waiting_for_catalog_import")
  self.c.execute("DELETE FROM roz_catalog_queue WHERE state='pending'");self.c.commit()
  with patch.object(fp,"available",return_value=True),patch.object(fp,"process_reference",return_value=None) as done:
   self.assertTrue(fp.run_once().startswith("indexed:"))
   done.assert_called_once()

if __name__=="__main__":unittest.main()