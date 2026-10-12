#!/usr/bin/env python3
"""Verify all benchmark cipher/transport settings and safe additive insertion."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

script=Path(__file__).with_name("benchmark-inbounds.py")
spec=importlib.util.spec_from_file_location("benchmark_inbounds",script)
x=importlib.util.module_from_spec(spec)
spec.loader.exec_module(x)

class TestBenchInbounds(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.db=str(Path(self.tmp.name)/"panel.db")
  with sqlite3.connect(self.db) as conn:
   conn.executescript("""
     CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
     INSERT INTO users VALUES(1,'admin');
     CREATE TABLE inbounds(id INTEGER PRIMARY KEY, user_id INTEGER, remark TEXT, tag TEXT,
     protocol TEXT, port INTEGER, listen TEXT, enable INTEGER, up INTEGER, down INTEGER,
     total INTEGER, expiry_time INTEGER, traffic_reset TEXT, traffic_reset_day INTEGER,
     settings TEXT, stream_settings TEXT, sniffing TEXT);
     INSERT INTO inbounds(id,user_id,remark,tag,protocol,port)
        VALUES(1,1,'Original','in-8443','vless',8443);
   """)
 def tearDown(self):self.tmp.cleanup()
 def setup_bench(self):
  with patch.object(x,"xray_test") as verify:
   count=x.extend(self.db,"exon.chanelchat.ir","/usr/local/x-ui/bin/xray-linux-amd64",
                  check_listeners=False,check_tls=False)
  return count,verify
 def test_all_six_and_idempotence(self):
  count,verify=self.setup_bench()
  self.assertEqual(count,6)
  self.assertEqual(verify.call_count,1)
  with sqlite3.connect(self.db) as conn:
   rows=conn.execute("SELECT port,protocol,settings,stream_settings FROM inbounds WHERE port>=8452 ORDER BY port").fetchall()
  self.assertEqual([r[0] for r in rows],list(range(8452,8458)))
  for port,proto,settings,stream in rows:
   cfg=json.loads(settings); transport=json.loads(stream)
   self.assertEqual(cfg["clients"],[])
   if port<=8455:
    self.assertEqual(proto,"shadowsocks")
    self.assertEqual(transport["security"],"none")
    self.assertEqual(cfg["network"],"tcp,udp")
    self.assertIn(cfg["method"],{
       "aes-128-gcm","aes-256-gcm","xchacha20-ietf-poly1305","chacha20-ietf-poly1305"})
    self.assertGreaterEqual(len(cfg["password"]),32)
   else:
    self.assertEqual(proto,"vless")
    self.assertEqual(transport["security"],"tls")
    self.assertEqual(cfg["decryption"],"none")
    self.assertEqual(transport["network"],"grpc" if port==8456 else "httpupgrade")
    self.assertEqual(transport["tlsSettings"]["serverName"],"exon.chanelchat.ir")
  count,_=self.setup_bench()
  self.assertEqual(count,0)
 def test_conflict_atomic(self):
  with sqlite3.connect(self.db) as c:
   c.execute("INSERT INTO inbounds(port,tag,user_id) VALUES(8456,'conflict',1)")
  with self.assertRaisesRegex(RuntimeError,"Port already registered"):
   self.setup_bench()
  with sqlite3.connect(self.db) as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0],2)
 def test_validator_failure_does_not_insert(self):
  with patch.object(x,"xray_test",side_effect=RuntimeError("invalid")):
   with self.assertRaisesRegex(RuntimeError,"invalid"):
    x.extend(self.db,"exon.chanelchat.ir","/usr/local/x-ui/bin/xray-linux-amd64",
                 check_listeners=False,check_tls=False)
  with sqlite3.connect(self.db) as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0],1)
 def test_no_shared_2022_psk(self):
  self.assertTrue(all("2022" not in str(p) for p in x.PRESETS))
 def test_bad_domain_rejected(self):
  with self.assertRaises(ValueError):
   x.extend(self.db,"a/b.invalid","/usr/local/x-ui/bin/xray-linux-amd64",
            check_listeners=False,check_tls=False)

if __name__=="__main__":unittest.main()
