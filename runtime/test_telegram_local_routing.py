"""Cloud-to-loopback Telegram route tests; no real calls or credentials."""
import sys, pathlib, unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import sales
class TelegramLocalTests(unittest.TestCase):
 def test_default_is_cloud(self):
  with patch.object(sales.os.path,"isfile",return_value=False):
   self.assertEqual(sales.telegram_endpoint({"token":"SAFE_TEST"},"getMe"),
     "https://api.telegram.org/botSAFE_TEST/getMe")
 def test_enabled_is_local_only(self):
  with patch.object(sales.os.path,"isfile",return_value=True):
   self.assertEqual(sales.telegram_endpoint({"token":"SAFE_TEST"},"getMe"),
     "http://127.0.0.1:8081/botSAFE_TEST/getMe")
 def test_allowed_endpoint_guard(self):
  import urllib.request
  for u in ("http://127.0.0.1:8082/botFAKE/getMe",
            "http://evil.example.com/botFAKE/getMe",
            "http://127.0.0.2:8081/botFAKE/getMe"):
   with self.assertRaises(ValueError):
    sales.telegram_open(urllib.request.Request(u),timeout=0.01)
if __name__=="__main__":unittest.main()