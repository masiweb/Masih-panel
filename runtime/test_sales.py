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
   for t in ("social_reports","social_geo","social_blocks","social_chat_messages","social_chat_pairs","social_chat_queue","social_chat_invites","social_chat_quotes","social_discovery","social_profile_comments","social_reactions","social_profile_views","social_follows","social_profiles","media_items","playlists","playlist_likes","media_likes","settings","users","orders","plans","services","ledger","coupons","redeemed","outbox","topups","updates","payments","quotes","receipt_orders","message_templates","loyalty_points","ai_usage"):c.execute("DELETE FROM "+t)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(101,'test',100000,0)")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(102,'other',0,0)")
   c.execute("INSERT INTO plans(id,name,category,protocol,price,days,gb,inbounds,capacity,active) VALUES(1,'test','test','openvpn',10000,30,0,'[]',0,1)")
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
   s.put(c,"trial",True);c.execute("UPDATE plans SET is_trial=1,price=0 WHERE id=1");s.order(c,101,1,"trial",trial=True)
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
   q=w.quote(c,101,1,quantity=5,label='Wholesale');w.purchase_quote(c,101,q);w.purchase_quote(c,101,q)
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
   q=w.quote(c,102,1,label='Card order');w.purchase_quote(c,102,q,card=True)
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
   q=w.quote(c,102,1,label='Card reject');w.purchase_quote(c,102,q,card=True)
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
 def test_rejected_card_receipt_restores_reserved_coupon(self):
  with s.conn() as c:
   s.put(c,"card","test-card")
   c.execute("INSERT INTO coupons VALUES('SAVE20',20,0,1,0)")
   c.execute("UPDATE users SET state='coupon:SAVE20' WHERE id=101")
   s.order(c,101,1,"card-reject",card=True)
   o=c.execute("SELECT * FROM orders WHERE id='card-reject'").fetchone()
   self.assertEqual(o["price"],8000)
   self.assertEqual(json.loads(o["plan"])["_coupon_code"],"SAVE20")
   self.assertEqual(c.execute("SELECT uses FROM coupons WHERE code='SAVE20'").fetchone()[0],0)
   c.execute("UPDATE orders SET status='receipt_review' WHERE id='card-reject'")
   rid=c.execute("INSERT INTO topups(user_id,amount,receipt,created) VALUES(101,8000,'file',0)").lastrowid
   c.execute("INSERT INTO receipt_orders VALUES(?,?)",(rid,"card-reject"))
   s.decide_receipt(c,rid,False)
   self.assertEqual(c.execute("SELECT uses FROM coupons WHERE code='SAVE20'").fetchone()[0],1)
   self.assertEqual(c.execute("SELECT count(*) FROM redeemed WHERE code='SAVE20'").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT status FROM orders WHERE id='card-reject'").fetchone()[0],"cancelled")
 def test_admin_cancel_pending_order_and_refund_coupon(self):
  with s.conn() as c:
   c.execute("INSERT INTO coupons VALUES('SALE',10,0,1,0)")
   c.execute("UPDATE users SET state='coupon:SALE' WHERE id=101")
   s.order(c,101,1,"pending-card",card=True)
  s.admin({"action":"cancel_order","id":"pending-card"})
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT status FROM orders WHERE id='pending-card'").fetchone()[0],"cancelled")
   self.assertEqual(c.execute("SELECT uses FROM coupons WHERE code='SALE'").fetchone()[0],1)
   self.assertEqual(c.execute("SELECT count(*) FROM redeemed WHERE code='SALE'").fetchone()[0],0)
  with self.assertRaises(ValueError):s.admin({"action":"cancel_order","id":"pending-card"})
 def test_pending_receipt_blocks_second_renewal(self):
  with s.conn() as c:
   c.execute("INSERT INTO services VALUES('service',101,'openvpn','name','{}',1999999999,'{}',0)")
   s.order(c,101,1,"pending-renew",renew="service",card=True)
   with self.assertRaises(ValueError):
    s.order(c,101,1,"another-renew",renew="service",card=True)
 def test_trial_flag_migration_preserves_legacy_plans(self):
  with s.conn() as c:
   cols=[x[1] for x in c.execute("PRAGMA table_info(plans)")]
   self.assertIn("is_trial",cols)
   self.assertEqual(c.execute("SELECT is_trial FROM plans WHERE id=1").fetchone()[0],0)
 def test_free_trial_single_use_without_wallet_debit(self):
  with patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   s.admin({"action":"plan","values":{"name":"Free Trial","category":"test",
     "protocol":"xray","price":20000,"days":2,"gb":3,"inbounds":[1],
     "capacity":10,"active":True,"is_trial":True}})
  with s.conn() as c:
   pid=c.execute("SELECT id FROM plans WHERE is_trial=1").fetchone()[0]
   self.assertEqual(c.execute("SELECT price FROM plans WHERE id=?",(pid,)).fetchone()[0],0)
   s.put(c,"trial",True)
   original=c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0]
   self.assertEqual(original,0)
   w.handle(c,{"update_id":300,"callback_query":{"from":{"id":102},
     "message":{"chat":{"id":102,"type":"private"}},"data":"trialpick:"+str(pid)}})
   o=c.execute("SELECT * FROM orders WHERE user_id=102 AND trial=1").fetchone()
   self.assertIsNotNone(o)
   self.assertEqual((o["price"],o["status"]), (0,"paid"))
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],original)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ledger WHERE user_id=102").fetchone()[0],0)
   with self.assertRaises(ValueError):s.order(c,102,pid,"any:second",trial=True)
   with self.assertRaisesRegex(ValueError,"قبلاً تست رایگان"):
    w.handle(c,{"update_id":301,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"trialpick:"+str(pid)}})
   with self.assertRaises(ValueError):s.order(c,101,pid,"try:charged",trial=False,card=True)
 def test_trial_cannot_be_paid_or_quoted_even_when_its_price_is_zero(self):
  with patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   s.admin({"action":"plan","values":{"name":"Trial","protocol":"xray",
    "price":0,"days":1,"gb":1,"inbounds":[1],"capacity":0,"active":True,"is_trial":True}})
  with s.conn() as c:
   pid=c.execute("SELECT id FROM plans WHERE is_trial=1").fetchone()[0]
   with self.assertRaises(ValueError):w.quote(c,101,pid)
   with self.assertRaises(ValueError):s.order(c,101,pid,"paid-attempt",card=False)
   s.put(c,"trial",False)
   with self.assertRaises(ValueError):s.order(c,101,pid,"free-disabled",trial=True)
 def test_trial_plan_type_protected_after_order(self):
  with patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   v={"name":"Trial","protocol":"xray","price":0,"days":1,"gb":1,
      "inbounds":[1],"capacity":0,"active":True,"is_trial":True}
   s.admin({"action":"plan","values":v})
   with s.conn() as c:
    pid=c.execute("SELECT id FROM plans WHERE is_trial=1").fetchone()[0]
    s.put(c,"trial",True)
    s.order(c,101,pid,"trial:101",trial=True)
   with self.assertRaises(ValueError):
    s.admin({"action":"plan","values":dict(v,id=pid,is_trial=False)})
 def test_trial_visible_in_dedicated_menu_only(self):
  with patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   s.admin({"action":"plan","values":{"name":"Free Offer","protocol":"xray","price":0,
     "days":1,"gb":2,"inbounds":[1],"capacity":0,"active":True,"is_trial":True}})
  with s.conn() as c:
   s.put(c,"trial",True)
   w.handle(c,{"update_id":500,"message":{"from":{"id":102},
    "chat":{"type":"private","id":102},"text":"/start"}})
   out=[json.loads(row["payload"]) for row in c.execute("SELECT payload FROM outbox")]
   self.assertFalse(any("دریافت تست رایگان" in str(x) for x in out))
   w.handle(c,{"update_id":501,"callback_query":{"from":{"id":102},
    "message":{"chat":{"id":102,"type":"private"}},"data":"plans"}})
   out=[json.loads(row["payload"]) for row in c.execute("SELECT payload FROM outbox")]
   self.assertTrue(any("دریافت تست رایگان" in str(x) for x in out))
   self.assertFalse(any("Free Offer" in json.dumps(x) for x in out))
   w.handle(c,{"update_id":502,"callback_query":{"from":{"id":102},
    "message":{"chat":{"id":102,"type":"private"}},"data":"trial"}})
   out=[json.loads(row["payload"]) for row in c.execute("SELECT payload FROM outbox")]
   self.assertTrue(any("Free Offer" in json.dumps(x) for x in out))
 def test_checkout_requires_name_from_customer(self):
  with s.conn() as c:
   q=w.quote(c,101,1)
   with self.assertRaisesRegex(ValueError,"نام اشتراک"):w.purchase_quote(c,101,q)
   w.handle(c,{"update_id":814,"callback_query":{"from":{"id":101},
     "message":{"chat":{"id":101,"type":"private"}},"data":"choose:1"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"planname:1:1")
   w.handle(c,{"update_id":815,"message":{"from":{"id":101},
     "chat":{"id":101,"type":"private"},"text":"My Office"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"")
   q=c.execute("SELECT * FROM quotes WHERE user_id=101 ORDER BY rowid DESC LIMIT 1").fetchone()
   self.assertEqual(json.loads(q["plan"])["label"],"My Office")
   w.purchase_quote(c,101,q["id"])
   o=c.execute("SELECT * FROM orders WHERE user_id=101").fetchone()
   self.assertEqual(json.loads(o["plan"])["_display_name"],"My Office")
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],90000)
 def test_duplicate_label_random_suffix_and_casefold(self):
  with s.conn() as c:
   q1=w.quote(c,101,1,label="Office")
   q2=w.quote(c,102,1,label="office")
   a=json.loads(c.execute("SELECT plan FROM quotes WHERE id=?",(q1,)).fetchone()[0])["label"]
   b=json.loads(c.execute("SELECT plan FROM quotes WHERE id=?",(q2,)).fetchone()[0])["label"]
   self.assertEqual(a,"Office")
   self.assertRegex(b,r"^office-[a-z0-9]{5}$")
   c.execute("DELETE FROM quotes")
   c.execute("INSERT INTO services VALUES(?,?,?,?,?,?,?,0)",(
     "existing-service",101,"openvpn","ms-existing","{}",s.now()+5000,
     json.dumps({"label":"کار"})))
   q3=w.quote(c,102,1,label="کار")
   alias=json.loads(c.execute("SELECT plan FROM quotes WHERE id=?",(q3,)).fetchone()[0])["label"]
   self.assertRegex(alias,r"^کار-[a-z0-9]{5}$")
 def test_label_validation_and_quote_expiry(self):
  with s.conn() as c:
   for invalid in ["", "a", "hello/there", "x"*60, "line\\nbreak"]:
    with self.assertRaises(ValueError):w.quote(c,101,1,label=invalid)
   q=w.quote(c,101,1,label="Office")
   c.execute("UPDATE quotes SET expires=0 WHERE id=?",(q,))
   q2=w.quote(c,102,1,label="office")
   self.assertEqual(json.loads(c.execute("SELECT plan FROM quotes WHERE id=?",(q2,)).fetchone()[0])["label"],"office")
 def test_selected_name_kept_through_card_payment(self):
  with s.conn() as c:
   s.put(c,"card","test-card details")
   q=w.quote(c,102,1,label="Company VPN")
   w.purchase_quote(c,102,q,card=True)
   o=c.execute("SELECT * FROM orders WHERE user_id=102").fetchone()
   self.assertEqual((o["price"],o["status"]),(10000,"awaiting_receipt"))
   self.assertEqual(json.loads(o["plan"])["_display_name"],"Company VPN")
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],0)
 def test_unique_labels_for_bulk_purchases(self):
  with s.conn() as c:
   s.put(c,"bulk",True);s.put(c,"reseller",True)
   c.execute("UPDATE users SET reseller=1 WHERE id=101")
   q=w.quote(c,101,1,quantity=3,label="Bulk")
   w.purchase_quote(c,101,q)
   labels=[json.loads(x[0])["_display_name"] for x in c.execute("SELECT plan FROM orders ORDER BY id")]
   self.assertEqual(len(labels),3)
   self.assertEqual(len(set(labels)),3)
   self.assertIn("Bulk",labels)
   self.assertTrue(all(x=="Bulk" or __import__('re').fullmatch(r"Bulk-[a-z0-9]{5}",x) for x in labels))
 def test_discount_survives_checkout_name_prompt(self):
  with s.conn() as c:
   c.execute("INSERT INTO coupons VALUES('OFF20',20,0,3,0)")
   c.execute("UPDATE users SET state='coupon:OFF20' WHERE id=101")
   w.handle(c,{"update_id":920,"callback_query":{"from":{"id":101},
     "message":{"chat":{"id":101,"type":"private"}},"data":"choose:1"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"planname:1:1:OFF20")
   w.handle(c,{"update_id":921,"message":{"from":{"id":101},
    "chat":{"id":101,"type":"private"},"text":"پشتیبانی من"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"coupon:OFF20")
   q=c.execute("SELECT id FROM quotes WHERE user_id=101 ORDER BY rowid DESC LIMIT 1").fetchone()[0]
   w.purchase_quote(c,101,q)
   o=c.execute("SELECT * FROM orders WHERE user_id=101").fetchone()
   self.assertEqual(o["price"],8000)
   self.assertEqual(json.loads(o["plan"])["_display_name"],"پشتیبانی من")
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=101").fetchone()[0],92000)
 def test_renaming_preserves_unique_name(self):
  with s.conn() as c:
   c.execute("INSERT INTO services VALUES(?,?,?,?,?,?,?,0)",(
     "existing-one",101,"openvpn","ms-a","{}",s.now()+5000,json.dumps({"label":"Sales"})))
   c.execute("INSERT INTO services VALUES(?,?,?,?,?,?,?,0)",(
     "existing-two",102,"openvpn","ms-b","{}",s.now()+5000,json.dumps({"label":"Support"})))
   s.put(c,"rename",True)
   c.execute("UPDATE users SET state='rename:existing-two' WHERE id=102")
   w.handle(c,{"update_id":923,"message":{"from":{"id":102},
      "chat":{"id":102,"type":"private"},"text":"sales"}})
   name=json.loads(c.execute("SELECT data FROM services WHERE id='existing-two'").fetchone()[0])["label"]
   self.assertRegex(name,r"^sales-[a-z0-9]{5}$")
   self.assertNotEqual(name.casefold(),"sales")
 def test_name_persists_after_provision_and_renewal(self):
  with s.conn() as c:
   s.order(c,101,1,"new-service",label="My Company")
   def fake(req):
    return {"users":[]} if req["action"]=="list" else {}
   with patch.object(s,"vpn",side_effect=fake):
    s.provision(c,c.execute("SELECT * FROM orders WHERE id='new-service'").fetchone())
    c.commit()
    service=c.execute("SELECT * FROM services").fetchone()
    self.assertEqual(json.loads(service["data"])["label"],"My Company")
    s.order(c,101,1,"renew-service",renew=service["id"])
    s.provision(c,c.execute("SELECT * FROM orders WHERE id='renew-service'").fetchone())
   final=c.execute("SELECT * FROM services WHERE id=?",(service["id"],)).fetchone()
   self.assertEqual(json.loads(final["data"])["label"],"My Company")
   self.assertEqual(json.loads(final["plan"])["_display_name"],"My Company")
 def test_xray_provision_keeps_display_name_and_internal_email_separate(self):
  with s.conn() as c:
   c.execute("""INSERT INTO plans(id,name,category,protocol,price,days,gb,inbounds,capacity,active)
     VALUES(2,'Paid Xray','QA','xray',5000,30,5,'[7,8]',0,1)""")
   s.order(c,101,2,"named-xray",label="دفتر مرکزی")
   order=c.execute("SELECT * FROM orders WHERE id='named-xray'").fetchone()
   class FakePanel:
    def __init__(self):self.client=None;self.inbound_ids=[]
    def call(self,path,data=None):
     if path.startswith("/panel/api/clients/get/"):
      if not self.client:raise ValueError("not found")
      return {"client":self.client,"inboundIds":self.inbound_ids}
     if path=="/panel/api/clients/add":
      self.client=data["client"];self.inbound_ids=data["inboundIds"];return {}
     if path.startswith("/panel/api/clients/links/"):return ["vless://example#QA"]
     raise AssertionError("Unexpected endpoint "+path)
   panel=FakePanel()
   with patch.object(s,"Panel",return_value=panel), patch.object(s,"subscription_url",return_value="https://domain/sub/demo"):
    s.provision(c,order)
   record=c.execute("SELECT * FROM services WHERE user_id=101 AND protocol='xray'").fetchone()
   self.assertEqual(json.loads(record["data"])["label"],"دفتر مرکزی")
   self.assertEqual(panel.client["comment"],"Masiha sales | دفتر مرکزی")
   self.assertRegex(record["username"],r"^ms-[a-f0-9]{20}$")
   self.assertNotEqual(record["username"],"دفتر مرکزی")
   self.assertEqual(panel.inbound_ids,[7,8])
   self.assertEqual(c.execute("SELECT status FROM orders WHERE id='named-xray'").fetchone()[0],"fulfilled")
 def test_active_trial_plan_enables_trial_menu_automatically(self):
  with patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   s.admin({"action":"plan","values":{"name":"Free Sample","category":"test","protocol":"xray","price":4000,
      "days":2,"gb":1,"inbounds":[1],"capacity":10,"active":True,"is_trial":True}})
  with s.conn() as c:
   self.assertTrue(s.settings(c)["trial"])
   self.assertEqual(c.execute("SELECT price FROM plans WHERE is_trial=1").fetchone()[0],0)
   w.home(c,102)
   home_rows=[json.loads(r["payload"]) for r in c.execute("SELECT payload FROM outbox")]
   self.assertFalse(any("trial" in str(z) for z in home_rows))
   w.handle(c,{"update_id":827,"callback_query":{"from":{"id":102},
    "message":{"chat":{"id":102,"type":"private"}},"data":"plans"}})
   x=[json.loads(r["payload"]) for r in c.execute("SELECT payload FROM outbox")]
   self.assertTrue(any("trial" in str(z) for z in x))
 def test_admin_menu_allows_only_authorized_telegram_user(self):
  from sales_bot_admin import callback
  with s.conn() as c:
   s.put(c,"admins",[101])
   with self.assertRaisesRegex(ValueError,"مدیر"):
    callback(c,102,"a:settings")
   callback(c,101,"a:settings")
   self.assertTrue(c.execute("SELECT 1 FROM outbox WHERE chat=101").fetchone())
   callback(c,101,"a:toggle:trial")
   self.assertTrue(s.settings(c)["trial"])
   with self.assertRaises(ValueError):callback(c,101,"a:toggle:token")
 def test_admin_creates_free_trial_through_bot(self):
  from sales_bot_admin import callback,input_text
  with s.conn() as c, patch.object(s,"inbound_list",return_value=[{"id":1,"enable":True,"protocol":"vless"}]):
   s.put(c,"admins",[101])
   callback(c,101,"a:newplan")
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"a:newplan")
   input_text(c,101,"a:newplan","تست دوم | عمومی | xray | 999 | 1 | 2 | 5 | 1 | 1",8000)
   new=c.execute("SELECT * FROM plans WHERE is_trial=1").fetchone()
   self.assertTrue(new and new["active"])
   self.assertEqual(new["price"],0)
   self.assertTrue(s.settings(c)["trial"])
 def test_response_templates_are_web_only_and_placeholder_safe(self):
  s.admin({"action":"message_templates","values":{"trial_confirm":"تست رایگان شما پذیرفته شد"}})
  with s.conn() as c:
   self.assertEqual(s.bot_message(c,"trial_confirm"),"تست رایگان شما پذیرفته شد")
  data=s.admin({"action":"get"})
  self.assertEqual(data["message_templates"]["trial_confirm"],"تست رایگان شما پذیرفته شد")
  with self.assertRaises(ValueError):
   s.admin({"action":"message_templates","values":{"service_ready":"{name.__class__}"}})
  with self.assertRaises(ValueError):
   s.admin({"action":"message_templates","values":{"custom":"foo"}})
 def test_contact_permission_gate_and_self_contact(self):
  with s.conn() as c:
   s.put(c,"require_contact",True)
   w.handle(c,{"update_id":910,"message":{"from":{"id":102,"first_name":"Tester"},
     "chat":{"id":102,"type":"private"},"text":"/start"}})
   sent=[json.loads(x["payload"]) for x in c.execute("SELECT payload FROM outbox WHERE chat=102")]
   self.assertTrue(any("request_contact" in str(x) for x in sent))
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"")
   w.handle(c,{"update_id":911,"message":{"from":{"id":102,"first_name":"Tester"},
     "chat":{"id":102,"type":"private"},"contact":{"user_id":102,"phone_number":"+989121234567"}}})
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"+989121234567")
   self.assertTrue(any('"remove_keyboard": true' in x["payload"] for x in c.execute("SELECT payload FROM outbox WHERE chat=102")))
 def test_phone_gate_rejects_all_features_and_foreign_contacts(self):
  def msg(uid,**payload):
   return {"update_id":secrets.randbelow(100000),"message":{
    "chat":{"id":uid,"type":"private"},"from":{"id":uid,"first_name":"Existing"},
    **payload}}
  def callback(uid,data):
   return {"update_id":secrets.randbelow(100000),"callback_query":{
    "message":{"chat":{"id":uid,"type":"private"}},"from":{"id":uid},"data":data}}
  with s.conn() as c:
   s.put(c,"require_contact",True)
   for q in (msg(102,text="/start"),callback(102,"plans"),
             callback(102,"med:play:11"),callback(102,"trialpick:1"),
             msg(102,text="+989121234567"),
             msg(102,audio={"file_id":"XX","file_unique_id":"xx"}),
             msg(102,contact={"user_id":101,"phone_number":"+989121234567"}),
             msg(102,contact={"user_id":102,"phone_number":"not-a-phone"}),
             msg(102,contact={"user_id":102,"phone_number":"    "}),):
    w.handle(c,q)
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],0)
   prompts=[json.loads(x[0]) for x in c.execute(
     "SELECT payload FROM outbox WHERE chat=102 AND dedupe LIKE 'phone-gate:%'")]
   self.assertTrue(prompts)
   self.assertTrue(all(x["reply_markup"]["keyboard"][0][0]["request_contact"] for x in prompts))
   w.handle(c,msg(102,contact={"user_id":102,"phone_number":"+98 912-1234567"}))
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"+989121234567")
   self.assertTrue(any(json.loads(x[0]).get("reply_markup",{}).get("remove_keyboard")
       for x in c.execute("SELECT payload FROM outbox WHERE chat=102")))

 def test_phone_gate_also_applies_to_admin_and_existing_members(self):
  with s.conn() as c:
   s.put(c,"require_contact",True);s.put(c,"admins",[101])
   w.handle(c,{"update_id":6001,"callback_query":{
    "from":{"id":101},"message":{"chat":{"id":101,"type":"private"}},
    "data":"admin"}})
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=101").fetchone()[0],"")
   self.assertTrue(c.execute("SELECT 1 FROM outbox WHERE chat=101 AND dedupe LIKE 'phone-gate:%'").fetchone())
   self.assertFalse(c.execute("SELECT 1 FROM outbox WHERE chat=101 AND payload LIKE '%پنل مدیریت%'").fetchone())

 def test_activating_phone_gate_prompts_existing_members_once(self):
  with s.conn() as c:
   c.execute("UPDATE users SET phone=? WHERE id=101",("+989130001111",))
  s.admin({"action":"settings","values":{"require_contact":True}})
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'phone-gate:rollout:%'").fetchone()[0],1)
   target=c.execute("SELECT chat,payload FROM outbox WHERE dedupe LIKE 'phone-gate:rollout:%'").fetchone()
   self.assertEqual(target["chat"],102)
   self.assertIn("request_contact",target["payload"])
  s.admin({"action":"settings","values":{"require_contact":True}})
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'phone-gate:rollout:%'").fetchone()[0],1)
  s.admin({"action":"settings","values":{"require_contact":False}})
  s.admin({"action":"settings","values":{"require_contact":True}})
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'phone-gate:rollout:%'").fetchone()[0],2)

 def test_outbox_only_delivers_contact_challenge_before_verification(self):
  with s.conn() as c:
   s.put(c,"enabled",True);s.put(c,"require_contact",True)
   s.put(c,"contact_onboarding_rollout_v2",True)
   s.put(c,"bot_username","testbot")
   s.put(c,"notifications",False)
   s.put(c,"admins",[101])
   c.execute("INSERT INTO outbox(dedupe,chat,method,payload) VALUES(?,?,?,?)",
     ("media-broadcast:music:100:102",102,"sendAudio",
      json.dumps({"chat_id":102,"audio":"FILE_ID"})))
   s.prompt_for_own_contact(c,102,"phone-gate:rollout:one:102")
  calls=[]
  def mock_tg(settings,method,payload):
   calls.append((method,payload))
   if method=="getUpdates":return []
   return {"ok":True}
  with patch.object(w,"tg",side_effect=mock_tg):
   w.tick()
  self.assertFalse(any(method=="sendAudio" for method,payload in calls))
  self.assertTrue(any(method=="sendMessage" and payload.get("reply_markup",{}).get("keyboard") for method,payload in calls))
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT done FROM outbox WHERE dedupe='media-broadcast:music:100:102'").fetchone()[0],0)
   c.execute("UPDATE users SET phone=? WHERE id=102",("+989121234567",))
  calls.clear()
  with patch.object(w,"tg",side_effect=mock_tg):
   w.tick()
  self.assertTrue(any(method=="sendAudio" for method,payload in calls))
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT done FROM outbox WHERE dedupe='media-broadcast:music:100:102'").fetchone()[0],1)

 def test_admin_broadcast_requires_explicit_confirmation_and_can_cancel(self):
  from sales_bot_admin import callback,input_text
  with s.conn() as c:
   s.put(c,"admins",[101])
   callback(c,101,"a:broadcast")
   input_text(c,101,"a:broadcast","پیام آزمایشی مدیریت",102004)
   self.assertTrue(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0].startswith("a:broadcast-confirm:"))
   self.assertFalse(c.execute("SELECT 1 FROM outbox WHERE chat=102").fetchone())
   callback(c,101,"a:broadcastcancel")
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"")
   self.assertFalse(c.execute("SELECT 1 FROM outbox WHERE chat=102").fetchone())
 def test_admin_navigation_discards_pending_financial_input(self):
  from sales_bot_admin import callback,input_text
  with s.conn() as c:
   s.put(c,"admins",[101])
   callback(c,101,"a:credit:102")
   self.assertTrue(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0].startswith("a:credit:"))
   callback(c,101,"a:home")
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"")
   with self.assertRaises(ValueError):
    input_text(c,101,"","10000 | accidental",102005)
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],0)
 def test_normalize_public_channel_without_at_prefix(self):
  self.assertEqual(s.normalize_channel("masih_v2ray"),"@masih_v2ray")
  self.assertEqual(s.normalize_channel("@masih_v2ray"),"@masih_v2ray")
  self.assertEqual(s.normalize_channel("https://t.me/masih_v2ray"),"@masih_v2ray")
  with self.assertRaises(ValueError):
   s.normalize_channel("https://example.org/not-telegram")

 def test_unknown_channel_nonmember_returns_false_instead_of_bot_error(self):
  import io,urllib.error
  payload=json.dumps({"ok":False,"error_code":400,
       "description":"Bad Request: PARTICIPANT_ID_INVALID"}).encode()
  err=urllib.error.HTTPError("https://api.telegram.org/botREDACTED/getChatMember",
      400,"Bad Request",{},io.BytesIO(payload))
  with patch.object(s,"telegram_open",side_effect=err):
   self.assertFalse(s.telegram_channel_member({"token":"TEST_TOKEN"},"masih_v2ray",1234567890))

 def test_fresh_nonmember_gets_join_buttons_not_token_error(self):
  with s.conn() as c:
   s.put(c,"channel","masih_v2ray")
   with patch.object(w,"telegram_channel_member",return_value=False):
    w.handle(c,{"update_id":43210,"message":{
     "chat":{"id":98765432,"type":"private"},
     "from":{"id":98765432,"first_name":"new customer"},"text":"/start"}})
   self.assertTrue(c.execute("SELECT 1 FROM users WHERE id=98765432").fetchone())
   sent=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=98765432 ORDER BY id DESC LIMIT 1").fetchone()["payload"])
   self.assertIn("عضو شوید",sent["text"])
   buttons=[b for row in sent["reply_markup"]["inline_keyboard"] for b in row]
   self.assertTrue(any(b.get("url")=="https://t.me/masih_v2ray" for b in buttons))
   self.assertTrue(any(b.get("callback_data")=="home" for b in buttons))

 def test_member_gets_normal_home_after_check(self):
  with s.conn() as c:
   s.put(c,"channel","@masih_v2ray")
   s.put(c,"trial",True)
   c.execute("UPDATE plans SET is_trial=1,price=0 WHERE id=1")
   with patch.object(w,"telegram_channel_member",return_value=True):
    w.handle(c,{"update_id":43211,"message":{
     "chat":{"id":98765432,"type":"private"},
     "from":{"id":98765432,"first_name":"new customer"},"text":"/start"}})
   sent=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=98765432 ORDER BY id DESC LIMIT 1").fetchone()["payload"])
   buttons=[b.get("callback_data") for row in sent["reply_markup"]["inline_keyboard"] for b in row]
   self.assertNotIn("trial",buttons)
   self.assertIn("plans",buttons)
   with patch.object(w,"telegram_channel_member",return_value=True):
    w.handle(c,{"update_id":43212,"callback_query":{
     "from":{"id":98765432},"message":{"chat":{"id":98765432,"type":"private"}},"data":"plans"}})
   latest=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=98765432 ORDER BY id DESC LIMIT 1").fetchone()["payload"])
   self.assertIn("trial",[b["callback_data"] for row in latest["reply_markup"]["inline_keyboard"] for b in row])

 def test_save_channel_normalizes_and_verifies_bot_is_admin(self):
  with s.conn() as c:
   s.put(c,"enabled",True)
   s.put(c,"token","123456:"+("x"*30))
   s.put(c,"admins",[101])
   def fake_tg(settings,method,data):
    if method=="getChat":
     self.assertEqual(data["chat_id"],"@masih_v2ray")
     return {"type":"channel"}
    if method=="getMe":return {"id":900,"username":"test_bot"}
    if method=="getChatMember":return {"status":"administrator"}
    if method=="deleteWebhook":return True
    raise AssertionError(method)
   with patch.object(s,"tg",side_effect=fake_tg):
    s.save_settings(c,{"channel":"masih_v2ray"})
   self.assertEqual(s.settings(c)["channel"],"@masih_v2ray")
 def test_purchase_points_awarded_only_once_on_fulfillment(self):
  import gamification as loyalty
  with s.conn() as c:
   s.put(c,"points_per_toman",5000);s.put(c,"points_per_purchase_unit",3)
   s.order(c,101,1,"loyal-paid");c.commit()
   def fake(req):
    if req["action"]=="list":return {"users":[]}
    return {}
   with patch.object(s,"vpn",side_effect=fake):
    o=c.execute("SELECT * FROM orders WHERE id='loyal-paid'").fetchone()
    s.provision(c,o)
    c.commit()
    self.assertEqual(loyalty.balance(c,101),6)
    s.provision(c,c.execute("SELECT * FROM orders WHERE id='loyal-paid'").fetchone())
    self.assertEqual(loyalty.balance(c,101),6)
    self.assertEqual(c.execute("SELECT COUNT(*) FROM loyalty_points WHERE kind='purchase'").fetchone()[0],1)

 def test_free_trial_does_not_reward_purchase_points(self):
  import gamification as loyalty
  with s.conn() as c:
   s.put(c,"trial",True);c.execute("UPDATE plans SET is_trial=1,price=0 WHERE id=1")
   s.order(c,101,1,"trial-no-points",trial=True);c.commit()
   def fake(req):
    if req["action"]=="list":return {"users":[]}
    return {}
   with patch.object(s,"vpn",side_effect=fake):
    s.provision(c,c.execute("SELECT * FROM orders WHERE id='trial-no-points'").fetchone())
   self.assertEqual(loyalty.balance(c,101),0)

 def test_social_invitation_survives_mandatory_phone_registration(self):
  import social
  with s.conn() as c:
   s.put(c,"require_contact",True)
   sender=social.profile(c,101)
   c.execute("UPDATE social_profiles SET display_name='دعوت‌کننده',visibility='public' WHERE user_id=101")
   link="/start soc_"+sender["code"]
   w.handle(c,{"update_id":99001,"message":{
    "from":{"id":105,"first_name":"NewGuest"},
    "chat":{"id":105,"type":"private"},"text":link}})
   self.assertTrue(c.execute("SELECT state FROM users WHERE id=105").fetchone()[0].startswith("soc:pendinginvite:"))
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=105").fetchone()[0],"")
   w.handle(c,{"update_id":99002,"message":{
    "from":{"id":105,"first_name":"NewGuest"},
    "chat":{"id":105,"type":"private"},
    "contact":{"user_id":105,"phone_number":"+989121111111"}}})
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=105").fetchone()[0],"+989121111111")
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=105").fetchone()[0],"")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM social_profile_views WHERE owner=101 AND viewer=105").fetchone()[0],1)
   latest=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=105 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn(sender["code"],latest["text"])
   self.assertNotIn("user_id",latest["text"])

 def test_referral_url_uses_public_opaque_code_and_not_telegram_id(self):
  with s.conn() as c:
   s.put(c,"bot_username","test_social_bot")
   s.put(c,"referrals",True)
   w.handle(c,{"update_id":50001,"message":{
      "from":{"id":101,"first_name":"Sender"},
      "chat":{"id":101,"type":"private"},"text":"/start"}})
   w.handle(c,{"update_id":50002,"callback_query":{
      "from":{"id":101},"message":{"chat":{"id":101,"type":"private"}},
      "data":"referral"}})
   from social import code
   opaque=code(c,101)
   payload=c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0]
   self.assertIn("ref_"+opaque,payload)
   self.assertNotIn("ref_101",payload)
   w.handle(c,{"update_id":50003,"message":{
      "from":{"id":144,"first_name":"Invitee"},
      "chat":{"id":144,"type":"private"},"text":"/start ref_"+opaque}})
   self.assertEqual(c.execute("SELECT referrer FROM users WHERE id=144").fetchone()[0],101)

 def test_referral_reward_once_after_verified_new_user_onboarding(self):
  import gamification as loyalty
  with s.conn() as c:
   s.put(c,"points_per_referral",25)
   upd={"update_id":7601,"message":{"from":{"id":104,"first_name":"Invitee"},
       "chat":{"id":104,"type":"private"},"text":"/start ref_101"}}
   w.handle(c,upd)
   self.assertEqual(loyalty.balance(c,101),25)
   w.handle(c,{"update_id":7602,"message":{"from":{"id":104,"first_name":"Invitee"},
       "chat":{"id":104,"type":"private"},"text":"/start"}})
   self.assertEqual(loyalty.balance(c,101),25)

 def test_loyalty_referral_waits_for_own_contact_when_required(self):
  import gamification as loyalty
  with s.conn() as c:
   s.put(c,"require_contact",True);s.put(c,"points_per_referral",40)
   w.handle(c,{"update_id":7603,"message":{"from":{"id":104,"first_name":"Invitee"},
     "chat":{"id":104,"type":"private"},"text":"/start ref_101"}})
   self.assertEqual(loyalty.balance(c,101),0)
   w.handle(c,{"update_id":7604,"message":{"from":{"id":104,"first_name":"Invitee"},
     "chat":{"id":104,"type":"private"},"contact":{"user_id":104,"phone_number":"+989120000001"}}})
   self.assertEqual(loyalty.balance(c,101),40)
   w.handle(c,{"update_id":7605,"message":{"from":{"id":104,"first_name":"Invitee"},
     "chat":{"id":104,"type":"private"},"text":"/start"}})
   self.assertEqual(loyalty.balance(c,101),40)

 def test_ai_success_charges_points_once_and_failure_does_not_charge(self):
  import gamification as loyalty, entertainment as fun
  with s.conn() as c:
   loyalty.award(c,101,20,"test","manual-award",s.now())
   s.put(c,"ai_enabled",True);s.put(c,"ai_api_url","https://provider.example/v1")
   s.put(c,"ai_api_key","secret-test-key-never-send");s.put(c,"ai_model","sandbox")
   s.put(c,"ai_cost",5)
   with patch.object(loyalty,"query_ai",side_effect=ValueError("provider unavailable")):
    with self.assertRaises(ValueError):fun.ai_answer(c,101,"test question",55001)
   self.assertEqual(loyalty.balance(c,101),20)
   with patch.object(loyalty,"query_ai",return_value="پاسخ نمونه") as api:
    fun.ai_answer(c,101,"hello",55002)
    self.assertEqual(loyalty.balance(c,101),15)
    fun.ai_answer(c,101,"hello",55002)
    self.assertEqual(loyalty.balance(c,101),15)
    self.assertEqual(api.call_count,1)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0],1)

 def test_admin_user_photo_and_subscription_buttons(self):
  import sales_bot_admin as adm
  with s.conn() as c:
   s.put(c,"admins",[101])
   with patch.object(adm,"tg",return_value={"photos":[[{"file_id":"photo-A"}]]}):
    adm.callback(c,101,"a:user:102")
   photo=c.execute("SELECT payload FROM outbox WHERE chat=101 AND method='sendPhoto'").fetchone()
   self.assertEqual(json.loads(photo[0])["photo"],"photo-A")
   user_card=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 AND method='sendMessage' ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("امتیاز",user_card["text"])
   callbacks=[b.get("callback_data") for row in user_card["reply_markup"]["inline_keyboard"] for b in row]
   self.assertIn("a:usubs:102",callbacks)
   c.execute("INSERT INTO services VALUES(?,?,?,?,?,?,?,0)",
      ("test-sub-02",102,"xray","ms-sub",json.dumps({"name":"gold"}),
       s.now()+86400,json.dumps({"label":"اشتراک طلایی"})))
   adm.callback(c,101,"a:usubs:102")
   x=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 AND method='sendMessage' ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("a:usub:test-sub-02",[b["callback_data"] for row in x["reply_markup"]["inline_keyboard"] for b in row])

 def test_complete_telegram_admin_profile_and_submenus(self):
  import sales_bot_admin as adm
  import social
  with s.conn() as c:
   s.put(c,"admins",[101])
   pr=social.profile(c,102)
   with patch.object(adm,"tg",return_value={"photos":[]}):
    adm.callback(c,101,"a:user:102")
   card=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("پروفایل کلاب ناشناس",card["text"])
   self.assertIn("ناقص",card["text"])
   self.assertIn(pr["code"],card["text"])
   keys=[b["callback_data"] for row in card["reply_markup"]["inline_keyboard"] for b in row]
   for required in ("a:clubprofile:102","a:clubgallery:102","a:clubmedia:102",
                    "a:clubchats:102","a:clubhistory:102","a:phoneprompt:102",
                    "soc:adjust:points_add:"+pr["code"],"soc:adjust:wallet_sub:"+pr["code"]):
    self.assertIn(required,keys)
   adm.callback(c,101,"a:clubprofile:102")
   detail=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("فیلدهای ناقص",detail["text"])
   adm.callback(c,101,"a:clubmedia:102")
   adm.callback(c,101,"a:clubchats:102")
   adm.callback(c,101,"a:clubhistory:102")
   for route in ("a:clubprofile:102","a:clubchats:102","a:phoneprompt:102"):
    with self.assertRaises(ValueError):adm.callback(c,102,route)

 def test_admin_phone_request_queues_own_contact_only_not_arbitrary_number(self):
  import sales_bot_admin as adm
  with s.conn() as c:
   s.put(c,"admins",[101]);s.put(c,"require_contact",True)
   adm.callback(c,101,"a:phoneprompt:102")
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"")
   msg=c.execute("SELECT payload FROM outbox WHERE chat=102 AND dedupe LIKE 'phone-gate:admin-request:%'").fetchone()
   self.assertIsNotNone(msg)
   keyboard=json.loads(msg["payload"])["reply_markup"]["keyboard"]
   self.assertTrue(keyboard[0][0]["request_contact"])
   adm.callback(c,101,"a:phoneprompt:102")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE chat=102 AND dedupe LIKE 'phone-gate:admin-request:%'").fetchone()[0],2)
  result=s.admin({"action":"social_request_contact","id":102})
  self.assertTrue(result["requested"])
  with s.conn() as c:
   self.assertEqual(c.execute("SELECT phone FROM users WHERE id=102").fetchone()[0],"")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE chat=102 AND dedupe LIKE 'phone-gate:admin-request:%'").fetchone()[0],3)
   c.execute("UPDATE users SET phone=? WHERE id=102",("+989121234567",))
  with self.assertRaises(ValueError):s.admin({"action":"social_request_contact","id":102})

 def test_voice_sent_without_pressing_search_enqueues_music_lookup(self):
  import audio_fingerprint as fingerprint
  from tempfile import TemporaryDirectory
  with TemporaryDirectory() as root, s.conn() as c:
   from pathlib import Path
   db=Path(root)/"fp.pklz";db.write_bytes(b"dummy fingerprint db")
   with patch.object(fingerprint,"DATABASE",db),patch.object(fingerprint,"available",return_value=True):
    w.handle(c,{"update_id":410020,"message":{
     "from":{"id":102,"first_name":"Listener"},
     "chat":{"id":102,"type":"private"},
     "voice":{"file_id":"VALID_VOICE_FILE_ID_XYZ","duration":14,"file_size":60000}}})
   row=c.execute("SELECT state,file_id,user_id FROM audio_fp_queries WHERE update_id=410020").fetchone()
   self.assertEqual(tuple(row),("queued","VALID_VOICE_FILE_ID_XYZ",102))
   latest=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("شناسایی صوتی",latest["text"])

 def test_default_navigation_buttons_available_in_every_message(self):
  with s.conn() as c:
   s.enqueue(c,101,"standard reply")
   p=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("home",[b["callback_data"] for row in p["reply_markup"]["inline_keyboard"] for b in row])
   c.execute("UPDATE users SET state='a:credit:102' WHERE id=101")
   s.enqueue(c,101,"amount please")
   p=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("a:home",[b["callback_data"] for row in p["reply_markup"]["inline_keyboard"] for b in row])

 def test_ai_secret_masked_in_web_admin(self):
  with s.conn() as c:s.put(c,"ai_api_key","TEST_ONLY_SECRET")
  view=s.admin({"action":"get"})
  self.assertNotIn("ai_api_key",view["settings"])
  self.assertTrue(view["settings"]["ai_api_key_set"])
 def test_telegram_transport_prefers_ipv4_on_mixed_dns(self):
  import socket
  from unittest.mock import Mock
  ipv6=(socket.AF_INET6,socket.SOCK_STREAM,6,"",("2001:db8::1",443,0,0))
  ipv4=(socket.AF_INET,socket.SOCK_STREAM,6,"",("149.154.166.110",443))
  sock=Mock()
  with patch.object(s.socket,"getaddrinfo",return_value=[ipv6,ipv4]),patch.object(s.socket,"socket",return_value=sock) as opened:
   result=s._telegram_connect(("api.telegram.org",443),timeout=3)
  self.assertIs(result,sock)
  self.assertEqual(opened.call_args.args[0],socket.AF_INET)
  sock.connect.assert_called_once_with(ipv4[4])

 def test_telegram_transport_falls_back_to_ipv6_if_ipv4_fails(self):
  import socket
  from unittest.mock import Mock
  ipv6=(socket.AF_INET6,socket.SOCK_STREAM,6,"",("2001:db8::1",443,0,0))
  ipv4=(socket.AF_INET,socket.SOCK_STREAM,6,"",("149.154.166.110",443))
  bad=Mock();bad.connect.side_effect=OSError("IPv4 unreachable")
  good=Mock()
  with patch.object(s.socket,"getaddrinfo",return_value=[ipv6,ipv4]),patch.object(s.socket,"socket",side_effect=[bad,good]) as opened:
   result=s._telegram_connect(("api.telegram.org",443),timeout=3)
  self.assertIs(result,good)
  bad.close.assert_called_once()
  self.assertEqual(opened.call_args_list[0].args[0],socket.AF_INET)
  self.assertEqual(opened.call_args_list[1].args[0],socket.AF_INET6)

 def test_telegram_http_opener_rejects_non_telegram_hosts(self):
  import urllib.request
  with self.assertRaises(ValueError):
   s.telegram_open(urllib.request.Request("https://api.telegram.org.evil.example/test"))
  with self.assertRaises(ValueError):
   s.telegram_open(urllib.request.Request("http://api.telegram.org/test"))

 def test_tg_reuses_fast_telegram_transport_for_safe_calls(self):
  import io
  with patch.object(s,"telegram_open",return_value=io.BytesIO(b'{"ok":true,"result":{"is_bot":true}}')) as opener:
   self.assertEqual(s.tg({"token":"DUMMY_TOKEN"},"getMe",{}),{"is_bot":True})
   self.assertEqual(opener.call_count,1)

 def test_order_receipt_photo_first_then_buyer_and_service_with_actions(self):
  import sales_bot_admin as adm
  with s.conn() as c:
   s.put(c,"admins",[102])
   c.execute("UPDATE users SET phone=?, telegram_username=? WHERE id=101",
             ("+989121234567","sample_buyer"))
   ref="quote:test-photo-receipt:0"
   s.order(c,101,1,ref,card=True,label="سرویس من")
   c.execute("UPDATE users SET state=? WHERE id=101",("orderreceipt:"+ref,))
   w.handle(c,{"update_id":65001,"message":{
    "chat":{"id":101,"type":"private"},"from":{"id":101,"first_name":"test","username":"sample_buyer"},
    "photo":[{"file_id":"receipt-photo","file_unique_id":"receipt-unique"}]}})
   items=c.execute("SELECT method,payload FROM outbox WHERE chat=102 ORDER BY id").fetchall()
   self.assertEqual([r["method"] for r in items],["sendPhoto","sendMessage"])
   picture,card=[json.loads(r["payload"]) for r in items]
   self.assertEqual(picture["photo"],"receipt-photo")
   self.assertNotIn("reply_markup",picture)
   self.assertIn("test",card["text"])
   self.assertIn("+989121234567",card["text"])
   self.assertIn("@sample_buyer",card["text"])
   self.assertIn("شناسه عددی: 101",card["text"])
   self.assertIn("سرویس من",card["text"])
   self.assertIn("پلن: test",card["text"])
   self.assertIn("مدت: 30 روز",card["text"])
   self.assertIn("مبلغ: 10,000 تومان",card["text"])
   callbacks={b["callback_data"] for row in card["reply_markup"]["inline_keyboard"] for b in row}
   self.assertIn("approve:1",callbacks)
   self.assertIn("reject:1",callbacks)
   self.assertEqual(c.execute("SELECT status FROM orders WHERE id=?",(ref,)).fetchone()[0],"receipt_review")
   # Reopen from admin receipt list uses the same ordering and card.
   adm.callback(c,102,"a:receipt:1")
   new=c.execute("SELECT method,payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 2").fetchall()
   self.assertEqual([x["method"] for x in reversed(new)],["sendPhoto","sendMessage"])
   self.assertEqual(c.execute("SELECT status FROM topups WHERE id=1").fetchone()[0],"pending")

 def test_wallet_receipt_image_before_details_and_no_service(self):
  with s.conn() as c:
   s.put(c,"admins",[102])
   s.put(c,"wallet",True);s.put(c,"card","کارت تست")
   c.execute("UPDATE users SET phone=?,telegram_username=? WHERE id=101",("09121112233",""))
   c.execute("UPDATE users SET state='receipt:25000' WHERE id=101")
   w.handle(c,{"update_id":65002,"message":{
    "chat":{"id":101,"type":"private"},"from":{"id":101,"first_name":"customer"},
    "photo":[{"file_id":"topup-photo","file_unique_id":"topup-unique"}]}})
   items=c.execute("SELECT method,payload FROM outbox WHERE chat=102 ORDER BY id").fetchall()
   self.assertEqual([r["method"] for r in items],["sendPhoto","sendMessage"])
   card=json.loads(items[1]["payload"])
   self.assertIn("شارژ کیف پول",card["text"])
   self.assertIn("ثبت نشده",card["text"])
   self.assertIn("25,000 تومان",card["text"])
   self.assertIn("📱 موبایل: 09121112233",card["text"])
   self.assertNotIn("پلن:",card["text"])
   self.assertEqual(c.execute("SELECT status FROM topups").fetchone()[0],"pending")

 def test_receipt_text_fallback_and_admin_only(self):
  import sales_bot_admin as adm
  with s.conn() as c:
   s.put(c,"admins",[102])
   c.execute("INSERT INTO topups(id,user_id,amount,receipt,created) VALUES(1,101,35000,?,0)",
             ("tracking-987",))
   with self.assertRaises(ValueError):
    adm.queue_receipt_review(c,101,1)
   adm.queue_receipt_review(c,102,1)
   rows=c.execute("SELECT method,payload FROM outbox WHERE chat=102 ORDER BY id").fetchall()
   self.assertEqual([r["method"] for r in rows],["sendMessage","sendMessage"])
   self.assertIn("tracking-987",json.loads(rows[0]["payload"])["text"])
   self.assertNotIn("reply_markup",json.loads(rows[0]["payload"]))
   self.assertIn("approve:1",str(rows[1]["payload"]))
 def test_new_home_menu_exact_order_and_admin_only(self):
  expected=[
    ["plans","services"],["fun:ai"],["fun:music","fun:film"],
    ["soc:home"],["points_discount"],["fun","support"],["wallet"]
  ]
  with s.conn() as c:
   s.put(c,"admins",[101])
   w.home(c,102)
   first=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   layout=[[b["callback_data"] for b in row] for row in first["reply_markup"]["inline_keyboard"]]
   self.assertEqual(layout,expected)
   w.home(c,101)
   second=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=101 ORDER BY id DESC LIMIT 1").fetchone()[0])
   admin_layout=[[b["callback_data"] for b in row] for row in second["reply_markup"]["inline_keyboard"]]
   self.assertEqual(admin_layout,expected+[["admin"]])
   self.assertNotIn("admin",str(first))
   self.assertIn("🛒 خرید جدید",first["reply_markup"]["inline_keyboard"][0][0]["text"])

 def test_points_discounts_only_current_unredeemed_and_methods(self):
  import gamification
  with s.conn() as c:
   s.put(c,"discounts",True)
   s.put(c,"points_per_toman",100000)
   s.put(c,"points_per_purchase_unit",10)
   s.put(c,"points_per_referral",20)
   gamification.award(c,102,45,"test","test:points",s.now())
   c.execute("INSERT INTO coupons VALUES('ACTIVE20',20,0,2,0)")
   c.execute("INSERT INTO coupons VALUES('EXPIRED10',10,0,2,1)")
   c.execute("INSERT INTO coupons VALUES('USED15',15,0,2,0)")
   c.execute("INSERT INTO coupons VALUES('ZERO10',10,0,0,0)")
   c.execute("INSERT INTO redeemed VALUES('USED15',102)")
   w.handle(c,{"update_id":91011,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"points_discount"}})
   item=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("ACTIVE20",item["text"])
   self.assertLess(item["text"].index("ACTIVE20"),item["text"].index("روش‌های کسب امتیاز"))
   self.assertIn("45",item["text"])
   self.assertIn("100,000",item["text"])
   for banned in ("EXPIRED10","USED15","ZERO10"):self.assertNotIn(banned,item["text"])
   buttons={b["callback_data"] for r in item["reply_markup"]["inline_keyboard"] for b in r}
   self.assertTrue({"coupon","referral","fun:points","home"}<=buttons)

 def test_wallet_amount_first_card_approval_and_no_early_balance_credit(self):
  with s.conn() as c:
   s.put(c,"wallet",True);s.put(c,"card","6037-9999 TEST");s.put(c,"admins",[101])
   w.handle(c,{"update_id":91012,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"wallet"}})
   overview=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("موجودی: 0 تومان",overview["text"])
   self.assertIn("walletadd",str(overview))
   w.handle(c,{"update_id":91013,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletadd"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"wallet_amount")
   w.handle(c,{"update_id":91014,"message":{"from":{"id":102},"chat":{"id":102,"type":"private"},"text":"35,000"}})
   options=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("walletcard:35000",str(options))
   self.assertNotIn("6037-9999",options["text"])
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],0)
   w.handle(c,{"update_id":91015,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletcard:35000"}})
   card=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("عکس یا فایل رسید",card["text"])
   last_two=list(reversed(c.execute(
     "SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 2").fetchall()))
   first=json.loads(last_two[0]["payload"])
   self.assertIn("6037-9999",first["text"])
   self.assertNotIn("عکس یا فایل رسید",first["text"])
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"receipt:35000")
   w.handle(c,{"update_id":91016,"message":{"from":{"id":102},
      "chat":{"id":102,"type":"private"},"document":{"file_id":"RECEIPT_DOC"}}})
   topup=c.execute("SELECT * FROM topups WHERE user_id=102").fetchone()
   self.assertEqual((topup["amount"],topup["receipt"],topup["status"]),(35000,"document:RECEIPT_DOC","pending"))
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],0)
   s.decide_receipt(c,topup["id"],True)
   self.assertEqual(c.execute("SELECT balance FROM users WHERE id=102").fetchone()[0],35000)

 def test_wallet_disallowed_callbacks_and_method_gate(self):
  with s.conn() as c:
   s.put(c,"wallet",True);s.put(c,"card","Card 123");s.put(c,"zarinpal",False)
   with self.assertRaisesRegex(ValueError,"ابتدا مبلغ"):
    w.handle(c,{"update_id":91017,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletcard:25000"}})
   w.handle(c,{"update_id":91018,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletadd"}})
   w.handle(c,{"update_id":91019,"message":{"from":{"id":102},
      "chat":{"id":102,"type":"private"},"text":"25000"}})
   item=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertNotIn("walletonline",str(item))
   w.handle(c,{"update_id":91020,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"wallet"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"")
   with self.assertRaisesRegex(ValueError,"ابتدا مبلغ"):
    w.handle(c,{"update_id":91021,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletcard:25000"}})

 def test_wallet_zarinpal_only_after_amount_choice(self):
  with s.conn() as c:
   s.put(c,"wallet",True);s.put(c,"card","");s.put(c,"zarinpal",True);s.put(c,"merchant_id","a"*36)
   w.handle(c,{"update_id":91022,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletadd"}})
   w.handle(c,{"update_id":91023,"message":{"from":{"id":102},
      "chat":{"id":102,"type":"private"},"text":"12000"}})
   item=json.loads(c.execute("SELECT payload FROM outbox WHERE chat=102 ORDER BY id DESC LIMIT 1").fetchone()[0])
   self.assertIn("walletonline:12000",str(item))
   self.assertNotIn("walletcard:",str(item))
   with patch.object(w,"payment_create",return_value="A"*36) as payment:
    w.handle(c,{"update_id":91024,"callback_query":{"from":{"id":102},
      "message":{"chat":{"id":102,"type":"private"}},"data":"walletonline:12000"}})
   self.assertEqual(payment.call_args.args[1:3],(102,12000))
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"")

 def test_navigation_away_from_wallet_amount_cancels_pending_input(self):
  with s.conn() as c:
   s.put(c,"wallet",True);s.put(c,"card","Card test")
   w.handle(c,{"update_id":92001,"callback_query":{"from":{"id":102},
     "message":{"chat":{"id":102,"type":"private"}},"data":"walletadd"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"wallet_amount")
   w.handle(c,{"update_id":92002,"callback_query":{"from":{"id":102},
     "message":{"chat":{"id":102,"type":"private"}},"data":"fun:music"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"")
   w.handle(c,{"update_id":92003,"callback_query":{"from":{"id":102},
     "message":{"chat":{"id":102,"type":"private"}},"data":"fun:ai"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=102").fetchone()[0],"")

 def test_coupon_keeps_selected_code_when_opening_new_purchase_menu(self):
  with s.conn() as c:
   c.execute("INSERT INTO coupons VALUES('SALE10',10,0,3,0)")
   c.execute("UPDATE users SET state='coupon:SALE10' WHERE id=101")
   w.handle(c,{"update_id":92004,"callback_query":{"from":{"id":101},
      "message":{"chat":{"id":101,"type":"private"}},"data":"plans"}})
   self.assertEqual(c.execute("SELECT state FROM users WHERE id=101").fetchone()[0],"coupon:SALE10")

if __name__=="__main__":unittest.main()
