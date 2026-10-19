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
                "social_chat_quotes","social_discovery","social_admin_adjust_drafts",
                "social_admin_adjustments","social_profile_comments",
                "social_reactions","social_profile_views","social_follows","social_profiles",
                "playlist_likes","playlists","media_likes","media_items","loyalty_points",
                "outbox","users","settings","audit","ledger"):
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
 def test_four_admin_adjustments_send_notices_and_record_exact_balances(self):
  with s.conn() as c:
   p=self.ready(c,970012341)
   from gamification import award,balance
   award(c,970012341,60,"test","test:f1",s.now())
   s.money(c,970012341,200000,"test","test:w1")
   targets=[("points_add",25,60,85),("points_sub",15,85,70),
            ("wallet_add",50000,200000,250000),("wallet_sub",12000,250000,238000)]
   for i,(op,amount,before,after) in enumerate(targets):
    request="test-event-unique-"+str(i)+"-AAAAAAA"
    result=social.adjust_account(c,p["code"],op,amount,request,note="تغییر با تأیید مدیر")
    self.assertTrue(result["applied"])
    self.assertEqual((result["before"],result["after"]),(before,after))
    latest=c.execute("SELECT payload FROM outbox WHERE dedupe=?",("social:admin-adjust-notice:"+request,)).fetchone()
    self.assertIsNotNone(latest)
    notification=json.loads(latest[0])
    self.assertIn(social.ADMIN_OPERATIONS[op][0],notification["text"])
    self.assertIn(f"{amount:,}",notification["text"])
    self.assertIn("تغییر با تأیید مدیر",notification["text"])
    self.assertNotIn(str(970012341),notification["text"])
   self.assertEqual(balance(c,970012341),70)
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=970012341").fetchone()[0],238000)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_admin_adjustments").fetchone()[0],4)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action LIKE 'social_admin_%'").fetchone()[0],4)
   log=social.admin_view(c,{"view":"adjustments","code":p["code"]})
   self.assertEqual(len(log),4)
   self.assertEqual(log[0]["operation"],"wallet_sub")

 def test_adjustment_replay_never_charges_twice_or_notifies_twice(self):
  with s.conn() as c:
   p=self.ready(c,970012341)
   ref="abc0123456789-unique-test"
   a=social.adjust_account(c,p["code"],"points_add",30,ref)
   b=social.adjust_account(c,p["code"],"points_add",30,ref)
   self.assertTrue(a["applied"])
   self.assertFalse(b["applied"])
   self.assertEqual(social._adjust_info(c,970012341)["points"],30)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe=?",("social:admin-adjust-notice:"+ref,)).fetchone()[0],1)
   with self.assertRaises(ValueError):
    social.adjust_account(c,p["code"],"points_add",999,ref)
   with self.assertRaises(ValueError):
    social.adjust_account(c,p["code"],"wallet_add",30,ref)

 def test_reductions_cannot_go_negative_and_invalid_inputs_do_not_touch_ledger(self):
  with s.conn() as c:
   p=self.ready(c,970012341)
   for op in ("points_sub","wallet_sub"):
    with self.assertRaisesRegex(ValueError,"موجودی"):
     social.adjust_account(c,p["code"],op,1,"negative-block-"+op)
   for amount in (0,-1,10**13,2.5,True,"123"):
    with self.assertRaises(ValueError):
     social.adjust_account(c,p["code"],"points_add",amount,"amount-invalid-aaaaaa")
   for token in ("short","unsafe-!@@-not-good"):
    with self.assertRaises(ValueError):
     social.adjust_account(c,p["code"],"wallet_add",1,token)
   with self.assertRaises(ValueError):
    social.adjust_account(c,p["code"],"points_add",1,"nonexistent-user-a",note="\x00")
   with self.assertRaises(ValueError):
    social.adjust_account(c,"MS-00000000","points_add",1,"nonexistent-user-a")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_admin_adjustments").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'social:admin-adjust-notice:%'").fetchone()[0],0)

 def test_admin_only_profile_buttons_and_confirmation_replay(self):
  with s.conn() as c:
   target=self.ready(c,970012341)
   manager=self.ready(c,970012343)
   s.put(c,"admins",[970012343])
   social.public_card(c,970012342,970012341)
   payload=json.dumps(self.payloads(c,970012342),ensure_ascii=False)
   self.assertNotIn("soc:adjust:",payload)
   with self.assertRaisesRegex(ValueError,"فقط برای مدیر"):
    social.callback(c,970012342,"soc:adjust:points_add:"+target["code"])
   social.public_card(c,970012343,970012341)
   payload=json.dumps(self.payloads(c,970012343)[-1],ensure_ascii=False)
   for op in social.ADMIN_OPERATIONS:
    self.assertIn("soc:adjust:"+op+":"+target["code"],payload)
   social.callback(c,970012343,"soc:adjust:points_add:"+target["code"])
   self.assertTrue(c.execute("SELECT state FROM users WHERE id=970012343").fetchone()[0].startswith("soc:adminamount:"))
   social.input_text(c,970012343,"soc:adminamount:points_add:"+target["code"],
                     {"text":"35 هدیه مدیریت"},90001)
   self.assertEqual(social._adjust_info(c,970012341)["points"],0)
   row=c.execute("SELECT token FROM social_admin_adjust_drafts").fetchone()
   token=row["token"]
   with self.assertRaisesRegex(ValueError,"فقط برای مدیر"):
    social.callback(c,970012342,"soc:adjustconfirm:"+token)
   social.callback(c,970012343,"soc:adjustconfirm:"+token)
   self.assertEqual(social._adjust_info(c,970012341)["points"],35)
   with self.assertRaises(ValueError):
    social.callback(c,970012343,"soc:adjustconfirm:"+token)
   self.assertEqual(social._adjust_info(c,970012341)["points"],35)
   queued=c.execute("SELECT payload FROM outbox WHERE dedupe='social:admin-adjust-notice:"+token+"'").fetchone()
   self.assertIn("هدیه مدیریت",json.loads(queued[0])["text"])

 def test_bot_admin_accepts_persian_amount_with_thousands_separator(self):
  with s.conn() as c:
   p=self.ready(c,970012341)
   s.put(c,"admins",[970012343])
   social.input_text(c,970012343,"soc:adminamount:wallet_add:"+p["code"],
     {"text":"۱۲٬۵۰۰ هدیه شرکت"},9033)
   row=c.execute("SELECT token,amount,note FROM social_admin_adjust_drafts").fetchone()
   self.assertEqual((row["amount"],row["note"]),(12500,"هدیه شرکت"))
   self.assertEqual(social._adjust_info(c,970012341)["wallet"],0)
   social.callback(c,970012343,"soc:adjustconfirm:"+row["token"])
   self.assertEqual(social._adjust_info(c,970012341)["wallet"],12500)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ledger WHERE user_id=970012341 AND reason='social_admin_wallet_add'").fetchone()[0],1)

 def test_admin_can_manage_hidden_and_blocked_account_but_regular_user_cannot(self):
  with s.conn() as c:
   p=self.ready(c,970012341)
   s.put(c,"admins",[970012343])
   c.execute("UPDATE social_profiles SET visibility='hidden' WHERE user_id=970012341")
   c.execute("INSERT INTO social_blocks(blocker,blocked,created) VALUES(?,?,?)",
             (970012341,970012343,s.now()))
   social.public_card(c,970012343,970012341)
   bot_buttons=json.dumps(self.payloads(c,970012343)[-1],ensure_ascii=False)
   self.assertIn("soc:adjust:wallet_sub:"+p["code"],bot_buttons)
   with self.assertRaises(ValueError):
    social.public_card(c,970012342,970012341)

 def test_web_admin_action_through_sales_admin_and_history(self):
  with s.conn() as c:
   p=self.ready(c,970012341);identifier=p["code"]
  reply=s.admin({"action":"social_adjust","code":identifier,"operation":"wallet_add",
                 "amount":12500,"request_id":"admin-web-unique-ID-00001","note":"پاداش شرکت"})
  self.assertEqual(reply["after"],12500)
  again=s.admin({"action":"social_adjust","code":identifier,"operation":"wallet_add",
                 "amount":12500,"request_id":"admin-web-unique-ID-00001","note":"پاداش شرکت"})
  self.assertFalse(again["applied"])
  rows=s.admin({"action":"social_admin","view":"users"})
  target=next(x for x in rows if x["code"]==identifier)
  self.assertEqual(target["wallet_balance"],12500)
  self.assertEqual(target["points_balance"],0)
  rows=s.admin({"action":"social_admin","view":"adjustments","code":identifier})
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]["manager"],"web-admin")

 def test_integrated_user_dossier_includes_incomplete_club_and_account_history(self):
  with s.conn() as c:
   p=social.profile(c,970012341)
   c.execute("UPDATE users SET phone=?,reseller=1 WHERE id=?",("+989001112233",970012341))
   report=social.admin_view(c,{"view":"user_detail","id":970012341})
   self.assertFalse(report["profile"]["completed"])
   self.assertIn("نام",report["profile"]["missing"])
   self.assertEqual(report["code"],p["code"])
   self.assertEqual(report["phone"],"+989001112233")
   self.assertEqual(report["reseller"],1)
   self.assertEqual(report["points_balance"],0)
   self.assertIn("stats",report)
   self.assertIn("recent_chats",report)
   self.assertIn("recent_orders",report)
   self.assertIn("recent_wallet",report)
   self.assertEqual(report["recent_media"],[])
   self.ready(c,970012341)
   done=social.admin_view(c,{"view":"user_detail","id":970012341})
   self.assertTrue(done["profile"]["completed"])
   self.assertEqual(done["profile"]["missing"],[])
   self.assertEqual(done["profile"]["interests"],["🎵 موسیقی"])
   for bad in ("970012341",True,None,0,-5):
    with self.assertRaises(ValueError):
     social.admin_view(c,{"view":"user_detail","id":bad})

 def test_manager_bypasses_paid_anonymous_chat_but_regular_user_pays(self):
  with s.conn() as c:
   self.ready(c,970012341)
   self.ready(c,970012342)
   s.put(c,"admins",[970012341])
   c.execute("UPDATE media_settings SET v=70 WHERE k='social_chat_cost_random'")
   from gamification import award
   award(c,970012342,80,"test","fund:normal",s.now())
   social.callback(c,970012341,"soc:random")
   self.assertIn("soc:match:random",json.dumps(self.payloads(c,970012341)[-1]))
   social.callback(c,970012341,"soc:match:random")
   c.execute("SELECT points FROM social_chat_queue WHERE user_id=970012341")
   self.assertEqual(c.execute("SELECT points FROM social_chat_queue WHERE user_id=970012341").fetchone()[0],0)
   social.callback(c,970012342,"soc:match:random")
   self.assertEqual(social._balance(c,970012341),0)
   self.assertEqual(social._balance(c,970012342),10)
   charged=c.execute("SELECT user_id,points FROM loyalty_points WHERE kind='social_chat'").fetchall()
   self.assertEqual([tuple(r) for r in charged],[(970012342,-70)])
   social.end_chat(c,970012341)
   c.execute("UPDATE media_settings SET v=1000 WHERE k='social_chat_cost_direct'")
   p=social.profile(c,970012342)
   social.callback(c,970012341,"soc:friendchat:"+p["code"])
   quote=c.execute("SELECT cost FROM social_chat_quotes ORDER BY rowid DESC LIMIT 1").fetchone()
   self.assertEqual(quote["cost"],0)

 def test_manager_ai_is_free_without_points_and_usage_is_idempotent(self):
  from unittest.mock import patch
  import entertainment
  with s.conn() as c:
   c.execute("INSERT INTO users(id,name,created) VALUES(888881,'manager',0)")
   s.put(c,"admins",[888881]);s.put(c,"ai_cost",99)
   s.put(c,"ai_enabled",True)
   s.put(c,"ai_model","test-model")
   entertainment.ai_start(c,888881)
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=888881").fetchone()[0],"ai:chat")
   with patch.object(entertainment.loyalty,"query_ai",return_value="تست پاسخ"):
    entertainment.ai_answer(c,888881,"سلام",121212)
    entertainment.ai_answer(c,888881,"سلام",121212)
   self.assertEqual(entertainment.loyalty.balance(c,888881),0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM loyalty_points WHERE user_id=888881").fetchone()[0],0)
   item=c.execute("SELECT points FROM ai_usage WHERE user_id=888881").fetchone()
   self.assertEqual(item["points"],0)
   self.assertIn("بدون کسر امتیاز",json.dumps(self.payloads(c,888881),ensure_ascii=False))

 def test_manager_can_match_chat_without_completed_profile_and_zero_points(self):
  with s.conn() as c:
   s.put(c,"admins",[970012341])
   social.profile(c,970012341)
   self.ready(c,970012342)
   from gamification import award
   award(c,970012342,20,"test","regular:fund",s.now())
   c.execute("UPDATE media_settings SET v=20 WHERE k='social_chat_cost_random'")
   self.assertFalse(social.complete(social.profile(c,970012341)))
   social.start_matching(c,970012341,"random")
   self.assertEqual(c.execute("SELECT points FROM social_chat_queue WHERE user_id=970012341").fetchone()[0],0)
   social.start_matching(c,970012342,"random")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_chat_pairs WHERE closed=0").fetchone()[0],1)
   self.assertEqual(social._balance(c,970012341),0)
   self.assertEqual(social._balance(c,970012342),0)
   paid=c.execute("SELECT COUNT(*) FROM loyalty_points WHERE user_id=? AND kind='social_chat'",(970012341,)).fetchone()[0]
   self.assertEqual(paid,0)

 def test_gallery_delivery_restricted_to_defined_telegram_admin(self):
  import json
  with s.conn() as c:
   p=self.ready(c,970012341)
   s.put(c,"admins",[970012343])
   c.execute("UPDATE social_profiles SET photos=? WHERE user_id=?",
      (json.dumps(["valid_telegram_file_id_aaaa"]),970012341))
  with self.assertRaises(ValueError):
   s.admin({"action":"social_gallery_to_admin","id":970012341,"admin_id":970012342})
  outcome=s.admin({"action":"social_gallery_to_admin","id":970012341,"admin_id":970012343})
  self.assertEqual(outcome["count"],1)
  with s.conn() as c:
   result=c.execute("SELECT method,payload FROM outbox WHERE chat=970012343 ORDER BY id DESC LIMIT 1").fetchone()
   self.assertEqual(result["method"],"sendPhoto")
   self.assertIn("valid_telegram_file_id_aaaa",result["payload"])
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE chat=970012342").fetchone()[0],0)

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
