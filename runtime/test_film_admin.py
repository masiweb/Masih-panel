"""Isolated film panel API validation tests; no real upload or user data."""
import os, json, tempfile, sys, unittest
from pathlib import Path
from unittest.mock import patch
root=tempfile.TemporaryDirectory(prefix='masiha-film-admin-test-')
os.environ['MASIHA_SALES_DIR']=root.name
sys.path.insert(0,str(Path(__file__).resolve().parent))
import film_admin,film_importer as fi

class FilmAdminTests(unittest.TestCase):
 def setUp(self):
  fi._DNS_CACHE.clear()
  self.conf=Path(root.name)/'film_import.json'
  self.conf.write_text(json.dumps({'local_bot_api':True,'download_hosts':['test.example.com']}))
  self.patchcfg=patch.object(fi,'CONFIG',self.conf);self.patchcfg.start();self.addCleanup(self.patchcfg.stop)
  self.patchdns=patch.object(fi.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]);self.patchdns.start();self.addCleanup(self.patchdns.stop)
  with fi.connect() as c:
   for t in ('film_import_jobs','film_variants','film_titles'):c.execute('DELETE FROM '+t)
   c.commit()
 def item(self):return {'source':'film2media','external_key':'permitted-test','rights_ok':True,'media_type':'movie','title':'Authorized sample','variants':[{'quality':'720p','url':'https://test.example.com/movie.mp4'}]}
 def test_create_retry_idempotence(self):
  info=film_admin.handle({'action':'add','entry':self.item()})
  self.assertEqual(info['added_variants'],1)
  self.assertEqual(film_admin.handle({'action':'add','entry':self.item()})['added_variants'],0)
  self.assertEqual(info['counts']['pending'],1)
  vid=info['titles'][0]['variants'][0]['id']
  with fi.connect() as c:
   c.execute("UPDATE film_import_jobs SET state='failed', attempts=3 WHERE variant_id=?",(vid,));c.commit()
  status=film_admin.handle({'action':'retry','variant_id':vid})
  self.assertEqual(status['counts']['pending'],1)
  self.assertEqual(status['titles'][0]['variants'][0]['attempts'],0)
 def test_size_skipped_variant_cannot_be_retried(self):
  info=film_admin.handle({'action':'add','entry':self.item()})
  vid=info['titles'][0]['variants'][0]['id']
  with fi.connect() as c:
   c.execute("UPDATE film_import_jobs SET state='blocked',last_error='SKIPPED_SIZE_LIMIT: source too large' WHERE variant_id=?",(vid,));c.commit()
  with self.assertRaisesRegex(ValueError,'دوباره پردازش نمی‌شود'):
   film_admin.handle({'action':'retry','variant_id':vid})

 def test_rights_and_hosts_enforced(self):
  item=self.item();item['rights_ok']=False
  with self.assertRaises(ValueError):film_admin.handle({'action':'add','entry':item})
  item=self.item();item['variants'][0]['url']='https://other.example.com/media.mp4'
  with self.assertRaises(ValueError):film_admin.handle({'action':'add','entry':item})
  with patch.object(fi.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(ValueError):film_admin.handle({'action':'add_host','hostname':'private.example.com'})
 def test_authorize_exact_new_host(self):
  info=film_admin.handle({'action':'add_host','hostname':'public.example.com'})
  self.assertIn('public.example.com',info['download_hosts'])
  self.assertEqual(len(info['download_hosts']),2)

if __name__=='__main__': unittest.main()
