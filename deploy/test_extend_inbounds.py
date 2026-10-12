#!/usr/bin/env python3
"""Regression tests for additive Shadowsocks / modern XHTTP presets."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

p=Path(__file__).resolve().with_name("extend-inbounds.py")
spec=importlib.util.spec_from_file_location("extend_inbounds",p)
x=importlib.util.module_from_spec(spec)
spec.loader.exec_module(x)

class TestExtraInbounds(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.db=str(Path(self.tmp.name)/"panel.db")
  with sqlite3.connect(self.db) as db:
   db.executescript("""
   CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
   INSERT INTO users VALUES(1,'admin');
   CREATE TABLE inbounds(id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,remark TEXT,tag TEXT,protocol TEXT,port INTEGER,
    listen TEXT,enable INTEGER,up INTEGER,down INTEGER,total INTEGER,
    expiry_time INTEGER,traffic_reset TEXT,traffic_reset_day INTEGER,
    settings TEXT,stream_settings TEXT,sniffing TEXT);
   INSERT INTO inbounds(user_id,remark,tag,protocol,port) VALUES(1,'existing','existing','vless',8443);
   """)
 def tearDown(self):self.tmp.cleanup()
 def run_extend(self):
  real_is_file=Path.is_file
  with patch.object(Path,'is_file',autospec=True,side_effect=lambda self:
    True if str(self).startswith('/etc/letsencrypt/live/') or str(self)=='/usr/bin/xray'
    else real_is_file(self)),patch.object(x,'validate_xray',return_value=None),patch.object(
     x.socket.socket,'connect_ex',return_value=111):
   return x.extend(self.db,'vpn.example.com','/usr/bin/xray')
 def test_additive_and_unique(self):
  self.assertEqual(self.run_extend(),3)
  with sqlite3.connect(self.db) as db:
   rows=db.execute("SELECT port,protocol,settings,stream_settings FROM inbounds ORDER BY port").fetchall()
  self.assertEqual([r[0] for r in rows],[8443,8449,8450,8451])
  self.assertEqual([r[1] for r in rows[1:]],['shadowsocks','vless','vless'])
  ss=json.loads(rows[1][2])
  self.assertEqual(ss['method'],'2022-blake3-aes-128-gcm')
  self.assertTrue(len(ss['password'])>16)
  for row,mode in zip(rows[2:],['stream-up','packet-up']):
   self.assertEqual(json.loads(row[3])['xhttpSettings']['mode'],mode)
 def test_repeated_call_is_noop(self):
  self.assertEqual(self.run_extend(),3)
  self.assertEqual(self.run_extend(),0)
 def test_port_conflict_preserves_existing(self):
  with sqlite3.connect(self.db) as db:
   db.execute("INSERT INTO inbounds(user_id,port,tag) VALUES(1,8450,'another')")
  with self.assertRaisesRegex(RuntimeError,'Port already used'):
   self.run_extend()
  with sqlite3.connect(self.db) as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM inbounds').fetchone()[0],2)
 def test_rejects_domain(self):
  with self.assertRaises(ValueError):x.extend(self.db,'invalid/domain','/usr/bin/xray')

if __name__=='__main__':unittest.main()
