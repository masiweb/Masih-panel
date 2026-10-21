#!/usr/bin/env python3
"""Fail-closed IPsec provisioning + shared quota accounting tests, no real system writes."""
import os,sys,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ipsec_backend as ipsec
import ipsec_accounting as accounting
import manager,unified

class IPsecSafety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  p=Path(self.tmp.name)
  self.basedir=p/"manager"
  self.basedir.mkdir()
  for target,field,value in [(manager,"BASE",self.basedir),(ipsec,"ROOT",p/"ipsec"),(ipsec,"ACTIVE",p/"ipsec"/"enabled"),(unified,"DB",str(p/"panel.db"))]:
   patcher=patch.object(target,field,value)
   patcher.start()
   self.addCleanup(patcher.stop)
  with unified.connection() as c:
   c.executescript("CREATE TABLE clients(id INTEGER PRIMARY KEY,created_at INTEGER,email TEXT,sub_id TEXT,enable INTEGER,total_gb INTEGER,expiry_time INTEGER);CREATE TABLE client_traffics(email TEXT PRIMARY KEY,up INTEGER,down INTEGER,total INTEGER,expiry_time INTEGER,enable INTEGER,last_online INTEGER);")
   c.execute("INSERT INTO clients VALUES(1,10,'first','subtoken',1,1000,0)")
   c.execute("INSERT INTO client_traffics VALUES('first',0,0,1000,0,1,0)")
  unified.initialize()
  with unified.connection() as c:c.execute("INSERT INTO masiha_accounts VALUES(1,10,'testuser','test-password-12345',1,'')")
 def test_protocol_default_safe_off(self):
  self.assertFalse(ipsec.configured("ikev2"))
  self.assertFalse(ipsec.configured("l2tp"))
  with self.assertRaises(RuntimeError):
   manager.handle({"action":"create","protocol":"ikev2","username":"testuser","password":"safe-password-xyz"})
  with manager.db() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM users").fetchone()[0],0)
 def test_secrets_and_no_credentials_from_status(self):
  with ipsec.db() as c:
   c.execute("INSERT INTO users VALUES('ikev2','testuser',1,0,'',1)")
   c.execute("INSERT INTO users VALUES('l2tp','testuser',1,0,'',1)")
   c.execute("INSERT INTO ipsec_credentials VALUES('testuser','long-password-1234',1)")
   secret,ppp=ipsec.render_secrets(c,"Z"*48)
   self.assertIn('testuser : EAP "long-password-1234"',secret)
   self.assertIn('"testuser" xl2tpd "long-password-1234" *',ppp)
   self.assertNotIn('test-password',str(ipsec.status("ikev2")))
   self.assertEqual(len(c.execute("SELECT * FROM ipsec_credentials").fetchall()),1)
 def test_invalid_credentials(self):
  for username,password in [("a","long-password-1234"),("goodname","bad\npassword"),("goodname","short"),("bad space","good-password-1234")]:
   with self.assertRaises(ValueError):ipsec.validate(username,password)
 def test_parser_and_shared_quota(self):
  sample="""ikev2: #3, ESTABLISHED, IKEv2, abc
  local  'vpn.example' @ 1.2.3.4
  remote '1.2.3.4' @ 5.6.7.8 EAP: 'testuser' [10.83.0.3]
  ikev2: #1, reqid 4, INSTALLED, TUNNEL, ESP:AES_GCM
    in  c2713224,  250 bytes,   2 packets, 1s ago
    out 36ce686c,  400 bytes,   3 packets, 1s ago
"""
  values=accounting.parse_ike_sas(sample)
  self.assertEqual(values,[{"username":"testuser","ike_id":"3","child_id":"1","up":250,"down":400}])
  accounting.sample_ike(sample)
  self.assertEqual(self.counters(),(250,400))
  accounting.sample_ike(sample)
  self.assertEqual(self.counters(),(250,400))
  more=sample.replace("250 bytes","260 bytes").replace("400 bytes","415 bytes")
  accounting.sample_ike(more)
  self.assertEqual(self.counters(),(260,415))
  with unified.connection() as c:
   row=unified.clients(c)[0]
   self.assertTrue(unified.permitted(row))
  accounting.sample_ike(more.replace("260 bytes","620 bytes"))
  with unified.connection() as c:self.assertFalse(unified.permitted(unified.clients(c)[0]))
 def counters(self):
  with unified.connection() as c:
   return tuple(c.execute("SELECT up,down FROM client_traffics").fetchone())
 def test_no_credit_without_both_directions(self):
  text="""ikev2: #3, ESTABLISHED, IKEv2
  remote 'client' @ public EAP: 'testuser' [10.83.0.3]
  ikev2: #1, reqid 4, INSTALLED, TUNNEL
   in  SPI,  90 bytes, 1s ago
"""
  self.assertEqual(accounting.parse_ike_sas(text),[])
  self.assertEqual(accounting.sample_ike(text),0)
  self.assertEqual(self.counters(),(0,0))

if __name__=="__main__":unittest.main()