import os,tempfile,sys,unittest,json
from pathlib import Path
from unittest.mock import patch
env=tempfile.TemporaryDirectory(prefix="masiha-media-test-")
os.environ["MASIHA_SALES_DIR"]=env.name
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales,ai_commerce as commerce,ai_media as media

class MediaTests(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ("ai_media_jobs","ai_media_drafts","ai_sale_models","ai_sale_plans",
      "ai_sale_orders","ai_sale_subscriptions","ai_ready_stock","users","ai_usage",
      "settings","loyalty_points","outbox"):
    c.execute("DELETE FROM "+table)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(123,'customer',0,0)")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(500,'admin',0,0)")
   sales.put(c,"admins",[500])
   sales.put(c,"ai_api_key","TEST_EXAMPLE_KEY_NOT_REAL_123456")
   sales.put(c,"ai_media_enabled",True)
   sales.put(c,"ai_cost_image",9)
   sales.put(c,"ai_cost_video",25)
   sales.put(c,"ai_image_model","gpt-image-1-mini")
   sales.put(c,"ai_video_model","sora-2")
   sales.put(c,"ai_provider_fal_key","EXAMPLE_FAL_KEY_123456")
   sales.put(c,"ai_default_video_model","fal-wan")
   c.execute("INSERT INTO loyalty_points(user_id,points,kind,reference,created) VALUES(123,60,'test','fund',0)")
 def test_queue_image_and_debit_only_after_successful_send(self):
  with sales.conn() as c:
   c.execute("UPDATE users SET state='ai:image:input' WHERE id=123")
   media.input_message(c,123,'ai:image:input',{'text':'Draw a cozy robot'},7001)
   self.assertEqual(c.execute('SELECT status FROM ai_media_jobs').fetchone()[0],'queued')
   self.assertEqual(c.execute('SELECT SUM(points) FROM loyalty_points WHERE user_id=123').fetchone()[0],60)
  with patch.object(media,"_provider_create",return_value=("image",b"FAKE-PNG")), \
       patch.object(media,"_send_file",return_value=None):
   self.assertEqual(media.run_once(),"image_done")
  with sales.conn() as c:
   self.assertEqual(c.execute('SELECT SUM(points) FROM loyalty_points WHERE user_id=123').fetchone()[0],51)
   self.assertEqual(c.execute('SELECT status FROM ai_media_jobs').fetchone()[0],'done')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0],1)
 def test_failed_provider_never_charges(self):
  with sales.conn() as c:
   media.input_message(c,123,'ai:image:input',{'text':'A sunny garden'},7002)
  with patch.object(media,"_provider_create",side_effect=RuntimeError("provider")):
   self.assertEqual(media.run_once(),"media_failed")
  with sales.conn() as c:
   self.assertEqual(c.execute('SELECT status FROM ai_media_jobs').fetchone()[0],'failed')
   self.assertEqual(c.execute('SELECT SUM(points) FROM loyalty_points WHERE user_id=123').fetchone()[0],60)
 def test_video_durations_and_provider_request_seconds(self):
  self.assertEqual(media.VIDEO_SECONDS,8)
  with sales.conn() as c:
   with self.assertRaises(ValueError):
    media.input_message(c,123,'ai:video:input',{'video':{'file_id':'f123','duration':11,'file_size':4000},'caption':'Edit color'},7003)
   media.input_message(c,123,'ai:video:input',{'text':'Create a cinematic ocean view'},7004)
   row=c.execute('SELECT * FROM ai_media_jobs WHERE update_id=7004').fetchone()
   import ai_fal_video
   with patch.object(ai_fal_video,"submit",return_value="1dd54e2a-1234-4aa4-b3fd-5ad511c00e4e") as call:
    kind,remote=media._provider_create(c,row,"test-key",sales.settings(c))
    self.assertEqual(kind,"video")
    self.assertEqual(json.loads(remote)["model_key"],"fal-wan")
    self.assertEqual(call.call_args.args[0],"fal-ai/wan/v2.2-a14b/text-to-video")
 def test_job_not_queued_without_media_admin_enable(self):
  with sales.conn() as c:
   sales.put(c,'ai_media_enabled',False)
   with self.assertRaisesRegex(ValueError,'فعال'):
    media.input_message(c,123,'ai:image:input',{'text':'beautiful landscape'},7010)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_media_jobs').fetchone()[0],0)
 def test_admin_is_free_and_photo_caption_edit_flow(self):
  with sales.conn() as c:
   media.menu(c,500,'image')
   media.input_message(c,500,'ai:image:input',{'photo':[{'file_id':'telegram-image-ref'}]},7011)
   self.assertEqual(c.execute('SELECT state FROM users WHERE id=500').fetchone()[0],'ai:image:caption')
   media.input_message(c,500,'ai:image:caption',{'text':'Change background to blue'},7012)
   row=c.execute('SELECT * FROM ai_media_jobs WHERE update_id=7012').fetchone()
   self.assertEqual((row['kind'],row['file_id']),('image','telegram-image-ref'))
  with patch.object(media,"_provider_create",return_value=("image",b"FAKE-PNG")),patch.object(media,"_send_file"):
   self.assertEqual(media.run_once(),"image_done")
  with sales.conn() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM loyalty_points WHERE user_id=500").fetchone()[0],0)
 def test_provider_url_is_fixed_and_does_not_use_ai_api_url(self):
  self.assertEqual(media.API,'https://api.openai.com/v1')
  with patch.object(media,"_opener") as opener:
   opener.return_value.open.side_effect=RuntimeError("do-not-network")
   with self.assertRaises(RuntimeError):
    media._api("fake","/videos")
   request=opener.return_value.open.call_args.args[0]
   self.assertEqual(request.full_url,'https://api.openai.com/v1/videos')

if __name__=="__main__":unittest.main()
