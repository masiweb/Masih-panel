import os,sys,tempfile,sqlite3,unittest,time
from pathlib import Path
# Test the sibling source code, not an older installed runtime elsewhere.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import unified as u
class SharedQuota(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();u.DB=self.tmp.name+"/panel.db"
  with u.connection() as c:
   c.executescript("CREATE TABLE clients(id INTEGER PRIMARY KEY,created_at INTEGER,email TEXT,sub_id TEXT,enable INTEGER,total_gb INTEGER,expiry_time INTEGER);CREATE TABLE client_traffics(email TEXT PRIMARY KEY,up INTEGER,down INTEGER,total INTEGER,expiry_time INTEGER,enable INTEGER,last_online INTEGER);")
   c.execute("INSERT INTO clients VALUES(1,10,'a','token',1,1000,0)")
   c.execute("INSERT INTO client_traffics VALUES('a',100,200,1000,0,1,0)")
  u.initialize()
  with u.connection() as c:c.execute("INSERT INTO masiha_accounts VALUES(1,10,'testuser','secret',1,'')")
 def tearDown(self):self.tmp.cleanup()
 def counts(self):
  with u.connection() as c:return tuple(c.execute("SELECT up,down FROM client_traffics").fetchone())
 def test_shared_and_idempotent_out_of_order(self):
  u.credit("testuser","openvpn","one",30,40)
  u.credit("testuser","openconnect","two",50,60)
  u.credit("testuser","openvpn","one",30,40)
  u.credit("testuser","openconnect","two",10,20)
  self.assertEqual(self.counts(),(180,300))
  u.credit("testuser","openvpn","one",40,60)
  self.assertEqual(self.counts(),(190,320))
 def test_reset_does_not_recredit_old_session(self):
  u.credit("testuser","openvpn","one",100,200)
  with u.connection() as c:c.execute("UPDATE client_traffics SET up=0,down=0")
  u.credit("testuser","openvpn","one",100,200)
  self.assertEqual(self.counts(),(0,0))
  u.credit("testuser","openvpn","one",110,240)
  self.assertEqual(self.counts(),(10,40))
 def test_expiry_and_aggregate_quota(self):
  u.credit("testuser","openvpn","one",400,400)
  with u.connection() as c:self.assertFalse(u.permitted(u.clients(c)[0]))
  with u.connection() as c:c.execute("UPDATE clients SET total_gb=0");c.execute("UPDATE client_traffics SET expiry_time=?",(int(time.time()*1000)-1,))
  with u.connection() as c:self.assertFalse(u.permitted(u.clients(c)[0]))
 def test_late_event_cannot_charge_recreated_client(self):
  with u.connection() as c:c.execute("UPDATE clients SET created_at=20")
  u.credit("testuser","openvpn","one",400,400)
  self.assertEqual(self.counts(),(100,200))
 def test_missing_stats_does_not_ack_usage(self):
  with u.connection() as c:c.execute("DELETE FROM client_traffics")
  with self.assertRaises(ValueError):u.credit("testuser","openvpn","one",400,400)
  with u.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM masiha_sessions").fetchone()[0],0)
 def test_unknown_token(self):
  with self.assertRaises(ValueError):u.profiles("wrong-token")
 def test_invalid_counter(self):
  with self.assertRaises(ValueError):u.credit("testuser","openvpn","one",-1,0)
if __name__=="__main__":unittest.main()
