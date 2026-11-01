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
    "source":"film2media","external_key":"sample-film-2026","rights_ok":True,
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
    def test_stage_allows_one_original_and_one_dubbed_per_quality(self):
        entry={**ENTRY,"external_key":"quality-dedupe-sample","variants":[
            {"quality":"720p","url":URL+"original-webdl.mp4","dubbed":"no",
             "subtitles":"softsub","version":"WEB-DL"},
            {"quality":"720p","url":URL+"original-webrip.mp4","dubbed":"no",
             "subtitles":"none","version":"WEBRip"},
            {"quality":"720p","url":URL+"dubbed.mp4","dubbed":"yes",
             "subtitles":"none","version":"WEB-DL"},
            {"quality":"1080p","url":URL+"original-1080.mp4","dubbed":"unknown",
             "subtitles":"softsub","version":"WEB-DL"}
        ]}
        self.assertEqual(film.stage(self.c,entry,{"videos.example.com"}),3)
        rows=self.c.execute("""SELECT quality,
            CASE WHEN dubbed='yes' THEN 'dubbed' ELSE 'original' END AS kind,
            COUNT(*) AS n FROM film_variants GROUP BY quality,kind
            ORDER BY quality,kind""").fetchall()
        self.assertEqual([(r["quality"],r["kind"],r["n"]) for r in rows],
                         [("1080p","original",1),("720p","dubbed",1),("720p","original",1)])
        self.assertEqual(film.stage(self.c,{**entry,"variants":[
            {"quality":"720p","url":URL+"another-original.mp4","dubbed":"no",
             "subtitles":"softsub","version":"another encode"}
        ]},{"videos.example.com"}),0)

    def test_playback_deduplicates_quality_but_keeps_dubbed(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        base=self.c.execute("SELECT id,title_id FROM film_variants ORDER BY id").fetchall()
        for i,v in enumerate(base):
            self.c.execute("UPDATE film_variants SET file_id=?,file_unique_id=?,uploaded=1 WHERE id=?",
                           ("BASE_FILE_"+str(i)*15,"BASE_UNIQUE_"+str(i)*15,v["id"]))
            self.c.execute("UPDATE film_import_jobs SET state='done',updated=? WHERE variant_id=?",
                           (film.now(),v["id"]))
        title_id=base[0]["title_id"]
        duplicate=self.c.execute("""INSERT INTO film_variants
            (title_id,season,episode,quality,version,dubbed,subtitles,language,source_url,
             send_method,file_id,file_unique_id,uploaded,created)
            VALUES(?,0,0,'720p','WEBRip','no','softsub','',?,'sendDocument',
             'DUP_FILE_ID_333333333333','DUP_UNIQUE_333333333333',1,?)""",
             (title_id,URL+"duplicate-original.mp4",film.now())).lastrowid
        self.c.execute("INSERT INTO film_import_jobs(variant_id,state,updated) VALUES(?,'done',?)",
                       (duplicate,film.now()))
        dubbed=self.c.execute("""INSERT INTO film_variants
            (title_id,season,episode,quality,version,dubbed,subtitles,language,source_url,
             send_method,file_id,file_unique_id,uploaded,created)
            VALUES(?,0,0,'720p','WEB-DL','yes','none','Persian',?,'sendDocument',
             'DUB_FILE_ID_444444444444','DUB_UNIQUE_444444444444',1,?)""",
             (title_id,URL+"dubbed.mp4",film.now())).lastrowid
        self.c.execute("INSERT INTO film_import_jobs(variant_id,state,updated) VALUES(?,'done',?)",
                       (dubbed,film.now()))
        self.c.commit()
        mid=film.publish(self.c,base[0]["id"])
        lib.play(self.c,991,mid)
        card=self.c.execute("SELECT method,payload FROM outbox WHERE chat=991 ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(card["method"],"sendMessage")
        payload=json.loads(card["payload"])
        buttons=[b for group in payload["reply_markup"]["inline_keyboard"] for b in group]
        quality=[b for b in buttons if b["callback_data"].startswith("med:filmfile:")]
        callbacks={b["callback_data"] for b in quality}
        self.assertEqual(sum("720p" in b["text"] for b in quality),2)
        self.assertIn("med:filmfile:"+str(dubbed),callbacks)
        self.assertNotIn("med:filmfile:"+str(duplicate),callbacks)
        self.assertEqual(sum("720p" in b["text"] and "دوبله" in b["text"] for b in quality),1)
        self.assertEqual(sum("720p" in b["text"] and "زبان اصلی" in b["text"] for b in quality),1)

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
    def test_movie_poster_contains_quality_reactions_and_comments(self):
        film.stage(self.c,ENTRY,{"videos.example.com"})
        self.c.execute("UPDATE film_titles SET cover_id='COVER_SAMPLE_FILE_ID'")
        vids=[r[0] for r in self.c.execute("SELECT id FROM film_variants ORDER BY id")]
        for i,vid in enumerate(vids):
            self.c.execute("UPDATE film_variants SET file_id=?,file_unique_id=?,uploaded=1 WHERE id=?",
                           ("FILE_ID_"+str(i)*15,"UNIQUE_ID_"+str(i)*15,vid))
        mid=film.publish(self.c,vids[0])
        broadcast=self.c.execute(
            "SELECT method,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(broadcast["method"],"sendPhoto")
        notice=json.loads(broadcast["payload"])
        self.assertEqual(notice["photo"],"COVER_SAMPLE_FILE_ID")
        self.assertIn("med:filmfile:"+str(vids[0]),broadcast["payload"])
        self.assertIn("med:like:"+str(mid),broadcast["payload"])
        film.publish(self.c,vids[1])
        lib.play(self.c,991,mid)
        card=self.c.execute("SELECT method,payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(card["method"],"sendPhoto")
        self.assertIn("med:filmfile:"+str(vids[1]),card["payload"])
        self.assertIn("med:comments:"+str(mid),card["payload"])
        self.assertIn("med:dislike:"+str(mid),card["payload"])

    def test_uploader_refuses_when_local_api_unconfigured(self):
        with self.assertRaises(RuntimeError):
            film.upload(Path("/does/not/exist"),"sendDocument",
                {"local_bot_api":False},{"token":"FAKE","enabled":True},1)

if __name__=="__main__":unittest.main()