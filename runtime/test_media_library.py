#!/usr/bin/env python3
"""Isolated media/playlist tests; no Telegram API calls and no media downloads."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-media-qa-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales as s
import sales_worker as worker
import media_library as lib
import gamification as points

ALL_MEDIA=("media_reports","playlist_comments","playlist_likes","playlist_members",
           "playlist_entries","playlists","media_comments","media_likes",
           "media_metadata_reward","media_items","loyalty_points","ai_usage",
           "outbox","updates","users")

class MediaQA(unittest.TestCase):
 def setUp(self):
  with s.conn() as c:
   for table in ALL_MEDIA:c.execute("DELETE FROM "+table)
   for uid in (101,102,103):
    c.execute("INSERT INTO users(id,name,created) VALUES(?,?,?)",(uid,"Tester"+str(uid),s.now()))
   c.execute("DELETE FROM media_settings")
   c.executemany("INSERT INTO media_settings(k,v) VALUES(?,?)",
                 [("field_points",2),("like_points",1),("max_media_mb",0)])
 def payloads(self,c,uid):
  return [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox WHERE chat=? ORDER BY id",(uid,))]
 def audio(self,file_id="audioId",unique="uniqueId"):
  return {"audio":{"file_id":file_id,"file_unique_id":unique,"title":"Example Title","performer":"Example Artist"}}
 def film(self,file_id="movieId",unique="movieUnique"):
  return {"video":{"file_id":file_id,"file_unique_id":unique,"file_name":"movie.mp4"}}
 def upload(self,c,uid=101,kind="music",message=None):
  lib.accept(c,uid,message or (self.audio() if kind=="music" else self.film()),kind)
  return c.execute("SELECT * FROM media_items ORDER BY id DESC LIMIT 1").fetchone()["id"]
 def publish(self,c,uid,mid):
  lib.callback(c,uid,"med:finish:"+str(mid))
  lib.callback(c,uid,"med:publish:"+str(mid))
  lib.callback(c,uid,"med:confirm:"+str(mid))
 def playlist(self,c,uid=101,kind="music",private=False,name="My Mix"):
  lib.callback(c,uid,"med:plnew:"+kind+":"+str(int(private)))
  lib.text_input(c,uid,"med:plnew:"+kind+":"+str(int(private)),{"text":name},4000)
  return c.execute("SELECT id FROM playlists ORDER BY id DESC LIMIT 1").fetchone()[0]

 def test_auto_detect_file_without_upload_button(self):
  with s.conn() as c:
   msg={"from":{"id":101,"first_name":"Tester"},"chat":{"id":101,"type":"private"},**self.audio()}
   worker.handle(c,{"update_id":41,"message":msg})
   mid=c.execute("SELECT id FROM media_items").fetchone()[0]
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"draft")
   self.assertTrue(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0].startswith("med:meta:"))
   self.assertTrue(self.payloads(c,101))

 def test_metadata_is_optional_and_rewards_only_after_rights_confirm(self):
  with s.conn() as c:
   mid=self.upload(c)
   lib.set_field(c,101,mid,0,"My track")
   lib.set_field(c,101,mid,1,"My singer")
   self.assertEqual(points.balance(c,101),0)
   lib.callback(c,101,"med:finish:"+str(mid))
   lib.callback(c,101,"med:publish:"+str(mid))
   self.assertEqual(points.balance(c,101),0)
   lib.callback(c,101,"med:confirm:"+str(mid))
   self.assertEqual(points.balance(c,101),4)
   lib.play(c,102,mid)
   sent=c.execute("SELECT method,payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()
   self.assertEqual(sent[0],"sendAudio")
   self.assertEqual(json.loads(sent[1])["audio"],"audioId")
   self.assertFalse(c.execute("SELECT 1 FROM media_items WHERE file_id LIKE '%bytes%'").fetchone())

 def test_cancelled_draft_never_gets_metadata_points(self):
  with s.conn() as c:
   mid=self.upload(c)
   lib.set_field(c,101,mid,0,"not publish")
   lib.callback(c,101,"med:cancel:"+str(mid))
   self.assertEqual(points.balance(c,101),0)
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"removed")

 def test_direct_movie_metadata_and_cover(self):
  with s.conn() as c:
   mid=self.upload(c,kind="film")
   lib.set_field(c,101,mid,1,"سریال")
   lib.set_field(c,101,mid,2,"2")
   lib.set_field(c,101,mid,3,"8")
   lib._cover(c,101,mid,{"photo":[{"file_id":"COVER_TELEGRAM_ID"}]})
   lib.callback(c,101,"med:confirm:"+str(mid))
   row=c.execute("SELECT * FROM media_items WHERE id=?",(mid,)).fetchone()
   self.assertEqual(row["media_type"],"series")
   self.assertEqual((row["season"],row["episode"]),(2,8))
   self.assertEqual(row["cover_id"],"COVER_TELEGRAM_ID")
   self.assertEqual(points.balance(c,101),8)
   lib.play(c,102,mid)
   self.assertEqual({r[0] for r in c.execute("SELECT method FROM outbox WHERE chat=102")},
                   {"sendVideo","sendPhoto"})

 def test_forwarded_document_and_telegram_voice(self):
  with s.conn() as c:
   mid=self.upload(c,kind="music",message={"document":{"file_id":"D1","file_unique_id":"DU","file_name":"x.flac"}})
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   self.assertTrue(c.execute("SELECT 1 FROM outbox WHERE method='sendDocument'").fetchone())
   voice=self.upload(c,kind="music",message={"voice":{"file_id":"VOICE1","file_unique_id":"VU"}})
   self.publish(c,101,voice)
   lib.play(c,102,voice)
   self.assertTrue(c.execute("SELECT 1 FROM outbox WHERE method='sendVoice'").fetchone())

 def test_duplicate_telegram_file_rejected_without_points(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   with self.assertRaisesRegex(ValueError,"قبلاً"):
    lib.accept(c,102,self.audio())
   self.assertEqual(c.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],1)

 def test_https_external_link_no_server_download(self):
  with s.conn() as c:
   mid=self.upload(c,message={"text":"https://example.com/audio.mp3?source=share"})
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   self.assertEqual(c.execute("SELECT telegram_method FROM media_items WHERE id=?",(mid,)).fetchone()[0],"link")
   self.assertTrue(any("https://example.com/" in x.get("text","") for x in self.payloads(c,102)))
   with self.assertRaises(ValueError):lib._url("http://127.0.0.1/admin")

 def test_direct_music_and_film_link_autodetection(self):
  with s.conn() as c:
   worker.handle(c,{"update_id":481,"message":{
    "from":{"id":101,"first_name":"LinkUser"},"chat":{"id":101,"type":"private"},
    "text":"https://example.org/music.mp3?source=channel"}})
   row=c.execute("SELECT kind,telegram_method FROM media_items WHERE owner_id=101 ORDER BY id DESC LIMIT 1").fetchone()
   self.assertEqual(tuple(row),("music","link"))
   c.execute("UPDATE users SET state='' WHERE id=101")
   worker.handle(c,{"update_id":482,"message":{
    "from":{"id":101,"first_name":"LinkUser"},"chat":{"id":101,"type":"private"},
    "text":"https://example.org/movie.mp4"}})
   row=c.execute("SELECT kind,telegram_method FROM media_items WHERE owner_id=101 ORDER BY id DESC LIMIT 1").fetchone()
   self.assertEqual(tuple(row),("film","link"))
  self.assertFalse(lib.is_media_message({"text":"https://example.org/ordinary-page"}))
  self.assertFalse(lib.is_media_message({"voice":{"file_id":"v","file_unique_id":"u"}}))

 def test_public_private_playlists_and_membership_invite(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   public=self.playlist(c,101,private=False,name="Public Mix")
   private=self.playlist(c,101,private=True,name="Secret Mix")
   lib.callback(c,101,"med:add:"+str(public)+":"+str(mid))
   lib.callback(c,101,"med:add:"+str(private)+":"+str(mid))
   lib.playlist_view(c,102,public)
   with self.assertRaisesRegex(ValueError,"خصوصی"):lib.playlist_view(c,102,private)
   token=c.execute("SELECT invite_token FROM playlists WHERE id=?",(private,)).fetchone()[0]
   self.assertTrue(lib.invite_start(c,102,"/start mpl_"+token))
   lib.playlist_view(c,102,private)
   with self.assertRaises(ValueError):lib.callback(c,102,"med:add:"+str(private)+":"+str(mid))

 def test_search_public_and_private_playlist_does_not_leak(self):
  with s.conn() as c:
   mid=self.upload(c)
   lib.set_field(c,101,mid,1,"آرتیست خاص")
   self.publish(c,101,mid)
   self.playlist(c,101,private=True,name="مخفی باران")
   lib.search(c,102,"music","باران")
   p=self.payloads(c,102)[-1]
   self.assertNotIn("مخفی باران",json.dumps(p,ensure_ascii=False))
   lib.search(c,102,"music","آرتیست")
   p=self.payloads(c,102)[-1]
   self.assertIn("med:play:"+str(mid),json.dumps(p))

 def test_like_self_farming_prohibited_and_retoggle_no_double_points(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   with self.assertRaisesRegex(ValueError,"خودتان"):
    lib._like(c,101,"media",mid)
   self.assertTrue(lib._like(c,102,"media",mid))
   self.assertEqual(points.balance(c,101),1)
   self.assertFalse(lib._like(c,102,"media",mid))
   self.assertTrue(lib._like(c,102,"media",mid))
   self.assertEqual(points.balance(c,101),1)

 def test_comments_rate_limit(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   lib._comment(c,102,"media",mid,"نظرم این است عالی")
   with self.assertRaisesRegex(ValueError,"یک دقیقه"):
    lib._comment(c,102,"media",mid,"دومین نظر")
   lib.comments(c,103,"media",mid)
   self.assertIn("نظرم",self.payloads(c,103)[-1]["text"])

 def test_report_and_owner_removal(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   lib.callback(c,102,"med:report:"+str(mid))
   lib.text_input(c,102,"med:report:"+str(mid),{"text":"بدون اجازه صاحب اثر"},123)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM media_reports").fetchone()[0],1)
   with self.assertRaises(ValueError):lib.callback(c,102,"med:remove:"+str(mid))
   lib.callback(c,101,"med:remove:"+str(mid))
   with self.assertRaises(ValueError):lib.play(c,103,mid)

 def test_no_arbitrary_documents_accepted(self):
  with s.conn() as c:
   self.assertFalse(lib.is_media_message({"document":{"file_id":"x","file_unique_id":"x","file_name":"malware.exe","mime_type":"application/octet-stream"}}))

 def test_film_forward_auto_detects_without_button(self):
  with s.conn() as c:
   worker.handle(c,{"update_id":9907,"message":{"chat":{"id":102,"type":"private"},
      "from":{"id":102,"first_name":"movie fan"},
      "forward_origin":{"type":"channel"},"video":{"file_id":"telegramVideo","file_unique_id":"fwdMovie"}}})
   row=c.execute("SELECT * FROM media_items WHERE owner_id=102").fetchone()
   self.assertEqual((row["kind"],row["telegram_method"],row["status"]),("film","sendVideo","draft"))

 def test_metadata_daily_cap_is_enforced(self):
  with s.conn() as c:
   for i in range(17):
    mid=self.upload(c,message=self.audio("audio"+str(i),"newUnique"+str(i)))
    lib.set_field(c,101,mid,0,"عنوان جدید "+str(i))
    self.publish(c,101,mid)
   self.assertEqual(points.balance(c,101),30)

 def test_admin_report_review_and_takedown(self):
  with s.conn() as c:
   s.put(c,"admins",[103])
   mid=self.upload(c)
   self.publish(c,101,mid)
   lib.text_input(c,102,"med:report:"+str(mid),{"text":"ادعای حق نشر"},25000)
   rid=c.execute("SELECT id FROM media_reports").fetchone()[0]
   with self.assertRaises(ValueError):lib.admin_action(c,102,"medadmin:report:"+str(rid))
   lib.admin_action(c,103,"medadmin:report:"+str(rid))
   lib.admin_action(c,103,"medadmin:remove:"+str(rid))
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"removed")
   self.assertEqual(c.execute("SELECT status FROM media_reports WHERE id=?",(rid,)).fetchone()[0],"removed")
   with self.assertRaises(ValueError):lib.play(c,102,mid)

 def test_web_moderator_can_set_points_and_resolve_report(self):
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
   lib.text_input(c,102,"med:report:"+str(mid),{"text":"پرونده بررسی"},25001)
  s.admin({"action":"media_settings","values":{"field_points":4,"like_points":2}})
  data=s.admin({"action":"get"})
  self.assertEqual((data["media"]["field_points"],data["media"]["like_points"]),(4,2))
  self.assertEqual(len(data["media"]["reports"]),1)
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"field_points":999}})
  report_id=data["media"]["reports"][0]["id"]
  s.admin({"action":"media_report","id":report_id,"decision":"dismiss"})
  self.assertEqual(s.admin({"action":"get"})["media"]["reports"],[])

if __name__=="__main__":
 unittest.main()
