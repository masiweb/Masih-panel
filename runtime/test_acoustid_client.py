#!/usr/bin/env python3
"""AcoustID behavior mocked; no audio or keys sent on network."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-acoustid-test-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales,acoustid_client as client,audio_fingerprint as fp

class AcoustIdTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.secret=Path(self.temp.name)/"config.json"
  self.config=patch.object(client,"CONFIG_FILE",self.secret)
  self.config.start();self.addCleanup(self.config.stop)
  with sales.conn() as c:
   for table in ("audio_fp_queries","media_items","outbox","users"):
    c.execute("DELETE FROM "+table)
   c.execute("INSERT INTO users(id,name,created) VALUES(321999,'tester',0)")
   for mid,title,artist,state in (
     (13579,"یاد تو","علی رضایی","published"),
     (13580,"یاد تو","خواننده دیگر","published"),
     (13581,"آهنگ خصوصی","علی رضایی","draft"),
   ):
    c.execute("""INSERT INTO media_items(id,owner_id,kind,telegram_method,file_id,unique_id,
     title,artist,status,created) VALUES(?,0,'music','sendAudio',?,?,?,?,?,1)""",
     (mid,"AUDIO_FILE_ID_"+str(mid),"unique-"+str(mid),title,artist,state))
 def key(self):
  self.secret.write_text('{"provider":"acoustid","enabled":true,"client_key":"valid-private-key"}')
 def test_configuration(self):
  self.assertFalse(client.configured())
  self.secret.write_text('{"provider":"acoustid","enabled":false,"client_key":"abcdefghij"}')
  self.assertFalse(client.configured())
  self.key();self.assertTrue(client.configured())
  self.secret.write_text('{"provider":"acoustid","enabled":true,"client_key":"bad key &"}')
  self.assertFalse(client.configured())
 def test_lookup_api_and_response_limit(self):
  response=json.dumps({"status":"ok","results":[
   {"score":0.98,"recordings":[{"title":"یاد تو","artists":[{"name":"علی رضایی"}]}]},
   {"score":0.4,"recordings":[{"title":"نادرست","artists":[{"name":"ناشناس"}]}]}
  ]}).encode()
  class FakeResponse:
   def __enter__(self):return self
   def __exit__(self,*args):return False
   def geturl(self):return client.API_URL
   def read(self,n):return response
  with patch.object(client.OPENER,"open",return_value=FakeResponse()) as req:
   found=client.lookup("ABCDEF12345",31,"private-api-key")
  self.assertEqual(len(found),1)
  self.assertEqual(found[0]["artist"],"علی رضایی")
  request=req.call_args.args[0]
  self.assertEqual(request.get_method(),"POST")
  self.assertIn(b"fingerprint=",request.data)
  self.assertNotIn("private-api-key",str(found))
 def test_strict_catalog_match(self):
  with sales.conn() as c:
   result=client.match_catalog(c,[{"title":"یاد تو","artist":"علي رضايي","confidence":0.97}])
   self.assertEqual(result[0]["id"],13579)
   self.assertIsNone(client.match_catalog(c,[{"title":"یاد تو","artist":"خواننده نامرتبط","confidence":0.99}]))
   self.assertIsNone(client.match_catalog(c,[{"title":"آهنگ خصوصی","artist":"علی رضایی","confidence":1.0}]))
   self.assertIsNone(client.match_catalog(c,[{"title":"یاد تو","artist":"","confidence":0.99}]))
 def test_process_api_match_and_no_catalog(self):
  self.key()
  def fake_download(c,file_id,dest,limit):Path(dest).write_bytes(b"audio")
  with sales.conn() as c:
   for update,track,artist in ((90001,"یاد تو","علي رضايي"),(90002,"آهنگ ناشناخته","خواننده جدید")):
    c.execute("INSERT INTO audio_fp_queries(user_id,update_id,file_id,created) VALUES(321999,?,'VOICE_FILE_ID_ABCDEFGHIJ',1)",(update,))
    row=c.execute("SELECT * FROM audio_fp_queries WHERE update_id=?",(update,)).fetchone()
    with patch.object(fp,"download_telegram",side_effect=fake_download),patch.object(client,"identify",return_value=[{"title":track,"artist":artist,"confidence":0.99}]),patch.object(fp,"available",return_value=False):
     fp.process_query(c,row)
   out=c.execute("SELECT payload FROM outbox ORDER BY id").fetchall()
   self.assertIn("med:play:13579",out[0][0])
   self.assertIn("آهنگ ناشناخته",out[1][0])
   self.assertNotIn("med:play:",out[1][0])
   self.assertEqual(c.execute("SELECT message FROM audio_fp_queries WHERE update_id=90001").fetchone()[0],"matched_api")
   self.assertEqual(c.execute("SELECT message FROM audio_fp_queries WHERE update_id=90002").fetchone()[0],"identified_not_in_catalog")
if __name__=="__main__":unittest.main()