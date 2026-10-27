"""Offline tests for safe scheduled discovery, exact approvals and file cap."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import film2media_feed as feed


class FeedTests(unittest.TestCase):
    def test_approved_post_ids(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "approved.json")
            p.write_text('["88304","88901"]',encoding="utf-8")
            self.assertEqual(feed.approved_ids(p), {"88304","88901"})
            p.write_text('["../../private"]',encoding="utf-8")
            with self.assertRaises(ValueError):
                feed.approved_ids(p)

    def test_extract_recent_posts_only(self):
        class FakeResponse:
            status = 200
            url = feed.SITE
            headers = {"Content-Type": "text/html; charset=UTF-8"}
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n): return (b'<a href="/88304/tenzing-2026/">T</a>'
                b'<a href="/88304/tenzing-2026/">duplicate</a>'
                b'<a href="https://evil.example/12345/bad/">bad</a>')
        class Opener:
            def open(self,*a,**kw): return FakeResponse()
        with patch.object(feed,"build_opener",return_value=Opener()),patch.object(
             feed,"check_page",side_effect=lambda x:x):
            self.assertEqual(feed.recent_posts(4),
                ["https://www.f2mgo.top/88304/tenzing-2026/"])

    def test_unreviewed_assets_not_staged_and_large_assets_skipped(self):
        item={"title":"Tenzing","variants":[
           {"quality":"720p","url":"https://cdn.example/a.mkv"},
           {"quality":"1080p","url":"https://cdn.example/b.mkv"}]}
        with patch.object(feed,"recent_posts",return_value=[
            "https://www.f2mgo.top/88304/tenzing-2026/"]), patch.object(
            feed,"approved_ids",return_value=set()), patch.object(
            feed.films,"settings",return_value={"hosts":{"cdn.example"}}), patch.object(
            feed,"discover",return_value=item), patch.object(
            feed,"media_head",side_effect=[1_200_000_000,2_100_000_000]), patch.object(
            feed.time,"sleep"), patch.object(feed.films,"connect") as db:
            result=feed.scan(2,"not-used")
        self.assertEqual(result["queued"],0)
        self.assertEqual(result["eligible_variants"],1)
        self.assertEqual(result["scanned"][0]["uploadable"],1)
        self.assertEqual(result["scanned"][0]["skipped"][0]["reason"],"over_1950MB")
        db.assert_not_called()


if __name__=="__main__":
    unittest.main()
