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
import film_catalog as catalog

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
        film._DNS_CACHE.clear()
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
        self.assertEqual(film.stats(self.c),{"pending":2,"size_skipped":0})
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

    def test_catalog_parser_deduplicates_same_quality_and_version(self):
        source=" ".join([
            '<a href="https://videos.example.com/sub/Love.Language.720p.WEB-DL.x265.mkv">a</a>',
            '<a href="https://videos.example.com/sub/Love.Language.720p.WEBRip.x264.mkv">b</a>',
            '<a href="https://videos.example.com/dub/Love.Language.720p.DUBBED.WEB-DL.mkv">c</a>',
            '<a href="https://videos.example.com/sub/Love.Language.1080p.WEB-DL.x265.mkv">d</a>'
        ])
        variants=catalog.variants_from_html(source,"movie")
        keys={(v["quality"],"dubbed" if v["dubbed"]=="yes" else "original")
              for v in variants}
        self.assertEqual(len(variants),3)
        self.assertEqual(keys,{("720p","original"),("720p","dubbed"),("1080p","original")})

    def test_playback_hides_legacy_duplicate_but_keeps_dubbed_version(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        base=self.c.execute("SELECT id,title_id,quality,dubbed FROM film_variants ORDER BY id").fetchall()
        for i,v in enumerate(base):
            self.c.execute("""UPDATE film_variants SET file_id=?,file_unique_id=?,file_bytes=100000,
                duration=3600,width=1280,height=720,video_codec='h264',audio_codec='aac',
                stream_ready=1,uploaded=1 WHERE id=?""",
                ("BASE_FILE_"+str(i)*20,"BASE_UNIQUE_"+str(i)*20,v["id"]))
            self.c.execute("UPDATE film_import_jobs SET state='done',updated=? WHERE variant_id=?",
                           (film.now(),v["id"]))
        title_id=base[0]["title_id"]
        duplicate=self.c.execute("""INSERT INTO film_variants
            (title_id,season,episode,quality,version,dubbed,subtitles,language,source_url,
             send_method,file_id,file_unique_id,file_bytes,duration,width,height,video_codec,
             audio_codec,stream_ready,uploaded,created)
            VALUES(?,0,0,'720p','WEBRip','no','none','English',?,'sendVideo',
             'DUP_FILE_ID_333333333333','DUP_UNIQUE_333333333333',100000,3600,1280,720,
             'h264','aac',1,1,?)""",(title_id,URL+"duplicate-original.mp4",film.now())).lastrowid
        self.c.execute("INSERT INTO film_import_jobs(variant_id,state,updated) VALUES(?,'done',?)",
                       (duplicate,film.now()))
        dubbed=self.c.execute("""INSERT INTO film_variants
            (title_id,season,episode,quality,version,dubbed,subtitles,language,source_url,
             send_method,file_id,file_unique_id,file_bytes,duration,width,height,video_codec,
             audio_codec,stream_ready,uploaded,created)
            VALUES(?,0,0,'720p','WEB-DL','yes','none','فارسی',?,'sendVideo',
             'DUB_FILE_ID_444444444444','DUB_UNIQUE_444444444444',100000,3600,1280,720,
             'h264','aac',1,1,?)""",(title_id,URL+"dubbed.mp4",film.now())).lastrowid
        self.c.execute("INSERT INTO film_import_jobs(variant_id,state,updated) VALUES(?,'done',?)",
                       (dubbed,film.now()))
        self.c.commit()
        mid=film.publish(self.c,base[0]["id"])
        lib.play(self.c,991,mid)
        rows=self.c.execute("SELECT method,payload FROM outbox WHERE chat=991 ORDER BY id").fetchall()
        menu=json.loads(rows[-1]["payload"])
        buttons=[b for group in menu["reply_markup"]["inline_keyboard"] for b in group]
        quality=[b for b in buttons if b["callback_data"].startswith("med:filmfile:")]
        labels=[b["text"] for b in quality]
        callbacks={b["callback_data"] for b in quality}
        self.assertEqual(sum("720p" in label for label in labels),2)
        self.assertIn("med:filmfile:"+str(dubbed),callbacks)
        self.assertEqual(sum("720p" in label and "دوبله" in label for label in labels),1)
        self.assertEqual(sum("720p" in label and "زبان اصلی" in label for label in labels),1)

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
               duration=3600,width=1280,height=720,video_codec='hevc',audio_codec='aac',
               stream_ready=1,uploaded=1 WHERE id=?""",("FILE_ID_"+str(i)*15,"UNIQUE_"+str(i)*15,v[0]))
        self.c.execute("UPDATE film_titles SET cover_id='COVER_SAMPLE_FILE_ID'")
        first=film.publish(self.c,variants[0][0])
        self.assertIsNone(first)
        self.assertEqual(self.c.execute("SELECT count(*) FROM media_items WHERE kind='film'").fetchone()[0],0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],0)
        mid=film.publish(self.c,variants[1][0])
        self.assertTrue(mid>0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM media_items WHERE kind='film'").fetchone()[0],1)
        notifications=self.c.execute("SELECT method,payload,dedupe FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchall()
        self.assertEqual(len(notifications),1)
        self.assertEqual(notifications[0][0],"sendPhoto")
        notice=json.loads(notifications[0][1])
        self.assertEqual(notice["photo"],"COVER_SAMPLE_FILE_ID")
        self.assertIn("med:filmfile:"+str(variants[0][0]),notifications[0][1])
        self.assertIn("med:filmfile:"+str(variants[1][0]),notifications[0][1])
        self.assertIn("med:like:"+str(mid),notifications[0][1])
        self.assertIn("med:addmenu:"+str(mid),notifications[0][1])
        self.assertIn("med:report:"+str(mid),notifications[0][1])
        lib.play(self.c,991,mid)
        rows=self.c.execute("SELECT method,payload FROM outbox WHERE chat=991 ORDER BY id DESC LIMIT 2").fetchall()
        self.assertTrue(any("med:filmfile:"+str(variants[1][0]) in row[1] for row in rows))
        lib.callback(self.c,991,"med:filmfile:"+str(variants[1][0]))
        last=self.c.execute("SELECT method,payload FROM outbox ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last[0],"sendVideo")
        self.assertIn("1080p",last[1])
        self.assertIn("FILE_ID_111111111111111",last[1])
        self.assertIn('"supports_streaming": true',last[1])
    def test_series_notifies_once_per_episode_after_all_qualities(self):
        entry={**ENTRY,"external_key":"series-batch-2026","media_type":"series",
          "variants":[
            {"quality":"720p","url":URL+"s01e01-720.mp4","season":1,"episode":1,"dubbed":"no"},
            {"quality":"1080p","url":URL+"s01e01-1080.mp4","season":1,"episode":1,"dubbed":"no"},
            {"quality":"720p","url":URL+"s01e02-720.mp4","season":1,"episode":2,"dubbed":"no"},
            {"quality":"1080p","url":URL+"s01e02-1080.mp4","season":1,"episode":2,"dubbed":"no"}
          ]}
        self.assertEqual(film.stage(self.c,entry,{"videos.example.com"}),4)
        self.c.execute("UPDATE film_titles SET cover_id='SERIES_COVER'")
        self.c.execute("INSERT OR REPLACE INTO media_settings(k,v) VALUES('broadcast_series',1)")
        variants=self.c.execute("SELECT id,episode FROM film_variants ORDER BY episode,quality").fetchall()
        for i,v in enumerate(variants):
            self.c.execute("UPDATE film_variants SET file_id=?,file_unique_id=?,file_bytes=100000,duration=3600,width=1280,height=720,video_codec='h264',audio_codec='aac',stream_ready=1,uploaded=1 WHERE id=?",
              ("SERIES_FILE_"+str(i)*15,"SERIES_UNIQUE_"+str(i)*15,v["id"]))
        ep1=[v["id"] for v in variants if v["episode"]==1]
        ep2=[v["id"] for v in variants if v["episode"]==2]
        self.assertIsNone(film.publish(self.c,ep1[0]))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],0)
        mid1=film.publish(self.c,ep1[1])
        self.assertTrue(mid1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],1)
        self.assertIsNone(film.publish(self.c,ep2[0]))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchone()[0],1)
        mid2=film.publish(self.c,ep2[1])
        self.assertTrue(mid2 and mid2!=mid1)
        notices=self.c.execute("SELECT payload FROM outbox WHERE dedupe LIKE 'media-broadcast:%' ORDER BY id").fetchall()
        self.assertEqual(len(notices),2)
        captions=[json.loads(x["payload"])["caption"] for x in notices]
        self.assertTrue(any("قسمت 1" in x for x in captions))
        self.assertTrue(any("قسمت 2" in x for x in captions))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM media_items WHERE kind='film' AND status='published'").fetchone()[0],2)

    def test_blocked_quality_is_skipped_before_single_final_notification(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        self.c.execute("UPDATE film_titles SET cover_id='COVER_SAMPLE_FILE_ID'")
        variants=self.c.execute("SELECT id,title_id FROM film_variants ORDER BY id").fetchall()
        first,skipped=variants[0],variants[1]
        self.c.execute("UPDATE film_variants SET file_id='READY_VIDEO_FILE_ID_12345',file_unique_id='READY_VIDEO_UNIQUE_12345',stream_ready=1,uploaded=1 WHERE id=?",(first["id"],))
        self.c.execute("UPDATE film_import_jobs SET state='uploaded' WHERE variant_id=?",(first["id"],))
        self.assertIsNone(film.publish(self.c,first["id"]))
        self.c.execute("UPDATE film_import_jobs SET state='blocked',last_error='SKIPPED_SIZE_LIMIT' WHERE variant_id=?",(skipped["id"],))
        mid=film.finalize_episode(self.c,first["title_id"],0,0)
        self.assertTrue(mid)
        notices=self.c.execute("SELECT method,payload FROM outbox WHERE dedupe LIKE 'media-broadcast:%'").fetchall()
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0]["method"],"sendPhoto")
        self.assertIn("med:filmfile:"+str(first["id"]),notices[0]["payload"])
        self.assertNotIn("med:filmfile:"+str(skipped["id"]),notices[0]["payload"])

    def test_oversize_source_is_skipped_before_writing(self):
        class Response:
            status=200
            url=URL+"too-large.mp4"
            headers={"Content-Length":str(film.MAX_FILE+1),"Content-Type":"video/mp4"}
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,*args): raise AssertionError("oversized response body must not be read")
        class Opener:
            def open(self,*args,**kwargs): return Response()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/"source.bin"
            with patch.object(film,"build_opener",return_value=Opener()):
                with self.assertRaisesRegex(ValueError,"SKIPPED_SIZE_LIMIT"):
                    film.download(URL+"too-large.mp4",target,{"videos.example.com"})
            self.assertFalse(target.exists())

    def test_oversize_stream_output_is_skipped_without_reencoding(self):
        import subprocess
        original={"video_codec":"h264","duration":60,"format_name":"mov,mp4",
                  "width":1280,"height":720,"audio_codec":"aac"}
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"source.bin";source.write_bytes(b"source")
            output=Path(folder)/"stream.mp4"
            def fake_ffmpeg(cmd,**kwargs):
                Path(cmd[-1]).write_bytes(b"x"*70000)
                return type("Result",(),{"returncode":0,"stderr":"","stdout":""})()
            with patch.object(film,"MAX_FILE",65536), patch.object(film,"probe_media",return_value=original), patch.object(subprocess,"run",side_effect=fake_ffmpeg) as run:
                with self.assertRaisesRegex(ValueError,"SKIPPED_SIZE_LIMIT"):
                    film.prepare_streamable(source,output)
                self.assertEqual(run.call_count,1,"oversized output must not trigger a second encoding pass")
                self.assertFalse(output.exists())

    def test_oversize_worker_records_a_permanent_size_skip(self):
        import roz_telegram_upload as audio
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(film,"TEMP",Path(folder)), \
             patch.object(sales,"settings",return_value={"token":"FAKE","enabled":True}), \
             patch.object(audio,"verified_storage_chat",return_value=123), \
             patch.object(film,"download",side_effect=ValueError("SKIPPED_SIZE_LIMIT: source exceeds Telegram Local API 1950 MB limit")):
            result=film.process_one(self.c,{"local_bot_api":True,"hosts":{"videos.example.com"}})
        self.assertEqual(result,"blocked:ValueError")
        row=self.c.execute("SELECT state,last_error FROM film_import_jobs ORDER BY variant_id LIMIT 1").fetchone()
        self.assertEqual(row["state"],"blocked")
        self.assertTrue(row["last_error"].startswith("SKIPPED_SIZE_LIMIT:"))
        self.assertEqual(film.stats(self.c)["size_skipped"],1)

    def test_size_skipped_jobs_are_counted_and_not_retried(self):
        self.assertEqual(film.stage(self.c,ENTRY,{"videos.example.com"}),2)
        vid=self.c.execute("SELECT id FROM film_variants ORDER BY id LIMIT 1").fetchone()[0]
        self.c.execute("UPDATE film_import_jobs SET state='blocked',last_error='SKIPPED_SIZE_LIMIT: source too large' WHERE variant_id=?",(vid,))
        self.c.commit()
        self.assertEqual(film.stats(self.c)["size_skipped"],1)

    def test_uploader_refuses_when_local_api_unconfigured(self):
        with self.assertRaises(RuntimeError):
            film.upload(Path("/does/not/exist"),"sendDocument",
                {"local_bot_api":False},{"token":"FAKE","enabled":True},1)

if __name__=="__main__":unittest.main()