#!/usr/bin/python3
import os,sys,tempfile,json,base64,unittest
from pathlib import Path
from unittest.mock import patch
temp=tempfile.TemporaryDirectory(prefix="ai-conversation-tests-")
os.environ["MASIHA_SALES_DIR"]=temp.name
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales, entertainment, sales_worker, ai_conversation,ai_models

class ConversationTests(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ("users","settings","ai_chat_history","ai_long_requests","outbox",
                 "ai_usage","bot_retry_updates","updates","loyalty_points","ai_sale_subscriptions",
                 "ai_sale_orders","ai_sale_plans","ai_sale_models"):
    c.execute("DELETE FROM "+table)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(101,'manager',0,0)")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(102,'existing',0,0)")
   sales.put(c,"admins",[101])
   sales.put(c,"ai_enabled",True)
   sales.put(c,"ai_default_text_model","oa-mini")
   sales.put(c,"ai_api_key","EXAMPLE_ADMIN_KEY_12345")
   sales.put(c,"notifications",False)
   sales.put(c,"bot_username","testbot")
   sales.put(c,"contact_onboarding_rollout_v2",True)
   sales.put(c,"new_user_notice",True)
 def test_multiturn_context_is_per_user_and_persistent(self):
  with sales.conn() as c, patch.object(ai_models,"text_response",
       side_effect=["First answer about plan","Yes, as mentioned earlier"]) as fake:
   entertainment.ai_answer(c,101,"Tell me about plan A",91001)
   entertainment.ai_answer(c,101,"What about that plan?",91002)
   self.assertEqual(len(fake.call_args_list),2)
   context=fake.call_args_list[1].kwargs["history"]
   self.assertEqual([m["role"] for m in context],["user","assistant"])
   self.assertEqual(context[0]["content"],"Tell me about plan A")
   self.assertIn("plan",context[1]["content"])
   self.assertEqual(ai_conversation.previous(c,102),[])
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_chat_history WHERE user_id=101").fetchone()[0],4)
 def test_offer_requires_consent_before_provider_charges(self):
  question="لطفاً یک گزارش جامع و کامل با جزئیات درباره روش های آموزش زبان انگلیسی ارائه بده"
  with sales.conn() as c, patch.object(ai_models,"text_response",return_value="متن پاسخ مفصل"):
   entertainment.ai_answer(c,101,question,92001)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_chat_history").fetchone()[0],0)
   offer=c.execute("SELECT * FROM ai_long_requests WHERE user_id=101").fetchone()
   self.assertEqual(offer["question"],question)
 def test_pdf_txt_files_are_delivered_once_and_history_saved(self):
  question="یک تحلیل جامع و مفصل از تاریخ فناوری اطلاعات و اینترنت به من بده"
  answer="عنوان گزارش\n\n"+("این یک متن فارسی مفصل برای بررسی ساخت فایل PDF و نسخه متنی است.\n"*18)
  with sales.conn() as c, patch.object(ai_models,"text_response",return_value=answer) as provider:
   entertainment.ai_answer(c,101,question,93001)
   q,ref,wants=ai_conversation.consume_offer(c,101,"ai:export:yes")
   self.assertTrue(wants)
   entertainment.ai_answer(c,101,q,ref,long_choice=True)
   self.assertEqual(provider.call_count,1)
   self.assertEqual(provider.call_args.kwargs["expanded"],True)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_usage WHERE reference='ai:93001'").fetchone()[0],1)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_long_requests").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_chat_history WHERE user_id=101").fetchone()[0],2)
   files=c.execute("SELECT method,payload,dedupe FROM outbox WHERE dedupe LIKE 'ai-export:%' ORDER BY dedupe").fetchall()
   self.assertEqual(len(files),2)
   self.assertTrue(all(row["method"]=="file_b64" for row in files))
   decoded={json.loads(r["payload"])["filename"].split(".")[-1]:
     base64.b64decode(json.loads(r["payload"])["data"]) for r in files}
   self.assertTrue(decoded["pdf"].startswith(b"%PDF"))
   self.assertIn(answer,decoded["txt"].decode("utf-8-sig"))
   entertainment.ai_answer(c,101,q,ref,long_choice=True)
   self.assertEqual(provider.call_count,1)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0],1)
 def test_worker_sends_both_document_types_without_exposing_bot_token(self):
  with sales.conn() as c:
   sales.put(c,"enabled",True)
   sales.put(c,"require_contact",False)
   answer="فارسی: این یک گزارش فنی کامل درباره کیفیت خطوط اینترنت و آزمون فایل است."
   ai_conversation.queue_files(c,101,answer,"ai:93501")
  delivered=[]
  def fake_upload(conf,chat,name,data,photo=False):
   delivered.append((chat,name,data,photo))
  def fake_tg(conf,method,payload):
   return [] if method=="getUpdates" else True
  with patch.object(sales_worker,"tg",side_effect=fake_tg),patch.object(sales_worker,"upload",
    side_effect=fake_upload):
   sales_worker.tick()
  self.assertEqual(len(delivered),2)
  self.assertEqual({x[1].split(".")[-1] for x in delivered},{"pdf","txt"})
  self.assertTrue(any(payload.startswith(b"%PDF") for _,_,payload,_ in delivered))
  self.assertTrue(any(answer in payload.decode("utf-8-sig") for _,_,payload,_ in delivered if not payload.startswith(b"%PDF")))
  with sales.conn() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'ai-export:%' AND done=1").fetchone()[0],2)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'ai-export:%' AND payload<>'{}'").fetchone()[0],0)

 def test_export_failure_does_not_consume_or_forget_pending(self):
  question="یک مقاله جامع با جزئیات کامل و بررسی تخصصی بنویس"
  with sales.conn() as c,patch.object(ai_models,"text_response",return_value="پاسخ طولانی"),\
       patch.object(ai_conversation,"make_pdf",side_effect=ValueError("pdf unavailable")):
   entertainment.ai_answer(c,101,question,94001)
   q,ref,_=ai_conversation.consume_offer(c,101,"ai:export:yes")
   with self.assertRaises(ValueError):entertainment.ai_answer(c,101,q,ref,long_choice=True)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_long_requests").fetchone()[0],1)
 def test_decline_delivers_in_chat_and_history_is_deletable(self):
  question="یک مقاله جامع درباره کیفیت شبکه بنویس"
  with sales.conn() as c,patch.object(ai_models,"text_response",return_value="خلاصه مفید") as api:
   entertainment.ai_answer(c,101,question,95001)
   q,ref,wants=ai_conversation.consume_offer(c,101,"ai:export:no")
   self.assertFalse(wants)
   entertainment.ai_answer(c,101,q,ref,long_choice=False)
   self.assertEqual(api.call_args.kwargs["expanded"],False)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE method='file_b64'").fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_chat_history").fetchone()[0],2)
   ai_conversation.clear(c,101)
   self.assertEqual(ai_conversation.previous(c,101),[])
 def test_newcomer_alerts_admin_even_before_channel_or_phone(self):
  with sales.conn() as c:
   sales.put(c,"require_contact",True)
   sales.put(c,"enabled",True)
   sales.put(c,"channel","@testing_channel")
   message={"update_id":96001,"message":{
    "from":{"id":902341,"first_name":"New User","username":"newperson"},
    "chat":{"id":902341,"type":"private"},"text":"/start"}}
   with patch.object(sales_worker,"telegram_channel_member",return_value=False):
    sales_worker.handle(c,message)
    sales_worker.handle(c,dict(message,update_id=96002))
   alert=c.execute("SELECT payload FROM outbox WHERE dedupe=?",
                   ("new-user:902341:101",)).fetchone()
   self.assertIsNotNone(alert)
   self.assertIn("ثبت اولیه",json.loads(alert[0])["text"])
   self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'new-user:%'").fetchone()[0],1)
   self.assertTrue(c.execute("SELECT 1 FROM outbox WHERE dedupe LIKE 'channel-gate:%'").fetchone())
  calls=[]
  def fake_tg(cfg,method,payload):
   if method=="getUpdates":return []
   calls.append((method,payload))
   return {}
  with patch.object(sales_worker,"tg",side_effect=fake_tg):
   sales_worker.tick()
  self.assertTrue(any(method=="sendMessage" and p["chat_id"]==101 and "کاربر جدید" in p["text"]
                 for method,p in calls))
  self.assertTrue(any(method=="sendMessage" and p["chat_id"]==902341 and "عضو شوید" in p["text"]
                 for method,p in calls))
 def test_history_does_not_mix_between_accounts(self):
  with sales.conn() as c:
   ai_conversation.record(c,101,"سؤال مدیر","پاسخ مدیر","model","ai:97001")
   ai_conversation.record(c,102,"سؤال کاربر","پاسخ کاربر","model","ai:97002")
   self.assertNotIn("سؤال کاربر",repr(ai_conversation.previous(c,101)))
   self.assertNotIn("سؤال مدیر",repr(ai_conversation.previous(c,102)))
if __name__=="__main__":unittest.main()
