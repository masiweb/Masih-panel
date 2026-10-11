import os,tempfile,sys,json,unittest,secrets
from pathlib import Path
from unittest.mock import patch
tmp=tempfile.TemporaryDirectory(prefix="sales-test-")
os.environ["MASIHA_SALES_DIR"]=tmp.name
sys.path.insert(0,str(Path(__file__).resolve().parent))
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
 def test_transient_update_failure_does_not_acknowledge(self):
  with s.conn() as c:
   s.put(c,"enabled",True)
   s.put(c,"token","123456:"+("x"*30))
   s.put(c,"admins",[101])
   s.put(c,"bot_username","test_bot")
  update={"update_id":991,"message":{"from":{"id":101},"chat":{"id":101,"type":"private"},"text":"/start"}}
  def fake_tg(conf,method,payload):
   if method=="getUpdates":return [update]
   return True
  with patch.object(w,"tg",side_effect=fake_tg), patch.object(w,"handle",side_effect=RuntimeError("temporary upstream outage")):
   with self.assertRaises(RuntimeError):w.tick()
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT count(*) FROM updates").fetchone()[0],0)
   self.assertEqual(s.settings(c).get("offset",0),0)
 def test_dead_letter_is_visible_and_retryable(self):
  with s.conn() as c:
   s.put(c,"enabled",True)
   s.put(c,"token","123456:"+("x"*30))
   s.put(c,"admins",[101])
   s.put(c,"bot_username","test_bot")
   rid=c.execute("INSERT INTO outbox(dedupe,chat,method,payload,attempts) VALUES('dead',101,'sendMessage','{}',15)").lastrowid
  def fake_tg(conf,method,payload):
   if method=="getUpdates":return []
   raise ConnectionError("simulated Telegram network outage")
  with patch.object(w,"tg",side_effect=fake_tg):
   w.tick()
  with s.conn() as c:
   status=c.execute("SELECT done,attempts FROM outbox WHERE id=?",(rid,)).fetchone()
   self.assertEqual(tuple(status),(-1,16))
   self.assertTrue(s.settings(c)["last_error"])
  info=s.admin({"action":"get"})
  self.assertEqual(info["outbox_failed"][0]["id"],rid)
  self.assertEqual(info["outbox_failed"][0]["attempts"],16)
  s.admin({"action":"retry_outbox","id":rid})
  with s.conn() as c:
   self.assertEqual(tuple(c.execute("SELECT done,attempts,next_try FROM outbox WHERE id=?",(rid,)).fetchone()),(0,0,0))
   with self.assertRaises(ValueError):s.admin({"action":"retry_outbox","id":rid})
 def test_plan_create_edit_toggle_delete(self):
  with patch.object(s,"inbound_list",return_value=[{"id":7,"enable":True,"protocol":"vless"}]):
   with s.conn() as c:c.execute("DELETE FROM plans")
   v={"name":"Starter","category":"عمومی","protocol":"xray","price":45000,"days":30,
      "gb":40,"inbounds":[7],"capacity":2,"active":True}
   s.admin({"action":"plan","values":v})
   p=s.admin({"action":"get"})["plans"][0]
   self.assertEqual((p["sold"],p["remaining"],p["price"]),(0,2,45000))
   v.update(id=p["id"],name="Starter VIP",price=49000)
   s.admin({"action":"plan","values":v})
   self.assertEqual(s.admin({"action":"get"})["plans"][0]["name"],"Starter VIP")
   s.admin({"action":"plan_toggle","id":p["id"],"active":False})
   self.assertEqual(s.admin({"action":"get"})["plans"][0]["active"],0)
   s.admin({"action":"plan_delete","id":p["id"]})
   self.assertEqual(s.admin({"action":"get"})["plans"],[])
 def test_plan_capacity_and_sold_history(self):
  with patch.object(s,"inbound_list",return_value=[{"id":9,"enable":True,"protocol":"vless"}]):
   with s.conn() as c:c.execute("DELETE FROM plans")
   v={"name":"Capacity","category":"Public","protocol":"xray","price":1000,
     "days":10,"gb":3,"inbounds":[9],"capacity":1,"active":True}
   s.admin({"action":"plan","values":v})
   ident=s.admin({"action":"get"})["plans"][0]["id"]
   with s.conn() as c:
    c.execute("UPDATE users SET balance=100000 WHERE id=101")
    s.order(c,101,ident,"limited")
   p=s.admin({"action":"get"})["plans"][0]
   self.assertEqual((p["sold"],p["remaining"],p["pending"]),(1,0,1))
   with self.assertRaisesRegex(ValueError,"سابقه سفارش"):
    s.admin({"action":"plan_delete","id":ident})
   s.admin({"action":"plan_toggle","id":ident,"active":False})
   with s.conn() as c:
    with self.assertRaises(ValueError):s.order(c,102,ident,"disabled")
 def test_plan_validation_and_legacy(self):
  with patch.object(s,"inbound_list",return_value=[{"id":9,"enable":True,"protocol":"vless"}]):
   v={"name":"Xray","protocol":"xray","price":1000,"days":10,
      "gb":2,"inbounds":[99],"capacity":0,"active":True}
   with self.assertRaises(ValueError):s.admin({"action":"plan","values":v})
   with self.assertRaises(ValueError):s.admin({"action":"plan","values":dict(v,inbounds=[9,9])})
   with self.assertRaises(ValueError):s.admin({"action":"plan","values":dict(v,inbounds=[9],price="1000")})
   with self.assertRaises(ValueError):s.admin({"action":"plan","values":dict(v,protocol="openvpn",inbounds=[],gb=5)})
   with self.assertRaises(ValueError):s.admin({"action":"plan","values":dict(v,protocol="openconnect",inbounds=[9],gb=0)})
  with s.conn() as c:c.execute("DELETE FROM plans")
  s.admin({"action":"plan","values":{"name":"OVPN","protocol":"openvpn","price":0,
    "days":30,"gb":0,"inbounds":[],"capacity":0,"active":True}})
  p=s.admin({"action":"get"})["plans"][0]
  self.assertIsNone(p["remaining"])
if __name__=="__main__":unittest.main()
