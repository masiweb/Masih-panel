"""Offline tests for permitted film manifests, quota-safe variant playback, notices."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ["MASIHA_SALES_DIR"]=tempfile.mkdtemp(prefix="masiha-film-unit-")
sys.path.insert(0,str(Path(__file__).resolve().parent))
import sales
import media_library as lib
import film_importer as film

URL="https://videos.example.com/"
ENTRY={
    "source":"hexdl","external_key":"sample-film-2026","rights_ok":True,
    "media_type":"movie","title":"فیلم مجاز تست","year":2026,
    "director":"Test Director","actors":"Actor A, Actor B",
    "description":"Test synopsis","genre":"drama","rating":8.2,
    "rating_source":"IMDb","variants":[
        {"quality":"720p","url":URL+"small.mp4","dubbed":"no","subtitles":"softsub"},
        {"quality":"1080p","url":URL+"large.mp4","dubbed":"yes","subtitles":"none"}
    ]
}
class FilmImporterTests(unittest.TestCase):
    def setUp(self):
        self.c=sales.conn()
        lib.setup(self.c)
        film.setup(self.c)
        for table in ("film_import_jobs","film_variants","film_titles","media_items","outbox","users"):
            self.c.execute("DELETE FROM "+table)
        self.c.execute("INSERT INTO users(id,name,created) VALUES(991,'viewer',1)")
        self.c.execute("INSERT OR REPLACE INTO media_settings(k,v) VALUES('broadcast_movie',1)")
        self.c.commit()
        self.route=patch.object(film.socket,"getaddrinfo",
          return_value=[(2,1,6,"",("93.184.216.34",443))])
        self.route.start();self.addCleanup(self.route.stop)
        self.addCleanup(self.c.close)
    def test_stage_two_qualities_and_idempotency(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM film_variants").fetchone()[0],2)
        self.assertEqual(film.stats(self.c),{"pending":2})
    def test_bad_hosts_private_ip_rights_and_series(self):
        with self.assertRaises(ValueError):
            film.valid_url("http://videos.example.com/media.mp4",{"videos.example.com"})
        with self.assertRaises(ValueError):
            film.valid_url("https://evil.example.com/media.mp4",{"videos.example.com"})
        with self.assertRaises(ValueError):
            film.stage(self.c,{**ENTRY,"rights_ok":False},{"videos.example.com"})
        with self.assertRaises(ValueError):
            film.stage(self.c,{**ENTRY,"media_type":"series"},{"videos.example.com"})
        with patch.object(film.socket,"getaddrinfo",
               return_value=[(2,1,6,"",("127.0.0.1",443))]):
            with self.assertRaises(ValueError):
                film.valid_url(URL+"a.mp4",{"videos.example.com"})
    def test_publish_qualities_announces_once_and_playback(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        variants=self.c.execute("SELECT id FROM film_variants ORDER BY id").fetchall()
        for i,v in enumerate(variants):
            self.c.execute("""UPDATE film_variants SET file_id=?,file_unique_id=?,file_bytes=100000,
               uploaded=1 WHERE id=?""",("FILE_ID_"+str(i)*15,"UNIQUE_"+str(i)*15,v[0]))
        first=film.publish(self.c,variants[0][0])
        self.assertTrue(first>0)
        self.assertEqual(film.publish(self.c,variants[1][0]),first)
        self.assertEqual(self.c.execute("SELECT count(*) FROM media_items WHERE kind='film'").fetchone()[0],1)
        notifications=self.c.execute("SELECT method,payload,dedupe FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchall()
        self.assertEqual(len(notifications),1)
        self.assertEqual(notifications[0][0],"sendMessage")
        self.assertIn("med:play:"+str(first),notifications[0][1])
        lib.play(self.c,991,first)
        rows=self.c.execute("SELECT method,payload FROM outbox WHERE chat=991 ORDER BY id DESC LIMIT 2").fetchall()
        self.assertTrue(any("med:filmfile:"+str(variants[1][0]) in row[1] for row in rows))
        lib.callback(self.c,991,"med:filmfile:"+str(variants[1][0]))
        last=self.c.execute("SELECT method,payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last[0],"sendDocument")
        self.assertIn("1080p",last[1])
        self.assertIn("FILE_ID_111111111111111",last[1])
    def test_uploader_refuses_when_local_api_unconfigured(self):
        with self.assertRaises(RuntimeError):
            film.upload(Path("/does/not/exist"),"sendDocument",
                {"local_bot_api":False},{"token":"FAKE","enabled":True},1)

if __name__=="__main__":unittest.main()