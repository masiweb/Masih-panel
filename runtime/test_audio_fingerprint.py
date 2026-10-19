#!/usr/bin/env python3
"""Real local fingerprint regression without network, with synthetic nonperiodic audio."""
import json,os,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch

os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-audio-test-db-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audio_fingerprint as fp
import sales

try:import numpy as np
except ImportError:np=None

class Fingerprints(unittest.TestCase):
 def setUp(self):
  self.work=tempfile.TemporaryDirectory(prefix="masiha-audio-qa-")
  self.addCleanup(self.work.cleanup)
  self.root=Path(self.work.name)
  self.dbpatch=patch.multiple(fp,ROOT=self.root,DATABASE=self.root/"fp.pklz",LOCK=self.root/"fp.lock")
  self.dbpatch.start();self.addCleanup(self.dbpatch.stop)
  with sales.conn() as c:
   c.execute("DELETE FROM audio_fp_queries")
   c.execute("DELETE FROM audio_fp_index")
   c.execute("DELETE FROM outbox")
   c.execute("DELETE FROM users")
   c.execute("DELETE FROM media_items")
   c.execute("INSERT INTO users(id,name,balance,created) VALUES(4444444,'test',0,0)")

 def synthetic(self):
  if np is None:self.skipTest("numpy unavailable")
  rng=np.random.default_rng(311);sr=11025;n=sr*32
  s=np.zeros(n)
  for octave in range(7):
   segment=int(sr*(.13+.06*octave))
   for i in range(0,n,segment):
    size=min(segment,n-i)
    freq=float(rng.uniform(170,3900));q=np.arange(size)/sr
    env=np.minimum(1,np.arange(size)/max(1,int(sr*.015)))
    s[i:i+size]+=(.12+octave*.025)*env*np.sin(2*np.pi*freq*q+rng.uniform(0,6))
  data=(s/np.max(abs(s))*24000).astype(np.int16)
  output={}
  for name,samples in (("sample.wav",data),("query.wav",data[13*sr:25*sr])):
   path=self.root/name
   with wave.open(str(path),"wb") as stream:
    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(sr)
    stream.writeframes(samples.tobytes())
   output[name]=path
  return output

 def test_query_rejects_short_large_invalid(self):
  with sales.conn() as c:
   for obj in ({"file_id":"tiny","duration":10,"file_size":2344},
               {"file_id":"ABCDEFGHIJKL","duration":1,"file_size":2344},
               {"file_id":"ABCDEFGHIJKL","duration":10,"file_size":20_000_000}):
    with self.assertRaises(ValueError):fp.enqueue(c,4444444,44,obj)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM audio_fp_queries").fetchone()[0],0)

 @unittest.skipUnless(fp.available(),"audfprint and ffmpeg needed for live matching")
 def test_real_audio_fragment_matches_same_song_without_false_positive(self):
  files=self.synthetic()
  with sales.conn() as c:
   c.execute("""INSERT INTO media_items(id,owner_id,kind,telegram_method,file_id,unique_id,
     title,artist,status,created) VALUES(24680,0,'music','sendAudio','ref',
     'unique-one','موزیک تست','خواننده تست','published',1)""")
  fp.index_audio(files["sample.wav"],"m24680")
  match=fp.recognize_audio(files["query.wav"])
  self.assertIsNotNone(match)
  self.assertTrue(match.endswith("m24680.mp3"))
  with sales.conn() as c:
   result=fp.resolve_match(c,match)
   self.assertEqual(result["id"],24680)

 @unittest.skipUnless(fp.available(),"audfprint and ffmpeg needed for live matching")
 def test_query_processing_selects_db_media_and_never_saves_raw_audio(self):
  files=self.synthetic()
  with sales.conn() as c:
   c.execute("""INSERT INTO media_items(id,owner_id,kind,telegram_method,file_id,unique_id,
     title,artist,status,created) VALUES(30001,0,'music','sendAudio','ref',
     'another-one','آهنگ آزمایشی','خواننده','published',1)""")
   fp.index_audio(files["sample.wav"],"m30001")
   fp.enqueue(c,4444444,9001,{"file_id":"VOICEFILEID01","file_size":2000,"duration":12})
   item=c.execute("SELECT * FROM audio_fp_queries WHERE update_id=9001").fetchone()
   def fake_download(conn,fid,dest,limit):
    Path(dest).write_bytes(files["query.wav"].read_bytes())
   with patch.object(fp,"download_telegram",side_effect=fake_download):
    fp.process_query(c,item)
   result=c.execute("SELECT state,media_id FROM audio_fp_queries WHERE update_id=9001").fetchone()
   self.assertEqual(tuple(result),("done",30001))
   sent=c.execute("SELECT payload FROM outbox WHERE chat=4444444 ORDER BY id DESC LIMIT 1").fetchone()[0]
   self.assertIn("آهنگ آزمایشی",json.loads(sent)["text"])
   self.assertIn("med:play:30001",sent)
   raw=[p for p in self.root.rglob("query.ogg")]
   self.assertFalse(raw)

if __name__=="__main__":unittest.main()
