#!/usr/bin/env python3
"""No network or real Telegram traffic; approval/permission/queue regression tests."""
import datetime as dt
import json
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import channel_manager as cm

class ChannelManagerTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(":memory:")
  self.c.row_factory=sqlite3.Row
  self.c.execute("""CREATE TABLE outbox(
   id INTEGER PRIMARY KEY AUTOINCREMENT,dedupe TEXT UNIQUE,
   chat INTEGER,method TEXT,payload TEXT,done INTEGER DEFAULT 0,
   attempts INTEGER DEFAULT 0,next_try INTEGER DEFAULT 0)""")
  self.c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,state TEXT DEFAULT '')")
  self.c.executemany("INSERT INTO users(id,state) VALUES(?,'')",[(101,),(102,)])
  cm.setup(self.c)
  self.day="2026-11-02"
  self.t=int(dt.datetime(2026,11,2,9,0,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  self.c.execute("""UPDATE channel_manager_config SET
    channel_id='-1001234567890',discussion_id=-1009999999999,
    bot_id=999,bot_username='MasihaV2ray_bot',
    verified_at=?,enabled=1 WHERE id=1""",(self.t,))
  self.messages=[]
  self.api_calls=[]
  def enqueue(c,uid,text,buttons=None,key=None,quick_nav=True):
   self.messages.append((uid,text,buttons,key))
  def tg(s,method,payload):
   self.api_calls.append((method,payload))
   if method=="getMe":return {"id":999,"username":"MasihaV2ray_bot"}
   if method=="getChat":
    return {"id":-1001234567890,"type":"channel","title":"Test Channel",
            "linked_chat_id":-1009999999999} if str(payload["chat_id"]) in ("@TestChannel","-1001234567890") else {"id":-1009999999999,"type":"supergroup"}
   if method=="getChatMember":return {"status":"administrator","can_post_messages":True}
   raise AssertionError(method)
  self.fake_sales=types.ModuleType("sales")
  self.fake_sales.settings=lambda c:{"admins":[101],"bot_username":"MasihaV2ray_bot"}
  self.fake_sales.enqueue=enqueue
  self.fake_sales.tg=tg
  self.fake_sales.audit=lambda c,action,detail:None
  self.fake_sales.normalize_channel=lambda txt: txt if txt.startswith("@") else "@"+txt
  self.fake_ai=types.ModuleType("ai_models")
  self.fake_ai.select=lambda c,k,t:{"provider":"openai","model_id":"test-model"}
  self.fake_ai.credentials=lambda c:{"openai":True}
  self.fake_ai.text_response=lambda c,model,prompt,expanded=False:"عنوان آموزشی\n"+"این یک مطلب مفید برای کاربران است. "*8
  self.patch_modules=patch.dict(sys.modules,{"sales":self.fake_sales,"ai_models":self.fake_ai})
  self.patch_modules.start()
  self.clock=patch.object(cm,"now",return_value=self.t)
  self.now_mock=self.clock.start()
 def tearDown(self):
  self.clock.stop()
  self.patch_modules.stop()
  self.c.close()

 def posts(self):
  return self.c.execute("SELECT * FROM channel_manager_posts ORDER BY slot").fetchall()

 def test_three_drafts_require_individual_approval_and_never_exceed_daily_limit(self):
  for _ in range(3):self.assertTrue(cm._make_one_draft(self.c))
  self.assertFalse(cm._make_one_draft(self.c))
  self.assertEqual(len(self.posts()),3)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)
  for post in self.posts()[:2]:
   cm.admin_callback(self.c,101,"ch:approve:"+str(post["id"]))
  # Approved posts wait for their slot; no burst of messages at 09:00.
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)
  self.now_mock.return_value=int(dt.datetime(2026,11,2,10,1,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],1)
  self.assertEqual(self.posts()[2]["status"],"review")
  self.now_mock.return_value=int(dt.datetime(2026,11,2,15,1,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],2)
  cm.admin_callback(self.c,101,"ch:approve:"+str(self.posts()[2]["id"]))
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],2)
  self.now_mock.return_value=int(dt.datetime(2026,11,2,20,31,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  cm._stage_approved(self.c)
  queued=self.c.execute("SELECT dedupe,chat FROM outbox").fetchall()
  self.assertEqual(len(queued),3)
  self.assertTrue(all(r["dedupe"].startswith("channel-post:") for r in queued))
  for r in self.c.execute("SELECT id FROM outbox"):
   self.c.execute("UPDATE outbox SET done=1 WHERE id=?",(r["id"],))
  cm._complete_outbox(self.c)
  self.assertEqual(sum(r["status"]=="published" for r in self.posts()),3)

 def test_admin_cannot_bypass_approval(self):
  cm._make_one_draft(self.c)
  post=self.posts()[0]
  with self.assertRaises(ValueError):
   cm.admin_callback(self.c,102,"ch:approve:"+str(post["id"]))
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)
  cm.admin_callback(self.c,101,"ch:reject:"+str(post["id"]))
  with self.assertRaises(ValueError):
   cm.admin_callback(self.c,101,"ch:approve:"+str(post["id"]))

 def test_edited_post_resets_approval(self):
  cm._make_one_draft(self.c)
  ident=self.posts()[0]["id"]
  cm.admin_callback(self.c,101,"ch:approve:"+str(ident))
  cm.admin_callback(self.c,101,"ch:edit:"+str(ident))
  cm.admin_input(self.c,101,"ch:edit:"+str(ident),"یک مطلب جدید با محتوای تأیید نشده برای انتشار.")
  self.assertEqual(self.posts()[0]["status"],"review")
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)

 def test_permission_check_and_group_verification(self):
  self.c.execute("UPDATE channel_manager_config SET channel_id='@TestChannel',verified_at=0,enabled=0")
  title,group=cm.verify(self.c)
  self.assertEqual(title,"Test Channel")
  self.assertEqual(group,-1009999999999)
  self.assertEqual(cm.config(self.c)["bot_id"],999)
  self.assertEqual(cm.config(self.c)["enabled"],0)
  def forbidden(s,method,payload):
   if method=="getChatMember":return {"status":"administrator","can_post_messages":False}
   return self.fake_sales.tg.__wrapped__(s,method,payload)
  # Verify negative privilege without granting publishing permission.
  self.fake_sales.tg=lambda s,method,payload: {"id":999,"username":"MasihaV2ray_bot"} if method=="getMe" else (
   {"id":-1001234567890,"type":"channel"} if method=="getChat" else
   {"status":"administrator","can_post_messages":False})
  with self.assertRaises(ValueError):cm.verify(self.c)

 def test_linked_group_mentions_and_direct_replies_only(self):
  def msg(text,mid,reply=None,uid=202):
   m={"chat":{"id":-1009999999999,"type":"supergroup"},
      "from":{"id":uid,"first_name":"Test"},"message_id":mid,"text":text}
   if reply:m["reply_to_message"]={"from":{"id":reply}}
   return {"message":m}
  self.assertFalse(cm.group_message(self.c,msg("What is VPN?",1)))
  self.assertTrue(cm.group_message(self.c,msg("@MasihaV2ray_bot راهنمای خرید چیست؟",2)))
  self.assertFalse(cm.group_message(self.c,msg("@MasihaV2ray_bot دوباره لطفاً",3)))
  self.assertTrue(cm.group_message(self.c,msg("چطور وصل شوم؟",4,reply=999,uid=203)))
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM channel_manager_replies").fetchone()[0],2)
  cm._reply_once(self.c)
  output=json.loads(self.c.execute("SELECT payload FROM outbox LIMIT 1").fetchone()[0])
  self.assertEqual(output["reply_parameters"]["message_id"],2)
  self.assertEqual(output["chat_id"],-1009999999999)

 def test_pause_cancels_unsent_post_without_losing_admin_approval(self):
  cm._make_one_draft(self.c)
  post=self.posts()[0]
  cm.admin_callback(self.c,101,"ch:approve:"+str(post["id"]))
  self.now_mock.return_value=int(dt.datetime(2026,11,2,10,10,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],1)
  cm.admin_callback(self.c,101,"ch:pause")
  self.assertEqual(cm.config(self.c)["enabled"],0)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)
  self.assertEqual(self.posts()[0]["status"],"approved")
  cm.admin_callback(self.c,101,"ch:resume")
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],1)

 def test_drafts_only_cover_remaining_daily_slots(self):
  self.now_mock.return_value=int(dt.datetime(2026,11,2,12,0,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  self.assertTrue(cm._make_one_draft(self.c))
  self.assertEqual(self.posts()[0]["slot"],1)
  self.assertTrue(cm._make_one_draft(self.c))
  self.assertEqual(self.posts()[1]["slot"],2)
  self.assertFalse(cm._make_one_draft(self.c))
  self.now_mock.return_value=int(dt.datetime(2026,11,2,21,0,tzinfo=ZoneInfo(cm.TIMEZONE)).timestamp())
  self.assertFalse(cm._make_one_draft(self.c))

 def test_cannot_post_without_recent_verification(self):
  self.c.execute("UPDATE channel_manager_config SET verified_at=0")
  self.assertFalse(cm._make_one_draft(self.c))
  cm._stage_approved(self.c)
  self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],0)

if __name__=="__main__":
 unittest.main()
