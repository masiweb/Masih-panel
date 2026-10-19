"""No-network proof: Telegram file ID only, tempfile always removed."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
tmp=tempfile.TemporaryDirectory(prefix="masiha-roztg-qa-")
os.environ["MASIHA_SALES_DIR"]=tmp.name
import sales
import rozmusic_importer as imp
import roz_telegram_upload as u
import media_library as library

ARTICLE_URL="https://rozmusic.com/test-song.html"
MP3_URL="https://dl.rozmusic.com/Music/test-song.mp3"
METADATA={"title":"Test Song","artist":"Test Artist","lyrics":"Test lyrics","mp3":MP3_URL}


class UploadQA(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ("roz_catalog_sources","roz_catalog_queue","playlist_entries","playlists",
                 "media_items","outbox"):
    try:c.execute("DELETE FROM "+table)
    except Exception:pass
   c.execute("DELETE FROM users WHERE id=0")
   c.execute("DELETE FROM settings")
   sales.put(c,"enabled",True)
   sales.put(c,"token","placeholder_no_real_token")
   sales.put(c,"admins",[123456789])
  self.db,self.pid=imp.database()

 def tearDown(self):
  self.db.close()

 def test_saves_only_verified_bot_file_id(self):
  with self.assertRaisesRegex(ValueError,"verified Telegram"):
   imp.save(self.db,self.pid,ARTICLE_URL,METADATA,"")
  imp.save(self.db,self.pid,ARTICLE_URL,METADATA,"FILE_ID_VALID_12345")
  self.db.commit()
  row=self.db.execute("SELECT * FROM media_items WHERE owner_id=0").fetchone()
  self.assertEqual(row["telegram_method"],"sendAudio")
  self.assertEqual(row["file_id"],"FILE_ID_VALID_12345")
  self.assertEqual(row["source_url"],"")
  self.assertEqual(row["status"],"published")
  imp.save(self.db,self.pid,ARTICLE_URL,METADATA,"FILE_ID_VALID_12345")
  self.assertEqual(self.db.execute("SELECT COUNT(*) FROM media_items WHERE owner_id=0").fetchone()[0],1)
  self.assertEqual(self.db.execute("SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=?",(self.pid,)).fetchone()[0],1)

 def test_legacy_links_get_requeued_and_hidden(self):
  mid=self.db.execute("""INSERT INTO media_items
    (owner_id,kind,telegram_method,file_id,unique_id,title,source_url,created,status)
    VALUES(0,'music','link','','OLDTESTMUSIC','Old source',?,1,'published')""",
    (MP3_URL,)).lastrowid
  self.db.execute("INSERT INTO roz_catalog_sources VALUES(?,?,1,1)",(ARTICLE_URL,mid))
  self.db.execute("INSERT INTO roz_catalog_queue(url,state) VALUES(?,?)",(ARTICLE_URL,"done"))
  self.db.commit()
  self.assertEqual(imp.rollback_legacy_links(self.db),1)
  row=self.db.execute("SELECT status,source_url FROM media_items WHERE id=?",(mid,)).fetchone()
  self.assertEqual(tuple(row),("draft",""))
  self.assertEqual(self.db.execute("SELECT state FROM roz_catalog_queue WHERE url=?",(ARTICLE_URL,)).fetchone()[0],"pending")

 def test_mp3_download_tempfiles_always_cleaned(self):
  paths=[]
  def fake_download(url,destination):
   paths.append(Path(destination))
   Path(destination).write_bytes(b"ID3"+b"a"*8192)
   return Path(destination).stat().st_size
  def fake_upload(path,*args):
   self.assertTrue(Path(path).exists())
   return {"file_id":"FILE_ID_REAL_123456","file_unique_id":"SOME_UNIQUE_ID",
           "message_id":2}
  st={"token":"PLACEHOLDER","enabled":True,"admins":[123456789]}
  with patch.object(u,"download_mp3",side_effect=fake_download),\
       patch.object(u,"upload_to_telegram",side_effect=fake_upload),\
       patch.object(u,"delete_storage_message",return_value=True):
   fid,uid,size,deleted=u.stream_once(MP3_URL,"Artist","Title",st)
  self.assertEqual(fid,"FILE_ID_REAL_123456")
  self.assertTrue(deleted)
  self.assertFalse(paths[0].exists())
  with patch.object(u,"download_mp3",side_effect=fake_download),\
       patch.object(u,"upload_to_telegram",side_effect=RuntimeError("UPLOAD_FAIL")):
   with self.assertRaisesRegex(RuntimeError,"UPLOAD_FAIL"):
    u.stream_once(MP3_URL,"Artist","Title",st)
  self.assertFalse(paths[1].exists())

 def test_run_creates_no_public_link_and_telegram_file_is_playable(self):
  self.db.execute("INSERT INTO roz_catalog_queue(url) VALUES(?)",(ARTICLE_URL,))
  self.db.commit()
  with patch.object(imp,"get",return_value='''<h1>دانلود آهنگ Test Artist به نام Test Song</h1>
    <div class="matnmusic"><p>Some lyrics</p></div>
    <a href="https://dl.rozmusic.com/Music/test-song.mp3">mp3</a>'''),\
       patch.object(u,"stream_once",return_value=("FILE_ID_VALID_12345","TG_UNIQUE",9000,True)):
   processed=imp.run(self.db,self.pid,delay=0,max_posts=1)
  self.assertEqual(processed,1)
  row=self.db.execute("SELECT id,telegram_method,source_url,file_id,status FROM media_items WHERE owner_id=0").fetchone()
  self.assertEqual((row["telegram_method"],row["source_url"],row["file_id"],row["status"]),
                   ("sendAudio","","FILE_ID_VALID_12345","published"))
  library.play(self.db,123456789,row["id"])
  r=self.db.execute("SELECT method,payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()
  self.assertEqual(r["method"],"sendAudio")
  self.assertEqual(json.loads(r["payload"])["audio"],"FILE_ID_VALID_12345")

 def test_importer_broadcasts_uploaded_audio_once_and_never_a_source_link(self):
  with sales.conn() as c:
   c.execute("UPDATE media_settings SET v=1 WHERE k='broadcast_music'")
   c.execute("INSERT OR IGNORE INTO users(id,name,created) VALUES(?, ?, ?)",
             (456789123,"Public Listener",sales.now()))
  imp.save(self.db,self.pid,ARTICLE_URL,METADATA,"TELEGRAMFILE_123456")
  rows=self.db.execute("SELECT method,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:music:%'").fetchall()
  recipients={r["id"] for r in self.db.execute("SELECT id FROM users WHERE id>0 AND blocked=0")}
  self.assertEqual(len(rows),len(recipients))
  self.assertTrue(rows)
  for sent in rows:
   self.assertEqual(sent["method"],"sendAudio")
   payload=json.loads(sent["payload"])
   self.assertIn(payload["chat_id"],recipients)
   self.assertEqual(payload["audio"],"TELEGRAMFILE_123456")
   self.assertNotIn("http",json.dumps(payload))
  imp.save(self.db,self.pid,ARTICLE_URL,METADATA,"TELEGRAMFILE_123456")
  self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],len(recipients))

 def test_audio_url_rejects_other_servers_and_internal_ips(self):
  for url in ("http://dl.rozmusic.com/audio.mp3",
              "https://attacker.example.com/file.mp3",
              "https://rozmusic.com.attacker.net/audio.mp3",
              "https://127.0.0.1/x.mp3",
              "https://dl.rozmusic.com/track.mp4"):
   with self.assertRaises(ValueError):u.validate_mp3_url(url)


if __name__=="__main__":
 unittest.main()
