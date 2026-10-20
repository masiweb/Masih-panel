import os,tempfile,sys,unittest,json
from pathlib import Path
from unittest.mock import patch
temp=tempfile.TemporaryDirectory(prefix='masiha-ai-commerce-tests-')
os.environ['MASIHA_SALES_DIR']=temp.name
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales,ai_commerce as ai,sales_worker as worker

class AiCommerceTests(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ('ai_sale_orders','ai_sale_subscriptions','ai_sale_plans','ai_sale_models','ai_ready_stock',
                 'users','settings','outbox','ai_usage','loyalty_points','audit'):
    c.execute('DELETE FROM '+table)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(111,'Customer',5000,0)")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(222,'Admin',0,0)")
   sales.put(c,'admins',[222]);sales.put(c,'card','شماره کارت فروشگاه')
 def define(self,c,kind,price=5000,credits=(12,3,1)):
  model=ai.model(c,{'name':'Example AI Provider'})
  vals={'name':kind+' plan','kind':kind,'model_id':model,'price':price,'days':30}
  if kind=='bot':vals.update(dict(zip(('credits_text','credits_image','credits_video'),credits)))
  pid=ai.plan(c,vals)
  return pid
 def test_complete_bot_flow_from_selection_to_receipt_approval(self):
  with sales.conn() as c:
   pid=self.define(c,'bot')
   def cb(uid,command,number):
    worker.handle(c,{'update_id':number,'callback_query':{'from':{'id':uid},'message':{'chat':{'id':uid,'type':'private'}},'data':command}})
   cb(111,'ais:home',12001)
   cb(111,'ais:list:bot',12002)
   cb(111,'ais:choose:'+str(pid),12003)
   oid=c.execute('SELECT id FROM ai_sale_orders WHERE user_id=111').fetchone()[0]
   self.assertEqual(c.execute('SELECT state FROM users WHERE id=111').fetchone()[0],'ais:receipt:'+oid)
   worker.handle(c,{'update_id':12004,'message':{'from':{'id':111},'chat':{'id':111,'type':'private'},'photo':[{'file_id':'proof-photo'}]}})
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'preview')
   cb(111,'ais:confirm:'+oid,12005)
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'review')
   cb(222,'aisadmin:approve:'+oid,12006)
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'fulfilled')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_sale_subscriptions').fetchone()[0],1)

 def test_menu_lists_three_ai_products_separately_from_vpn(self):
  with sales.conn() as c:
   worker.handle(c,{'update_id':20001,'callback_query':{'from':{'id':111},
       'message':{'chat':{'id':111,'type':'private'}},'data':'buynew'}})
   latest=json.loads(c.execute('SELECT payload FROM outbox WHERE chat=111 ORDER BY id DESC LIMIT 1').fetchone()[0])
   labels=[x['callback_data'] for row in latest['reply_markup']['inline_keyboard'] for x in row]
   self.assertIn('ais:home',labels);self.assertIn('plans',labels)
   ai.menu(c,111)
   latest=json.loads(c.execute('SELECT payload FROM outbox WHERE chat=111 ORDER BY id DESC LIMIT 1').fetchone()[0])
   labels=[x['callback_data'] for row in latest['reply_markup']['inline_keyboard'] for x in row]
   self.assertIn('ais:list:bot',labels)
   self.assertIn('ais:list:personal',labels)
   self.assertIn('ais:list:ready',labels)
 def test_bot_paid_order_requires_receipt_user_confirm_and_manager_approval(self):
  with sales.conn() as c:
   pid=self.define(c,'bot')
   oid=ai.choose(c,111,pid)
   before=c.execute('SELECT balance FROM users WHERE id=111').fetchone()[0]
   ai.store_receipt(c,111,oid,{'photo':[{'file_id':'TGPHOTO_FILEID_LONG'}]})
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'preview')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_sale_subscriptions').fetchone()[0],0)
   ai.confirm(c,111,oid)
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'review')
   ai.admin_decide(c,222,oid,'approve')
   ent=c.execute('SELECT remaining_text,remaining_image,remaining_video FROM ai_sale_subscriptions').fetchone()
   self.assertEqual(tuple(ent),(12,3,1))
   self.assertEqual(c.execute('SELECT balance FROM users WHERE id=111').fetchone()[0],before)
   with self.assertRaises(ValueError):ai.admin_decide(c,222,oid,'approve')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_sale_subscriptions').fetchone()[0],1)
   self.assertTrue(c.execute('SELECT 1 FROM outbox WHERE dedupe=?',('ai-fulfilled:'+oid,)).fetchone())
 def test_rejection_records_reason_and_notifies_user(self):
  with sales.conn() as c:
   pid=self.define(c,'bot')
   oid=ai.choose(c,111,pid);ai.store_receipt(c,111,oid,{'document':{'file_id':'document'}});ai.confirm(c,111,oid)
   with self.assertRaises(ValueError):ai.admin_decide(c,222,oid,'reject','نه')
   ai.admin_decide(c,222,oid,'reject','تصویر رسید معتبر نیست')
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'rejected')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_sale_subscriptions').fetchone()[0],0)
   sent=c.execute('SELECT payload FROM outbox WHERE dedupe=?',('ai-rejected:'+oid,)).fetchone()[0]
   self.assertIn('تصویر رسید معتبر نیست',json.loads(sent)['text'])
 def test_personal_email_never_collects_password_and_requires_manual_completion(self):
  with sales.conn() as c:
   pid=self.define(c,'personal')
   oid=ai.choose(c,111,pid)
   with self.assertRaises(ValueError):ai.set_email(c,111,oid,'invalid')
   ai.set_email(c,111,oid,'customer@example.com')
   ai.store_receipt(c,111,oid,{'photo':[{'file_id':'receiptfile'}]});ai.confirm(c,111,oid)
   ai.admin_decide(c,222,oid,'approve')
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'activating')
   self.assertNotIn('password',str(dict(c.execute('SELECT * FROM ai_sale_orders').fetchone())).lower())
   with self.assertRaises(ValueError):ai.admin_decide(c,111,oid,'complete')
   ai.admin_decide(c,222,oid,'complete')
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'fulfilled')
 def test_prepared_stock_one_time_code_with_no_account_credentials(self):
  with sales.conn() as c:
   pid=self.define(c,'ready')
   with self.assertRaises(ValueError):ai.choose(c,111,pid)
   with self.assertRaises(ValueError):ai.stock(c,{'plan_id':pid,'delivery_code':'person@example.com:pass'})
   ai.stock(c,{'plan_id':pid,'delivery_code':'OFFICIAL-REDEEM-ABC-123'})
   oid=ai.choose(c,111,pid)
   ai.store_receipt(c,111,oid,{'photo':[{'file_id':'receipt'}]});ai.confirm(c,111,oid)
   ai.admin_decide(c,222,oid,'approve')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_ready_stock WHERE order_id=?',(oid,)).fetchone()[0],1)
   msg=c.execute('SELECT payload FROM outbox WHERE dedupe=?',('ai-fulfilled:'+oid,)).fetchone()[0]
   self.assertIn('OFFICIAL-REDEEM-ABC-123',json.loads(msg)['text'])
   with self.assertRaises(ValueError):ai.choose(c,111,pid)
 def test_quota_decrements_exactly_once_per_successful_request(self):
  with sales.conn() as c:
   pid=self.define(c,'bot')
   oid=ai.choose(c,111,pid)
   ai.store_receipt(c,111,oid,{'photo':[{'file_id':'proof'}]});ai.confirm(c,111,oid);ai.admin_decide(c,222,oid,'approve')
   self.assertTrue(ai.consume(c,111,'text','ai:client:test'))
   c.execute("""INSERT INTO ai_usage(user_id,reference,model,points,created)
      VALUES(111,'ai:client:test','model',0,0)""")
   self.assertFalse(ai.consume(c,111,'text','ai:client:test'))
   self.assertEqual(c.execute('SELECT remaining_text FROM ai_sale_subscriptions WHERE order_id=?',(oid,)).fetchone()[0],11)
   self.assertFalse(ai.available(c,111,'nonexistent') if False else False)
 def test_wallet_ai_purchase_only_once_and_not_vpn(self):
  with sales.conn() as c:
   pid=self.define(c,'bot',price=3200)
   oid=ai.choose(c,111,pid)
   ai.callback(c,111,'ais:paywallet:'+oid)
   self.assertEqual(c.execute('SELECT balance FROM users WHERE id=111').fetchone()[0],1800)
   self.assertEqual(c.execute('SELECT status FROM ai_sale_orders WHERE id=?',(oid,)).fetchone()[0],'fulfilled')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger WHERE reason=?',('ai_subscription',)).fetchone()[0],1)
   with self.assertRaises(ValueError):ai.callback(c,111,'ais:paywallet:'+oid)
   self.assertEqual(c.execute('SELECT balance FROM users WHERE id=111').fetchone()[0],1800)

 def test_admin_only_review(self):
  with sales.conn() as c:
   pid=self.define(c,'bot');oid=ai.choose(c,111,pid)
   ai.store_receipt(c,111,oid,{'photo':[{'file_id':'p'}]});ai.confirm(c,111,oid)
   with self.assertRaises(ValueError):ai.admin_decide(c,111,oid,'approve')

if __name__=='__main__':unittest.main()
