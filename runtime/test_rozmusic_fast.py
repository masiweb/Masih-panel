"""Concurrent catalog import and Telegram-hosted covers, without external calls."""
import os,tempfile,unittest,sys,sqlite3,time
from pathlib import Path
from unittest.mock import patch
os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-roz-fast-test-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales
import rozmusic_importer as imp
import roz_telegram_upload as upload

BODY="""<h1>دانلود آهنگ علی رضایی به نام یاد تو</h1>
<img data-src="https://rozmusic.com/wp-content/uploads/2026/10/Cover-300x300.jpg">
<a href="https://dl.rozmusic.com/Music/ali.mp3">320</a>"""

class FastCatalog(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ("roz_catalog_queue","roz_catalog_sources","playlist_entries","playlists","media_items"):
    try:c.execute("DELETE FROM "+table)
    except Exception:pass
 def test_cover_parsed_and_safe(self):
  row=imp.parse_article(BODY)
  self.assertTrue(row["cover"].endswith("Cover-300x300.jpg"))
  for url in ("https://evil.rozmusic.com.example.org/cover.jpg",
              "http://rozmusic.com/c.jpg","https://127.0.0.1/x.jpg"):
   with self.assertRaises(ValueError):upload.validate_cover_url(url)
 def test_parallel_atomic_and_recovery(self):
  c,pid=imp.database()
  try:
   sales.put(c,"enabled",True)
   sales.put(c,"token","valid-test-token")
   for i in range(17):
    c.execute("INSERT INTO roz_catalog_queue(url,state) VALUES(?,?)",
              ("https://rozmusic.com/track-%02d.html"%i,"processing" if i==0 else "pending"))
   c.commit()
   def fake_stream(*args,**kw):
    time.sleep(0.02)
    return ("telegram_file_"+str(time.monotonic_ns()),"unique",9000,True)
   with patch.object(imp,"get",return_value=BODY),\
        patch.object(imp.telegram_upload,"verified_storage_chat",return_value=101),\
        patch.object(imp.telegram_upload,"stream_once",side_effect=fake_stream),\
        patch.object(imp.telegram_upload,"upload_cover",return_value="telegram_cover_123456"),\
        patch("media_library.broadcast_new_media",return_value=0):
    done=imp.run_parallel(c,pid,0.5,workers=5)
   self.assertEqual(done,17)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM roz_catalog_queue WHERE state='done'").fetchone()[0],17)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM media_items WHERE cover_id='telegram_cover_123456' AND status='published'").fetchone()[0],17)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM roz_catalog_sources").fetchone()[0],17)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM roz_catalog_queue WHERE state='processing'").fetchone()[0],0)
  finally:c.close()
 def test_cover_photo_is_safely_uploaded(self):
  import io,json
  from PIL import Image
  from types import SimpleNamespace
  raw=io.BytesIO()
  Image.new("RGB",(550,450),(20,100,180)).save(raw,format="JPEG")
  class SourceResponse:
   url="https://rozmusic.com/cover.jpg"
   headers={"Content-Length":str(len(raw.getvalue()))}
   def __enter__(self):return self
   def __exit__(self,*args):return False
   def read(self,limit):return raw.getvalue()[:limit]
  class FakeConn:
   def __init__(self,*args,**kw):self.sent=b""
   def putrequest(self,*args,**kw):self.method=args
   def putheader(self,*args):pass
   def endheaders(self):pass
   def send(self,buf):self.sent+=buf
   def getresponse(self):
    content=json.dumps({"ok":True,"result":{"message_id":80,"photo":[{"file_id":"cover_file_123456"}]}}).encode()
    return SimpleNamespace(status=200,read=lambda limit:content)
   def close(self):pass
  fake=FakeConn()
  with patch.object(upload,"AUDIO_HTTP") as opener,\
       patch.object(upload.http.client,"HTTPSConnection",return_value=fake),\
       patch.object(upload,"verified_storage_chat",return_value=101),\
       patch.object(upload,"delete_storage_message",return_value=True) as deleted,\
       patch.object(upload,"storage_send_pace"):
   opener.open.return_value=SourceResponse()
   res=upload.upload_cover("https://rozmusic.com/cover.jpg",{"token":"x","enabled":True})
  self.assertEqual(res,"cover_file_123456")
  self.assertIn(b'name="photo"; filename="cover.jpg"',fake.sent)
  self.assertIn(b"Content-Type: image/jpeg",fake.sent)
  deleted.assert_called_once()
 def test_cover_failure_does_not_drop_song(self):
  c,pid=imp.database()
  try:
   sales.put(c,"enabled",True);sales.put(c,"token","valid-test-token")
   c.execute("INSERT INTO roz_catalog_queue(url) VALUES('https://rozmusic.com/fallback.html')");c.commit()
   with patch.object(imp,"get",return_value=BODY),\
        patch.object(imp.telegram_upload,"verified_storage_chat",return_value=101),\
        patch.object(imp.telegram_upload,"stream_once",return_value=("telegram_audio_123456","unique",9000,True)),\
        patch.object(imp.telegram_upload,"upload_cover",side_effect=ValueError("bad cover")),\
        patch("media_library.broadcast_new_media",return_value=0):
    imp.run(c,pid,delay=0,max_posts=1)
   r=c.execute("SELECT status,cover_id,file_id FROM media_items WHERE kind='music' ORDER BY id DESC LIMIT 1").fetchone()
   self.assertEqual((r["status"],r["cover_id"],r["file_id"]),("published","","telegram_audio_123456"))
  finally:c.close()
if __name__=="__main__":unittest.main()