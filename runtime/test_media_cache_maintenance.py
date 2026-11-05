"""Offline regression checks: no real Telegram files or databases touched."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import media_cache_maintenance as m


class MaintenanceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="masiha-cache-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.token="123456789:"+"Z"*35
        self.root=self.base/"bot-api"
        self.bot=self.root/self.token
        (self.bot/"music").mkdir(parents=True)
        (self.bot/"videos").mkdir()
        (self.bot/"temp").mkdir()
        self.db=self.base/"sales.db"
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE settings(k TEXT,v TEXT);
                CREATE TABLE media_items(kind TEXT,status TEXT,file_id TEXT);
                CREATE TABLE film_variants(uploaded INT,stream_ready INT,file_id TEXT);
            """)
            c.execute("INSERT INTO settings VALUES('token',?)",(json.dumps(self.token),))
            c.execute("INSERT INTO media_items VALUES('music','published',?)",("FILE_ID_FROM_SUCCESSFUL_UPLOAD",))
            c.commit()
        self.patchers=[
            patch.object(m,"ROOT",self.root),
            patch.object(m,"DB",self.db),
            patch.object(m,"FILM_TEMP",self.base/"nonexistent"),
            patch.object(m,"opened_inodes",return_value=set()),
            patch.object(m.pwd,"getpwnam",return_value=types.SimpleNamespace(pw_uid=os.getuid())),
        ]
        for p in self.patchers:p.start();self.addCleanup(p.stop)

    def file(self,name,age_hours):
        path=self.bot/"music"/name
        path.write_bytes(b"uploaded-cached-data")
        at=time.time()-age_hours*3600
        os.utime(path,(at,at))
        return path

    def invoke(self,apply):
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.run(apply)
        return json.loads(buf.getvalue())

    def test_prunes_only_old_confirmed_media_cache_files(self):
        old=self.file("file_100",24)
        fresh=self.file("file_101",1)
        unknown=self.file("not-a-cache-file",48)
        binlog=self.bot/"messages.binlog"
        binlog.write_bytes(b"do not touch")
        dry=self.invoke(False)
        self.assertEqual(dry["music"]["files"],1)
        self.assertTrue(old.exists())
        done=self.invoke(True)
        self.assertEqual(done["music"]["files"],1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(binlog.exists())

    def test_refuses_cleanup_without_persisted_media_receipt(self):
        stale=self.file("file_200",30)
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM media_items")
        out=self.invoke(True)
        self.assertEqual(out["music"]["files"],0)
        self.assertTrue(stale.exists())

    def test_video_snapshot_prunes_only_new_exact_size_without_getfile(self):
        import roz_telegram_upload as uploader
        import sales
        root=self.root
        older=self.bot/"videos"/"file_100"
        older.write_bytes(b"old-file")
        def fake_path(value):
            return root if str(value)=="/var/lib/telegram-bot-api" else Path(value)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(uploader,"Path",side_effect=fake_path))
            stack.enter_context(patch.object(sales,"telegram_local_enabled",return_value=True))
            stack.enter_context(patch.object(sales,"tg",side_effect=AssertionError("getFile must not run")))
            snapshot=uploader.local_cache_snapshot({"token":self.token},"videos")
            self.assertIsNotNone(snapshot)
            fresh=self.bot/"videos"/"file_101"
            fresh.write_bytes(b"confirmed-mp4-bytes")
            unrelated=self.bot/"videos"/"file_102"
            unrelated.write_bytes(b"different-length")
            self.assertTrue(uploader.cleanup_uploaded_from_snapshot(
                {"token":self.token},"videos",snapshot,len(b"confirmed-mp4-bytes")))
            self.assertFalse(fresh.exists())
            self.assertTrue(older.exists())
            self.assertTrue(unrelated.exists())

    def test_skips_files_held_open_by_process(self):
        old=self.file("file_300",50)
        st=old.stat()
        with patch.object(m,"opened_inodes",return_value={(st.st_dev,st.st_ino)}):
            out=self.invoke(True)
        self.assertEqual(out["music"]["files"],0)
        self.assertTrue(old.exists())


if __name__=="__main__":
    unittest.main()
