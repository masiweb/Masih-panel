#!/usr/bin/env python3
"""Path-independent native x-ui validation, without live cookies or credentials."""
import importlib.util,json,pathlib,sqlite3,tempfile,unittest
from unittest.mock import patch
SOURCE=pathlib.Path(__file__).with_name("server.py")
original_read_text=pathlib.Path.read_text
def mock_config(path,*args,**kwargs):
 if str(path)=="/etc/masiha-vpn-extension/config.json":
  return json.dumps({"domain":"localhost.test","panel_path":"/outdated/"})
 return original_read_text(path,*args,**kwargs)
with patch.object(pathlib.Path,"read_text",mock_config):
 spec=importlib.util.spec_from_file_location("masiha_sidecar_under_test",SOURCE)
 mod=importlib.util.module_from_spec(spec)
 spec.loader.exec_module(mod)

class DynamicPanelAuth(unittest.TestCase):
 def test_current_path_reads_live_database(self):
  conn=sqlite3.connect(":memory:")
  conn.execute("CREATE TABLE settings(key TEXT,value TEXT)")
  conn.execute("INSERT INTO settings VALUES('webBasePath','/custom-secret-name/')");conn.commit()
  class FakeCursor:
   def __init__(self,path):self.path=path
   def execute(self,*args):return self
   def fetchone(self):return (self.path,)
   def close(self):pass
  with patch.object(mod.sqlite3,"connect",return_value=FakeCursor("/custom-secret-name/")):
   self.assertEqual(mod.current_panel_path(),"/custom-secret-name/")
  with patch.object(mod.sqlite3,"connect",return_value=FakeCursor("/../")):
   with self.assertRaises(ValueError):mod.current_panel_path()
  conn.close()
 def test_csrf_auth_uses_dynamic_path_and_stays_cookie_bound(self):
  seen=[]
  class FakeReply:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):return False
   def read(self,cap):return json.dumps({"success":True,"obj":"csrf-token-locally-validated"}).encode()
  class FakeOpener:
   def open(self,request,timeout):
    seen.append((request.full_url,request.headers))
    return FakeReply()
  with patch.object(mod,"user_is_only_admin",return_value=True),\
       patch.object(mod,"current_panel_path",return_value="/private-2026/"),\
       patch.object(mod.urllib.request,"build_opener",return_value=FakeOpener()):
   self.assertIsNone(mod.upstream_panel_token(""))
   self.assertEqual(mod.upstream_panel_token("session=correct"),"csrf-token-locally-validated")
  self.assertEqual(len(seen),1)
  self.assertEqual(seen[0][0],"http://127.0.0.1:62053/private-2026/panel/csrf-token")
  self.assertEqual(seen[0][1]["Cookie"],"session=correct")
 def test_unauthorized_cannot_obtain_private_path(self):
  h=object.__new__(mod.Handler)
  h.headers={"Host":"localhost.test","Cookie":""}
  responses=[]
  h.send_bytes=lambda code,body,*args: responses.append((code,body))
  with patch.object(mod,"upstream_panel_token",return_value=None):
   h.path="/masiha-ui/api/session"
   mod.Handler.do_GET(h)
  self.assertEqual(responses[0][0],401)
  self.assertNotIn("private",str(responses[0]))
 def test_authenticated_session_keeps_stable_api_base(self):
  h=object.__new__(mod.Handler)
  h.headers={"Host":"localhost.test","Cookie":"session=fake"}
  h.path="/masiha-ui/api/session"
  responses=[]
  h.send_bytes=lambda code,body,*args: responses.append((code,body))
  with patch.object(mod,"upstream_panel_token",return_value="csrf-validated"),\
       patch.object(mod,"current_panel_path",return_value="/custom-route/"):
   mod.Handler.do_GET(h)
  self.assertEqual(responses[0][0],200)
  self.assertEqual(responses[0][1]["obj"]["native_panel"],"/custom-route/")
  self.assertEqual(responses[0][1]["obj"]["api_base"],"/xui/")

if __name__=="__main__":unittest.main()
