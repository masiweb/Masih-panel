#!/usr/bin/env python3
"""Admin-only searchable AI conversation monitor tests."""
import sqlite3
import unittest
from unittest.mock import patch

import ai_admin_audit as audit

class AIAdminAuditTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(":memory:")
  self.c.row_factory=sqlite3.Row
  self.c.executescript("""
   CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,telegram_username TEXT);
   CREATE TABLE ai_chat_history(id INTEGER PRIMARY KEY,user_id INTEGER,role TEXT,
    content TEXT,model TEXT,reference TEXT,created INTEGER);
   CREATE TABLE ai_media_jobs(uid INTEGER,kind TEXT,status TEXT,created INTEGER,
    prompt TEXT,file_id TEXT,output_file_id TEXT,provider_id TEXT);
   CREATE TABLE ai_usage(user_id INTEGER,reference TEXT,model TEXT,points INTEGER,created INTEGER);
   INSERT INTO users VALUES(101,'Ali','09121234567','alpha_user');
   INSERT INTO users VALUES(102,'Sara','09350000000','beta_user');
   INSERT INTO users VALUES(103,'Mohsen','09000000000','gamma_user');
  """)
  audit.setup(self.c)
  audit.record_text(self.c,101,"ai:1","question 1","answer 1","model-a",1000)
  audit.record_text(self.c,102,"ai:2","question 2","answer 2","model-b",2000)
  audit.record_text(self.c,103,"ai:3","question 3","answer 3","model-c",3000)
  self.sent=[]
  self.settings_patch=patch.object(audit,"settings",return_value={"admins":[900]})
  self.audit_patch=patch.object(audit,"audit")
  self.enqueue_patch=patch.object(audit,"enqueue",side_effect=self.enqueue)
  self.settings_patch.start();self.audit_patch.start();self.enqueue_patch.start()

 def tearDown(self):
  self.enqueue_patch.stop();self.audit_patch.stop();self.settings_patch.stop()
  self.c.close()

 def enqueue(self,c,admin,text,buttons=None,quick_nav=False):
  self.sent.append({"admin":admin,"text":text,"buttons":buttons or []})
  return True

 def _ids(self):
  return [button[1] for row in self.sent[-1]["buttons"] for button in row]

 def test_monitor_shows_recent_users_and_search_action(self):
  audit.monitor(self.c,900,0)
  ids=self._ids()
  self.assertIn("a:aimonitordetail:103",ids)
  self.assertIn("a:aimonitordetail:102",ids)
  self.assertIn("a:aimonitordetail:101",ids)
  self.assertIn("a:aimonitorsearch",ids)

 def test_search_by_phone(self):
  audit.monitor_search(self.c,900,"۰۹۱۲۱۲۳۴۵۶۷")
  self.assertIn("a:aimonitordetail:101",self._ids())
  self.assertNotIn("a:aimonitordetail:102",self._ids())

 def test_search_by_telegram_id(self):
  audit.monitor_search(self.c,900,"102")
  self.assertEqual(self._ids()[0],"a:aimonitordetail:102")

 def test_search_by_username(self):
  audit.monitor_search(self.c,900,"@gamma_user")
  self.assertEqual(self._ids()[0],"a:aimonitordetail:103")

 def test_non_admin_cannot_monitor_or_search(self):
  with self.assertRaises(ValueError):audit.monitor(self.c,101,0)
  with self.assertRaises(ValueError):audit.monitor_search(self.c,101,"101")

 def test_admin_can_open_conversation_detail(self):
  audit.route(self.c,900,"aimonitordetail",["101"])
  self.assertTrue(any("گفتگوهای متنی ثبت‌شده" in item["text"] for item in self.sent))
  self.sent.clear()
  audit.route(self.c,900,"aidetail",["101","1"])
  self.assertTrue(any("question 1" in item["text"] for item in self.sent))

if __name__=="__main__":
 unittest.main()
