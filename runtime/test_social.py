#!/usr/bin/env python3
"""Isolated opt-in Masiha Social privacy and chat tests; no live Telegram calls."""
import json, os, sys, tempfile, unittest
from pathlib import Path

os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-social-qa-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales as s
import social
import sales_worker as worker

class SocialQA(unittest.TestCase):
 def setUp(self):
  with s.conn() as c:
   c.executemany("UPDATE media_settings SET v=? WHERE k=?",
     [(0,"social_chat_cost_"+key) for key in social.CHAT_COST_LABELS])
   for name in ("social_reports","social_geo","social_blocks","social_chat_messages",
                 "social_chat_pairs","social_chat_queue","social_chat_invites",
                "social_chat_quotes","social_discovery","social_profile_comments",
                "social_reactions","social_profile_views","social_follows","social_profiles",
                "playlist_likes","playlists","media_likes","media_items","loyalty_points",
                "outbox","users","settings"):
    c.execute("DELETE FROM "+name)
   for u in (970012341,970012342,970012343):
    c.execute("INSERT INTO users(id,name,created) VALUES(?,?,?)",
              (u,"Legacy User",s.now()))
 def payloads(self,c,uid):
  return [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox WHERE chat=? ORDER BY id",(uid,))]
 def ready(self,c,uid):
  p=social.profile(c,uid)
  c.execute("UPDATE social_profiles SET display_name=?,gender='زن',age=23,province='تهران',interests=? WHERE user_id=?",
   ("سارا" if uid==970012341 else "نیلوفر",json.dumps(["🎵 موسیقی"]),uid))
  return social.profile(c,uid)
 def test_public_code_random_and_immutable_after_profile_edit(self):
  with s.conn() as c:
   p=social.profile(c,970012341)
   self.assertRegex(p["code"],r"^MS-[0-9A-F]{8}$")
   self.assertNotIn("970012341",p["code"])
   code=p["code"]
   social.input_text(c,970012341,"soc:edit:name",{"text":"آزاده"},22)
   self.assertEqual(social.profile(c,970012341)["code"],code)
   self.assertEqual(social.by_code(c,code),970012341)
   self.assertEqual(len(social.INTERESTS),10)
   self.assertEqual(len(social.PROVINCES),31)
 def test_five_max_interests_and_changeable(self):
  with s.conn() as c:
   p=social.profile(c,970012341)
   for i in range(5):social.callback(c,970012341,"soc:interest:"+str(i))
   with self.assertRaisesRegex(ValueError,"حداکثر"):social.callback(c,970012341,"soc:interest:5")
   social.callback(c,970012341,"soc:interest:0")
   social.callback(c,970012341,"soc:interest:5")
   self.assertEqual(len(json.loads(social.profile(c,970012341)["interests"])),5)
 def test_five_photos_mandatory_photo_fileid_only(self):
  with s.conn() as c:
   social.profile(c,970012341)
   for i in range(5):
    social.input_text(c,970012341,"soc:photo",{"photo":[{"file_id":"telegram_photo_file_"+str(i)}]},3+i)
   self.assertEqual(len(json.loads(social.profile(c,970012341)["photos"])),5)
   with self.assertRaises(ValueError):
    social.input_text(c,970012341,"soc:photo",{"photo":[{"file_id":"telegram_photo_new"}]},5)
 def test_public_only_displaynames_never_internal_telegram_ids(self):
  with s.conn() as c:
   self.ready(c,970012341);other=self.ready(c,970012342)
   social.public_card(c,970012341,970012342)
   output=json.dumps(self.payloads(c,970012341),ensure_ascii=False)
   self.assertNotIn("970012342",output)
   self.assertIn("نیلوفر",output)
   self.assertIn(other["code"],output)
   social.callback(c,970012341,"soc:follow:"+other["code"])
   social.callback(c,970012341,"soc:vote:like:"+other["code"])
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_follows").fetchone()[0],1)
   self.assertEqual(c.execute("SELECT SUM(vote) FROM social_reactions WHERE kind='profile'").fetchone()[0],1)
   social.callback(c,970012341,"soc:vote:dislike:"+other["code"])
   self.assertEqual(c.execute("SELECT SUM(vote) FROM social_reactions WHERE kind='profile'").fetchone()[0],-1)
   other_latest=json.dumps(self.payloads(c,970012341),ensure_ascii=False)
   self.assertNotIn("970012342",other_latest)
 def test_private_profile_and_views_consent(self):
  with s.conn() as c:
   a=self.ready(c,970012341);b=self.ready(c,970012342)
   social.callback(c,970012342,"soc:visibility:hidden")
   with self.assertRaises(ValueError):social.public_card(c,970012341,970012342)
   self.assertFalse(c.execute("SELECT 1 FROM social_profile_views").fetchone())
   social.callback(c,970012342,"soc:visibility:public")
   social.public_card(c,970012341,970012342)
   social.callback(c,970012341,"soc:toggle:show_visitors")
   social._visitors(c,970012342)
   output=self.payloads(c,970012342)[-1]["text"]
   self.assertIn("ناشناس",output)
   self.assertNotIn("970012341",output)
 def test_media_and_playlist_dislikes_transition_from_like(self):
  with s.conn() as c:
   c.execute("INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,title,status,created) VALUES(?,?,?,?,?,?,?,?)",
             (970012341,"music","sendAudio","TG_FILE","UNIQUE","New Song","published",s.now()))
   mid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
   vote=social.reaction(c,970012342,"music",mid,1)
   self.assertEqual(vote[0],1)
   self.assertTrue(c.execute("SELECT 1 FROM media_likes WHERE media_id=?",(mid,)).fetchone())
   vote=social.reaction(c,970012342,"music",mid,-1)
   self.assertEqual(vote[0],-1)
   self.assertFalse(c.execute("SELECT 1 FROM media_likes WHERE media_id=?",(mid,)).fetchone())
   with self.assertRaises(ValueError):social.reaction(c,970012341,"music",mid,-1)
 def test_matching_random_and_filtered_without_telegram_id_exposure(self):
  with s.conn() as c:
   self.ready(c,970012341);self.ready(c,970012342)
   social.start_matching(c,970012341,"random")
   social.start_matching(c,970012342,"random")
   chat=c.execute("SELECT * FROM social_chat_pairs").fetchone()
   self.assertEqual(chat["closed"],0)
   first=self.payloads(c,970012341)[-1]["text"]
   self.assertNotIn("970012342",first)
   self.assertIn("مدیران مجاز",first)
   social._chat_message(c,970012341,{"text":"سلام"},chat["id"])
   inbox=self.payloads(c,970012342)[-1]["text"]
   self.assertIn("سلام",inbox)
   self.assertNotIn("970012341",inbox)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_chat_messages").fetchone()[0],1)
   social.end_chat(c,970012341)
   self.assertGreater(c.execute("SELECT closed FROM social_chat_pairs").fetchone()[0],0)
 def test_private_chat_request_requires_consent_and_is_free_until_accepted(self):
  with s.conn() as c:
   a=self.ready(c,970012341);b=self.ready(c,970012342)
   social.callback(c,970012341,"soc:friendchat:"+b["code"])
   quote=c.execute("SELECT * FROM social_chat_quotes").fetchone()
   self.assertEqual(quote["cost"],0)
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_invites").fetchone())
   social.callback(c,970012341,"soc:chatconfirm:"+quote["token"])
   self.assertEqual(c.execute("SELECT status FROM social_chat_invites").fetchone()[0],"pending")
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_pairs").fetchone())
   social.callback(c,970012342,"soc:acceptfriend:"+a["code"])
   row=c.execute("SELECT type,closed FROM social_chat_pairs").fetchone()
   self.assertEqual(tuple(row),("direct",0))
   self.assertNotIn("970012341",json.dumps(self.payloads(c,970012342),ensure_ascii=False))
 def fund(self,c,uid,amount=100):
  from gamification import award
  award(c,uid,amount,"test","test:fund:"+str(uid),s.now())

 def test_priced_random_only_charges_after_successful_match_and_no_per_message_charge(self):
  with s.conn() as c:
   for uid in (970012341,970012342):self.ready(c,uid);self.fund(c,uid)
   c.execute("UPDATE media_settings SET v=12 WHERE k='social_chat_cost_random'")
   before=[social._balance(c,uid) for uid in (970012341,970012342)]
   social.callback(c,970012341,"soc:random")
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_queue").fetchone())
   self.assertEqual(social._balance(c,970012341),100)
   social.callback(c,970012341,"soc:match:random")
   self.assertEqual(social._balance(c,970012341),100)
   self.assertEqual(c.execute("SELECT points FROM social_chat_queue WHERE user_id=970012341").fetchone()[0],12)
   social.callback(c,970012342,"soc:match:random")
   chat=c.execute("SELECT id FROM social_chat_pairs").fetchone()[0]
   self.assertEqual((social._balance(c,970012341),social._balance(c,970012342)),(88,88))
   social._chat_message(c,970012341,{"text":"سلام"},chat)
   social._chat_message(c,970012342,{"text":"درود"},chat)
   self.assertEqual((social._balance(c,970012341),social._balance(c,970012342)),(88,88))
   social._charge_once(c,970012341,12,chat)
   self.assertEqual(social._balance(c,970012341),88)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM loyalty_points WHERE kind='social_chat'").fetchone()[0],2)

 def test_unmatched_wait_cancel_and_insufficient_balance_cost_nothing(self):
  with s.conn() as c:
   self.ready(c,970012341);self.ready(c,970012342)
   self.fund(c,970012341,10)
   c.execute("UPDATE media_settings SET v=20 WHERE k='social_chat_cost_filtered'")
   with self.assertRaisesRegex(ValueError,"امتیاز کافی"):
    social.start_matching(c,970012341,"filtered")
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_pairs").fetchone())
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_queue").fetchone())
   c.execute("UPDATE media_settings SET v=4 WHERE k='social_chat_cost_filtered'")
   social.start_matching(c,970012341,"filtered")
   self.assertEqual(social._balance(c,970012341),10)
   social.end_chat(c,970012341)
   self.assertEqual(social._balance(c,970012341),10)
   self.assertFalse(c.execute("SELECT 1 FROM social_chat_queue").fetchone())
   self.assertFalse(c.execute("SELECT 1 FROM loyalty_points WHERE kind='social_chat'").fetchone())

 def test_request_reject_not_charged_and_accept_exact_once_with_original_quote(self):
  with s.conn() as c:
   a=self.ready(c,970012341);b=self.ready(c,970012342)
   self.fund(c,970012341,50)
   c.execute("UPDATE media_settings SET v=8 WHERE k='social_chat_cost_search'")
   social._discover(c,970012341,970012342,"search")
   social.callback(c,970012341,"soc:friendchat:"+b["code"])
   quote=c.execute("SELECT token,cost,route FROM social_chat_quotes").fetchone()
   self.assertEqual((quote["cost"],quote["route"]),(8,"search"))
   social.callback(c,970012341,"soc:chatconfirm:"+quote["token"])
   self.assertEqual(social._balance(c,970012341),50)
   social.callback(c,970012342,"soc:rejectfriend:"+a["code"])
   self.assertEqual(social._balance(c,970012341),50)
   social.callback(c,970012341,"soc:friendchat:"+b["code"])
   quote=c.execute("SELECT token,cost FROM social_chat_quotes ORDER BY rowid DESC LIMIT 1").fetchone()
   social.callback(c,970012341,"soc:chatconfirm:"+quote["token"])
   c.execute("UPDATE media_settings SET v=15 WHERE k='social_chat_cost_search'")
   social.callback(c,970012342,"soc:acceptfriend:"+a["code"])
   self.assertEqual(social._balance(c,970012341),42)
   self.assertEqual(social._balance(c,970012342),0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM loyalty_points WHERE kind='social_chat'").fetchone()[0],1)
   with self.assertRaises(ValueError):
    social.callback(c,970012342,"soc:acceptfriend:"+a["code"])
   self.assertEqual(social._balance(c,970012341),42)

 def test_priced_discovery_sources_and_direct_fallback_prevent_cheaper_forgery(self):
  with s.conn() as c:
   self.ready(c,970012341);b=self.ready(c,970012342)
   self.fund(c,970012341,200)
   config={"search":6,"near":9,"following":11,"followers":13,"direct":1}
   for route,amount in config.items():
    c.execute("UPDATE media_settings SET v=? WHERE k=?", (amount,"social_chat_cost_"+route))
   for route in ("search","near","following","followers"):
    social._discover(c,970012341,970012342,route)
    social.callback(c,970012341,"soc:friendchat:"+b["code"])
    got=c.execute("SELECT route,cost FROM social_chat_quotes ORDER BY rowid DESC LIMIT 1").fetchone()
    self.assertEqual(tuple(got),(route,config[route]))
   c.execute("UPDATE social_discovery SET created=0")
   self.assertEqual(social._route_for(c,970012341,970012342),"direct")
   social.callback(c,970012341,"soc:friendchat:"+b["code"])
   got=c.execute("SELECT route,cost FROM social_chat_quotes ORDER BY rowid DESC LIMIT 1").fetchone()
   self.assertEqual(tuple(got),("direct",13))
   self.assertEqual(social._balance(c,970012341),200)

 def test_no_credits_removes_confirm_and_admin_validates_ranges(self):
  with s.conn() as c:
   self.ready(c,970012341)
   c.execute("UPDATE media_settings SET v=100 WHERE k='social_chat_cost_random'")
   social.callback(c,970012341,"soc:random")
   latest=self.payloads(c,970012341)[-1]
   self.assertNotIn("soc:match:random",json.dumps(latest))
   self.assertEqual(social._balance(c,970012341),0)
  settings=s.admin({"action":"social_admin","view":"overview"})["rewards"]
  self.assertEqual(settings["social_chat_cost_random"],100)
  s.admin({"action":"media_settings","values":{"social_chat_cost_near":8,"social_chat_cost_followers":17}})
  settings=s.admin({"action":"social_admin","view":"overview"})["rewards"]
  self.assertEqual(settings["social_chat_cost_near"],8)
  self.assertEqual(settings["social_chat_cost_followers"],17)
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"social_chat_cost_random":-1}})
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"social_chat_cost_random":50000}})

 def test_admin_can_read_chat_logs_and_manage_reports_with_role_api(self):
  with s.conn() as c:
   self.ready(c,970012341);self.ready(c,970012342)
   social.start_matching(c,970012341,"random")
   social.start_matching(c,970012342,"random")
   chat=c.execute("SELECT id FROM social_chat_pairs").fetchone()[0]
   social._chat_message(c,970012341,{"text":"سلام مدیر، متن گفتگو"},chat)
   c.execute("INSERT INTO social_reports(reporter,target,reason,created) VALUES(?,?,?,?)",
    (970012342,970012341,"پیام نامناسب",s.now()))
   report=c.execute("SELECT id FROM social_reports").fetchone()[0]
  chats=s.admin({"action":"social_admin","view":"chats"})
  self.assertEqual(len(chats),1)
  self.assertEqual({chats[0]["first_user"],chats[0]["second_user"]},{970012341,970012342})
  msgs=s.admin({"action":"social_admin","view":"messages","id":chat})
  self.assertEqual(msgs[0]["body"],"سلام مدیر، متن گفتگو")
  self.assertEqual(msgs[0]["sender"],970012341)
  with self.assertRaises(ValueError):
   s.admin({"action":"social_admin","view":"messages","id":"1 OR 1=1"})
  s.admin({"action":"social_report","id":report,"decision":"block"})
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT blocked FROM users WHERE id=970012341").fetchone()[0],1)
   self.assertGreater(c.execute("SELECT closed FROM social_chat_pairs WHERE id=?",(chat,)).fetchone()[0],0)

 def test_points_management_and_90day_chat_retention(self):
  stats=s.admin({"action":"social_admin","view":"overview"})
  self.assertEqual(stats["rewards"]["social_post_music"],10)
  self.assertEqual(stats["rewards"]["social_chat_retention_days"],90)
  s.admin({"action":"media_settings","values":{"social_like_movie":7,
   "social_post_series":25,"social_chat_retention_days":120}})
  stats=s.admin({"action":"social_admin","view":"overview"})
  self.assertEqual(stats["rewards"]["social_like_movie"],7)
  self.assertEqual(stats["rewards"]["social_post_series"],25)
  self.assertEqual(stats["rewards"]["social_chat_retention_days"],120)
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"social_chat_retention_days":4}})
  with self.assertRaises(ValueError):
   s.admin({"action":"media_settings","values":{"social_like_music":50000}})

 def test_legacy_film_without_type_supports_dislike(self):
  with s.conn() as c:
   c.execute("INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,title,status,created) VALUES(?,?,?,?,?,?,?,?)",
    (970012341,"film","sendVideo","TG_FILE","LEGACY-FILM","Classic Movie","published",s.now()))
   mid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
   result=social.reaction(c,970012342,"movie",mid,-1)
   self.assertEqual(result[0],-1)
   self.assertEqual(result[1][2],1)

 def test_public_media_comment_uses_displayname_not_internal_telegram_number(self):
  import media_library as media
  with s.conn() as c:
   self.ready(c,970012341);self.ready(c,970012342)
   c.execute("INSERT INTO media_items(owner_id,kind,telegram_method,file_id,unique_id,title,status,created) VALUES(?,?,?,?,?,?,?,?)",
    (970012341,"music","sendAudio","TG_FILE","UNIQUE","New Song","published",s.now()))
   mid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
   media._comment(c,970012342,"media",mid,"این آهنگ عالی است")
   media.comments(c,970012341,"media",mid)
   published=self.payloads(c,970012341)[-1]["text"]
   self.assertIn("نیلوفر",published)
   self.assertNotIn("970012342",published)

 def test_nearby_uses_optin_and_coarse_distance(self):
  with s.conn() as c:
   self.ready(c,970012341);self.ready(c,970012342)
   social.geo_input(c,970012341,{"location":{"latitude":35.70,"longitude":51.40}})
   social.geo_input(c,970012342,{"location":{"latitude":35.72,"longitude":51.42}})
   social.near(c,970012341)
   output=json.dumps(self.payloads(c,970012341)[-1],ensure_ascii=False)
   self.assertIn("تا حدود 5 کیلومتر",output)
   self.assertNotIn("35.72",output)
   self.assertNotIn("970012342",output)
   social.callback(c,970012342,"soc:geodelete")
   social.near(c,970012341)
   self.assertNotIn("نیلوفر",json.dumps(self.payloads(c,970012341)[-1],ensure_ascii=False))

if __name__=="__main__":unittest.main()
