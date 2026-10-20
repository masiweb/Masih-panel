import os,tempfile,sys,unittest,json,base64
from pathlib import Path
from unittest.mock import patch
tmp=tempfile.TemporaryDirectory(prefix="multi-model-tests-")
os.environ["MASIHA_SALES_DIR"]=tmp.name
sys.path.insert(0,str(Path(__file__).parent))
import sales,ai_commerce,ai_models,ai_fal_video
class AssignmentTests(unittest.TestCase):
 def setUp(self):
  with sales.conn() as c:
   for table in ("ai_sale_orders","ai_sale_subscriptions","ai_sale_plans","ai_sale_models","users","settings"):
    c.execute("DELETE FROM "+table)
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(77,'User',0,0)")
 def test_capability_catalog_and_sora_removal(self):
  with sales.conn() as c:
   items=ai_models.catalog(c)
   self.assertIn(("google","text"),{(r["provider"],r["modality"]) for r in items})
   self.assertIn(("cloudflare","image"),{(r["provider"],r["modality"]) for r in items})
   self.assertIn(("fal","video"),{(r["provider"],r["modality"]) for r in items})
   self.assertNotIn(("openai","video"),{(r["provider"],r["modality"]) for r in items})
 def test_one_subscription_uses_three_different_providers(self):
  with sales.conn() as c:
   family=ai_commerce.model(c,{"name":"AI"})
   plan=ai_commerce.plan(c,{"name":"Hybrid","kind":"bot","model_id":family,
    "price":15000,"days":30,"credits_text":20,"credits_image":4,"credits_video":2,
    "model_text":"gg-lite","model_image":"cf-flux","model_video":"fal-wan"})
   oid=ai_commerce.choose(c,77,plan)
   c.execute("""INSERT INTO ai_sale_subscriptions(order_id,user_id,expires,
    remaining_text,remaining_image,remaining_video,created)
    VALUES(?,?,?,?,?,?,?)""",(oid,77,sales.now()+3000,20,4,2,sales.now()))
   self.assertEqual([ai_models.resolved(c,77,x)["provider"] for x in ("text","image","video")],
                    ["google","cloudflare","fal"])
   with self.assertRaises(ValueError):ai_models.select(c,"cf-flux","text")
 def test_no_secret_leak_and_provider_route(self):
  with sales.conn() as c:
   sales.put(c,"ai_provider_google_key","EXAMPLE_TEST_KEY_123456")
  reply=sales.admin({"action":"ai_model_catalog"})
  self.assertTrue(reply["providers"]["google"])
  self.assertNotIn("EXAMPLE_TEST_KEY_123456",str(reply))
  with sales.conn() as c:
   with patch.object(ai_models,"_api_json",return_value={"choices":[{"message":{"content":"سلام"}}]}) as mock:
    value=ai_models.text_response(c,ai_models.select(c,"gg-lite","text"),"سلام")
    self.assertEqual(value,"سلام")
    self.assertIn("generativelanguage.googleapis.com",mock.call_args.args[0])
 def test_cloudflare_image_and_video_url_guards(self):
  with sales.conn() as c:
   sales.put(c,"ai_provider_cloudflare_key","EXAMPLE_TEST_TOKEN_123456")
   sales.put(c,"ai_provider_cloudflare_account","a"*32)
   with patch.object(ai_models,"_api_json",return_value={"result":{"image":base64.b64encode(b"JPEG").decode()}}):
    self.assertEqual(ai_models.cloudflare_image(c,ai_models.select(c,"cf-flux","image"),"tree"),b"JPEG")
  with self.assertRaises(ValueError):ai_fal_video.submit("sora-2","key","a sunny park")
  with self.assertRaises(ValueError):ai_fal_video.download_video("http://127.0.0.1/file")
if __name__=="__main__":unittest.main()
