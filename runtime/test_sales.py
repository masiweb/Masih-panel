import os,tempfile,sys,json,unittest,secrets
from unittest.mock import patch
tmp=tempfile.TemporaryDirectory(prefix="sales-test-")
os.environ["MASIHA_SALES_DIR"]=tmp.name
sys.path.insert(0,"/opt/masiha-vpn-extension")
import sales as s
import sales_worker as w
class Commerce(unittest.TestCase):
 def setUp(self):
  with s.conn() as c:
   for t in ("settings","users","orders","plans","services","ledger","coupons","redeemed","outbox","topups","updates","payments","quotes","receipt_orders"):c.execute("DELETE FROM "+t)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(101,'test',100000,0)")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(102,'other',0,0)")
   c.execute("INSERT INTO plans VALUES(1,'test','test','openvpn',10000,30,0,'[]',0,1)")
 def test_duplicate_purchase_and_balance(self):
  with s.conn() as c:
   a=s.order(c,101,1,"same");b=s.order(c,101,1,"same")
   self.assertEqual(a,b);self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],90000)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],1)
 def test_insufficient_funds_rolls_back(self):
  with self.assertRaises(ValueError):
   with s.conn() as c:s.order(c,102,1,"no-funds")
  with s.conn() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)
 def test_transfer_failure_rolls_back(self):
  with self.assertRaises(ValueError):
   with s.conn() as c:
    s.money(c,101,-5000,"transfer","a");s.money(c,999,5000,"transfer","b")
  with s.conn() as c:self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],100000)
 def test_admin_callback_not_forgeable(self):
  with s.conn() as c:
   s.put(c,"admins",[102]);c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(101,20000,'receipt',0)")
   with self.assertRaises(ValueError):w.handle(c,{"update_id":1,"callback_query":{"from":{"id":101},"message":{"chat":{"id":101,"type":"private"}},"data":"approve:1"}})
 def test_secret_not_returned(self):
  s.admin({"action":"settings","values":{"admins":[101],"token":"123456:"+("x"*30)}})
  result=s.admin({"action":"get"})
  self.assertTrue(result["settings"]["token_set"]);self.assertNotIn("token",result["settings"])
 def test_trial_once(self):
  with s.conn() as c:
   s.put(c,"trial",True);s.put(c,"trial_plan",1);s.order(c,101,1,"trial",trial=True)
   with self.assertRaises(ValueError):s.order(c,101,1,"trial2",trial=True)
 def test_provision_retry_target_stable(self):
  with s.conn() as c:
   s.order(c,101,1,"retry");c.commit()
   def fake(req):
    if req["action"]=="list":return {"users":[]}
    return {}
   with patch.object(s,"vpn",side_effect=fake):
    s.provision(c,c.execute("SELECT * FROM orders").fetchone());c.commit()
   self.assertEqual(c.execute("SELECT status FROM orders").fetchone()[0],"fulfilled")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM services").fetchone()[0],1)
 def test_quote_changes_rejected(self):
  with s.conn() as c:
   q=w.quote(c,101,1);c.execute("UPDATE plans SET price=20000 WHERE id=1")
   with self.assertRaises(ValueError):w.purchase_quote(c,101,q)
 def test_bulk_atomic(self):
  with s.conn() as c:
   s.put(c,"bulk",True);s.put(c,"reseller",True);s.put(c,"reseller_discount",10)
   c.execute("UPDATE users SET reseller=1 WHERE id=101")
   q=w.quote(c,101,1,quantity=5);w.purchase_quote(c,101,q);w.purchase_quote(c,101,q)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],5)
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],55000)
 def test_gateway_verification_exact_amount_once(self):
  with s.conn() as c:
   c.execute("INSERT INTO payments VALUES('p1',101,1234,'A12345678901234567890','pending','',0)")
  with patch.object(s,"gateway",return_value={"code":100,"ref_id":55}) as gateway:
   s.payment_verify("A12345678901234567890")
   self.assertEqual(gateway.call_args.args[1]["amount"],12340)
   s.payment_verify("A12345678901234567890")
   self.assertEqual(gateway.call_count,1)
  with s.conn() as c:self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],101234)
 def test_fake_payment_never_credits(self):
  with self.assertRaises(ValueError):s.payment_verify("A00000000000000000000")
  with s.conn() as c:self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],100000)
 def test_card_order_without_wallet_and_duplicate_approval(self):
  with s.conn() as c:
   s.put(c,"card","test-card");s.put(c,"wallet",False);s.put(c,"admins",[102])
   q=w.quote(c,102,1);w.purchase_quote(c,102,q,card=True)
   o=c.execute("SELECT * FROM orders").fetchone()
   self.assertEqual(o["status"],"awaiting_receipt")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],0)
   c.execute("UPDATE users SET state=? WHERE id=102",("orderreceipt:"+o["id"],))
   w.handle(c,{"update_id":4,"message":{"from":{"id":102},"chat":{"type":"private"},"photo":[{"file_id":"test-file"}]}})
   r=c.execute("SELECT * FROM topups").fetchone()
   self.assertEqual(r["amount"],10000)
   s.decide_receipt(c,r["id"],True)
   with self.assertRaises(ValueError):s.decide_receipt(c,r["id"],True)
   self.assertEqual(c.execute("SELECT status FROM orders").fetchone()[0],"paid")
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],0)
 def test_card_rejection_never_provisions(self):
  with s.conn() as c:
   s.put(c,"card","test-card")
   q=w.quote(c,102,1);w.purchase_quote(c,102,q,card=True)
   o=c.execute("SELECT * FROM orders").fetchone()
   c.execute("UPDATE orders SET status='receipt_review'")
   rid=c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(102,10000,'test',0)").lastrowid
   c.execute("INSERT INTO receipt_orders VALUES(?,?)",(rid,o["id"]))
   s.decide_receipt(c,rid,False)
   self.assertEqual(c.execute("SELECT status FROM orders").fetchone()[0],"cancelled")
 def test_bot_activation_without_gateway(self):
  with patch.object(s,"tg",return_value={"username":"test_bot"}):
   s.admin({"action":"settings","values":{"enabled":True,"admins":[101],"token":"123456:"+("x"*30),"zarinpal":True}})
  self.assertTrue(s.admin({"action":"get"})["settings"]["enabled"])
  self.assertFalse(s.admin({"action":"get"})["settings"]["zarinpal"])
 def test_ownership(self):
  with s.conn() as c:
   with self.assertRaises(ValueError):w.deliver(c,102,"nonexistent")
if __name__=="__main__":unittest.main()
