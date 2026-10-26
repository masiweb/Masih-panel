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
                 [("field_points",2),("like_points",1),("max_media_mb",0),
                  ("broadcast_music",0),("broadcast_movie",0),("broadcast_series",0)])
 def payloads(self,c,uid):
  return [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox WHERE chat=? ORDER BY id",(uid,))]
 def audio(self,file_id="audioId",unique="uniqueId"):
  return {"audio":{"file_id":file_id,"file_unique_id":unique,"title":"Example Title","performer":"Example Artist"}}
 def film(self,file_id="movieId",unique="movieUnique"):
  return {"video":{"file_id":file_id,"file_unique_id":unique,"file_name":"movie.mp4"}}
 def upload(self,c,uid=101,kind="music",message=None):
  lib.accept(c,uid,message or (self.audio() if kind=="music" else self.film()),kind)
  mid=c.execute("SELECT * FROM media_items ORDER BY id DESC LIMIT 1").fetchone()["id"]
  if kind=="film":
   lib.callback(c,uid,"med:type:"+str(mid)+":movie")
  return mid
 def publish(self,c,uid,mid):
  lib.callback(c,uid,"med:finish:"+str(mid))
  lib.callback(c,uid,"med:publish:"+str(mid))
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

 def test_metadata_is_optional_and_rewards_on_direct_publication(self):
  with s.conn() as c:
   mid=self.upload(c)
   lib.set_field(c,101,mid,0,"My track")
   lib.set_field(c,101,mid,1,"My singer")
   self.assertEqual(points.balance(c,101),0)
   lib.callback(c,101,"med:finish:"+str(mid))
   lib.callback(c,101,"med:publish:"+str(mid))
   self.assertEqual(points.balance(c,101),14)  # 4 metadata + 10 published music
   row=c.execute("SELECT status,rights_ok FROM media_items WHERE id=?",(mid,)).fetchone()
   self.assertEqual(tuple(row),("published",0))
   with self.assertRaises(ValueError):
    lib.callback(c,101,"med:confirm:"+str(mid))
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
   row=c.execute("SELECT * FROM media_items WHERE id=?",(mid,)).fetchone()
   self.assertEqual(row["status"],"published")
   self.assertEqual(row["media_type"],"series")
   self.assertEqual((row["season"],row["episode"]),(2,8))
   self.assertEqual(row["cover_id"],"COVER_TELEGRAM_ID")
   self.assertEqual(points.balance(c,101),23)  # 8 metadata + 15 published episode
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

 def test_audio_formats_detected_as_telegram_documents(self):
  for ext in sorted(lib.AUDIO_EXTENSIONS):
   with self.subTest(ext=ext):
    message={"document":{"file_id":"file-"+ext,"file_unique_id":"u-"+ext,
          "file_name":"آهنگ.Sample."+ext.upper(),"mime_type":"application/octet-stream"}}
    self.assertTrue(lib.is_media_message(message))
    kind,method,_=lib._guess(message)
    self.assertEqual((kind,method),("music","sendDocument"))
    self.assertEqual(lib._link_kind("https://example.com/music."+ext.upper()+"?q=1"),"music")

 def test_video_formats_detected_as_telegram_documents(self):
  for ext in sorted(lib.VIDEO_EXTENSIONS):
   with self.subTest(ext=ext):
    message={"document":{"file_id":"video-"+ext,"file_unique_id":"v-"+ext,
          "file_name":"movie.S02.E03."+ext.upper(),"mime_type":"application/octet-stream"}}
    self.assertTrue(lib.is_media_message(message))
    self.assertEqual(lib._guess(message)[:2],("film","sendDocument"))
    self.assertEqual(lib._link_kind("https://example.com/film."+ext),"film")

 def test_document_audio_video_reach_correct_bot_flow_without_upload_button(self):
  with s.conn() as c:
   for uid,filename,kind in [(101,"Test Song.OPUS","music"),(102,"Episode.01.MKV","film")]:
    worker.handle(c,{"update_id":8500+uid,"message":{
      "from":{"id":uid,"first_name":"Guest"},"chat":{"id":uid,"type":"private"},
      "document":{"file_id":"f"+str(uid),"file_unique_id":"u"+str(uid),
                  "file_name":filename,"mime_type":"application/octet-stream"}}})
    row=c.execute("SELECT id,kind,status FROM media_items WHERE owner_id=?",(uid,)).fetchone()
    self.assertEqual((row["kind"],row["status"]),(kind,"draft"))
    current_state=c.execute("SELECT state FROM users WHERE id=?",(uid,)).fetchone()[0]
    self.assertTrue(current_state.startswith("med:meta:") if kind=="music" else current_state=="med:type:"+str(row["id"]))
    if kind=="film":lib.callback(c,uid,"med:type:"+str(row["id"])+":movie")
    lib.callback(c,uid,"med:finish:"+str(row["id"]))
    lib.callback(c,uid,"med:publish:"+str(row["id"]))
    self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(row["id"],)).fetchone()[0],"published")
    self.assertFalse(any("حق انتشار" in p.get("text","") for p in self.payloads(c,uid)))

 def test_mime_only_and_conflicts_are_safe(self):
  for mime,kind in [("audio/mpeg","music"),("audio/x-flac","music"),
                    ("application/ogg","music"),("video/mp4","film"),
                    ("video/x-matroska","film"),("application/x-matroska","film")]:
   with self.subTest(mime=mime):
    m={"document":{"file_id":"fid","file_unique_id":"unique",
       "mime_type":mime}}
    self.assertEqual(lib._guess(m)[0],kind)
  self.assertFalse(lib.is_media_message({"document":{
      "file_id":"fake","file_unique_id":"f","file_name":"malware.exe","mime_type":"audio/mpeg"}}))
  self.assertFalse(lib.is_media_message({"document":{
      "file_id":"fake","file_unique_id":"f","file_name":"track.mp3","mime_type":"video/mp4"}}))
  with self.assertRaises(ValueError):
   lib._guess({"document":{"file_id":"x","file_unique_id":"y","file_name":"music.flac"}},expected="film")

 def test_no_permission_stage_and_short_messages(self):
  with s.conn() as c:
   mid=self.upload(c)
   prompts=self.payloads(c,101)
   self.assertTrue(prompts)
   self.assertLess(len(prompts[-1]["text"]),95)
   lib.callback(c,101,"med:finish:"+str(mid))
   prompt=self.payloads(c,101)[-1]
   self.assertLess(len(prompt["text"]),95)
   self.assertNotIn("med:confirm",str(prompt))
   lib.callback(c,101,"med:publish:"+str(mid))
   post=self.payloads(c,101)[-1]
   self.assertIn("✅",post["text"])
   self.assertLess(len(post["text"]),70)
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"published")
   self.assertFalse(any("حق انتشار" in p.get("text","") for p in self.payloads(c,101)))

 def test_old_rights_state_and_confirm_button_remain_compatible(self):
  with s.conn() as c:
   mid=self.upload(c)
   c.execute("UPDATE users SET state=? WHERE id=101",("med:rights:"+str(mid),))
   lib.text_input(c,101,"med:rights:"+str(mid),{"text":"hello"},471)
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"published")
   mid2=self.upload(c,message=self.audio("newfile","new-unique"))
   lib.callback(c,101,"med:confirm:"+str(mid2))
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid2,)).fetchone()[0],"published")

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
   before=points.balance(c,101)
   self.assertTrue(lib._like(c,102,"media",mid))
   self.assertEqual(points.balance(c,101),before+1)
   self.assertFalse(lib._like(c,102,"media",mid))
   self.assertTrue(lib._like(c,102,"media",mid))
   self.assertEqual(points.balance(c,101),before+1)

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
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"published")
   lib.callback(c,101,"med:removeok:"+str(mid))
   with self.assertRaises(ValueError):lib.play(c,103,mid)

 def test_playlist_delete_requires_second_confirmation(self):
  with s.conn() as c:
   pid=self.playlist(c,uid=101,kind="film",private=False,name="Archived movie list")
   lib.callback(c,101,"med:plremove:"+str(pid))
   self.assertEqual(c.execute("SELECT removed FROM playlists WHERE id=?",(pid,)).fetchone()[0],0)
   with self.assertRaises(ValueError):lib.callback(c,102,"med:plremoveok:"+str(pid))
   lib.callback(c,101,"med:plremoveok:"+str(pid))
   self.assertEqual(c.execute("SELECT removed FROM playlists WHERE id=?",(pid,)).fetchone()[0],1)

 def test_play_queues_cover_with_details_before_music_and_buttons(self):
  with s.conn() as c:
   mid=self.upload(c)
   c.execute("UPDATE media_items SET cover_id=?,genre=?,lyrics=? WHERE id=?",
             ("COVER_FILE_ID","پاپ","بخشی از متن آهنگ",mid))
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   rows=c.execute("SELECT method,payload FROM outbox WHERE chat=? ORDER BY id",(102,)).fetchall()
   self.assertEqual([r["method"] for r in rows],["sendPhoto","sendAudio"])
   cover,audio=[json.loads(r["payload"]) for r in rows]
   self.assertEqual(cover["photo"],"COVER_FILE_ID")
   self.assertIn("متن آهنگ",cover["caption"])
   self.assertIn("پاپ",cover["caption"])
   self.assertNotIn("reply_markup",cover)
   self.assertEqual(audio["audio"],"audioId")
   self.assertNotIn("متن آهنگ",audio["caption"])
   buttons={b["callback_data"] for line in audio["reply_markup"]["inline_keyboard"] for b in line}
   self.assertTrue({"med:like:"+str(mid),"med:comments:"+str(mid),
                    "med:addmenu:"+str(mid),"med:report:"+str(mid)}<=buttons)

 def test_play_without_cover_is_info_message_then_movie_with_buttons(self):
  with s.conn() as c:
   mid=self.upload(c,kind="film")
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   rows=c.execute("SELECT method,payload FROM outbox WHERE chat=? ORDER BY id",(102,)).fetchall()
   self.assertEqual([r["method"] for r in rows],["sendMessage","sendVideo"])
   info,video=[json.loads(r["payload"]) for r in rows]
   self.assertIn("سینمایی",info["text"])
   self.assertNotIn("reply_markup",info)
   self.assertEqual(video["video"],"movieId")
   self.assertIn("reply_markup",video)

 def test_play_link_info_and_second_link_message_with_buttons(self):
  with s.conn() as c:
   mid=self.upload(c,message={"text":"https://example.com/audio.mp3"})
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   rows=c.execute("SELECT method,payload FROM outbox WHERE chat=? ORDER BY id",(102,)).fetchall()
   self.assertEqual([r["method"] for r in rows],["sendMessage","sendMessage"])
   first,second=[json.loads(r["payload"]) for r in rows]
   self.assertNotIn("reply_markup",first)
   self.assertIn("https://example.com/audio.mp3",second["text"])
   self.assertIn("reply_markup",second)

 def test_video_note_actions_are_on_video_not_extra_caption_message(self):
  with s.conn() as c:
   mid=self.upload(c,kind="film",message={"video_note":{
    "file_id":"note_id","file_unique_id":"note_unique"}})
   self.publish(c,101,mid)
   lib.play(c,102,mid)
   rows=c.execute("SELECT method,payload FROM outbox WHERE chat=? ORDER BY id",(102,)).fetchall()
   self.assertEqual([r["method"] for r in rows],["sendMessage","sendVideoNote"])
   note=json.loads(rows[1]["payload"])
   self.assertNotIn("caption",note)
   self.assertEqual(note["video_note"],"note_id")
   self.assertIn("reply_markup",note)

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
   metadata=c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=101 AND kind='media_metadata'").fetchone()[0]
   posted=c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=101 AND kind='social_post'").fetchone()[0]
   self.assertEqual((metadata,posted),(30,50))  # Separate 30pt metadata + 50pt daily posting limits

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

 def test_video_type_required_even_if_publish_pressed(self):
  with s.conn() as c:
   lib.accept(c,101,self.film("new_vid","new_vid_unique"),"film")
   mid=c.execute("SELECT id FROM media_items WHERE unique_id='new_vid_unique'").fetchone()[0]
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"med:type:"+str(mid))
   lib.callback(c,101,"med:publish:"+str(mid))
   self.assertEqual(c.execute("SELECT status FROM media_items WHERE id=?",(mid,)).fetchone()[0],"draft")
   with self.assertRaises(ValueError):lib.callback(c,102,"med:type:"+str(mid)+":series")
   lib.callback(c,101,"med:type:"+str(mid)+":series")
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"med:season:"+str(mid))
   with self.assertRaises(ValueError):lib.text_input(c,101,"med:season:"+str(mid),{"text":"0"},8)
   lib.text_input(c,101,"med:season:"+str(mid),{"text":"2"},9)
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"med:episode:"+str(mid))
   lib.text_input(c,101,"med:episode:"+str(mid),{"text":"10"},10)
   lib.callback(c,101,"med:finish:"+str(mid))
   lib.callback(c,101,"med:publish:"+str(mid))
   self.assertEqual(tuple(c.execute("SELECT media_type,season,episode,status FROM media_items WHERE id=?",(mid,)).fetchone()),("series",2,10,"published"))

 def test_broadcast_music_requires_explicit_switch_and_uses_telegram_file_id(self):
  with s.conn() as c:
   first=self.upload(c)
   self.publish(c,101,first)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],0)
   c.execute("UPDATE media_settings SET v=1 WHERE k='broadcast_music'")
   c.execute("UPDATE users SET blocked=1 WHERE id=103")
   second=self.upload(c,message=self.audio("realid","differentunique"))
   self.publish(c,101,second)
   rows=c.execute("SELECT dedupe,method,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:%' ORDER BY chat").fetchall()
   self.assertEqual(len(rows),2)
   self.assertTrue(all(r["method"]=="sendAudio" for r in rows))
   for r in rows:
    payload=json.loads(r["payload"])
    self.assertEqual(payload["audio"],"realid")
    self.assertIn("med:play:"+str(second),str(payload["reply_markup"]))
   self.assertEqual(lib.broadcast_new_media(c,second),0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],2)

 def test_film_and_series_broadcast_switches_are_independent(self):
  with s.conn() as c:
   c.execute("UPDATE media_settings SET v=1 WHERE k IN ('broadcast_series')")
   movie=self.upload(c,kind="film",message=self.film("filmId","movie1"))
   self.publish(c,101,movie)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],0)
   lib.accept(c,101,self.film("seriesFile","series1"),"film")
   series=c.execute("SELECT id FROM media_items WHERE unique_id='series1'").fetchone()[0]
   lib.callback(c,101,"med:type:"+str(series)+":series")
   lib.text_input(c,101,"med:season:"+str(series),{"text":"4"},35)
   lib.text_input(c,101,"med:episode:"+str(series),{"text":"3"},36)
   self.publish(c,101,series)
   rows=c.execute("SELECT method,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchall()
   self.assertEqual(len(rows),3)
   self.assertTrue(all(r["method"]=="sendMessage" for r in rows))
   self.assertIn("فصل 4 قسمت 3",json.loads(rows[0]["payload"])["text"])
   c.execute("UPDATE media_settings SET v=1 WHERE k='broadcast_movie'")
   movie2=self.upload(c,kind="film",message=self.film("filmId2","movie2"))
   self.publish(c,101,movie2)
   kinds=[r[0] for r in c.execute("SELECT dedupe FROM outbox WHERE dedupe LIKE 'media-broadcast:%'")]
   self.assertEqual(sum(x.startswith('media-broadcast:movie:') for x in kinds),3)
   self.assertEqual(sum(x.startswith('media-broadcast:series:') for x in kinds),3)

 def test_broadcast_does_not_publish_links_and_skips_drafts(self):
  with s.conn() as c:
   c.execute("UPDATE media_settings SET v=1 WHERE k='broadcast_music'")
   mid=self.upload(c,message={"text":"https://example.com/track.mp3"})
   self.assertEqual(lib.broadcast_new_media(c,mid),0)
   self.publish(c,101,mid)
   self.assertEqual(lib.broadcast_new_media(c,mid),0)
   self.assertFalse(c.execute("SELECT 1 FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone())

 def test_admin_broadcast_switches_persist_and_cancel_only_unsent_matching_queue(self):
  s.admin({"action":"media_settings","values":{
   "broadcast_music":True,"broadcast_movie":False,"broadcast_series":True}})
  view=s.admin({"action":"get"})["media"]
  self.assertTrue(view["broadcast_music"])
  self.assertFalse(view["broadcast_movie"])
  self.assertTrue(view["broadcast_series"])
  with s.conn() as c:
   mid=self.upload(c)
   self.publish(c,101,mid)
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"broadcast_music":1}})
  s.admin({"action":"media_settings","values":{"broadcast_music":False}})
  with s.conn() as c:
   cancelled=c.execute("SELECT done,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:music:%'").fetchall()
   self.assertEqual(len(cancelled),3)
   self.assertTrue(all(r["done"]==2 and r["payload"]=='{}' for r in cancelled))
   other=c.execute("SELECT COUNT(*) FROM outbox WHERE done=0 AND dedupe NOT LIKE 'media-broadcast:%'").fetchone()[0]
   self.assertGreaterEqual(other,1)
  self.assertFalse(s.admin({"action":"get"})["media"]["broadcast_music"])

if __name__=="__main__":
 unittest.main()