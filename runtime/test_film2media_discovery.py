"""Offline regression tests for Film2Media listing, parsing and upload safety."""
import unittest
from unittest.mock import patch
import film2media_discovery as d

PAGE = "https://www.f2mgo.top/88304/tenzing-2026/"
HOSTS = {"www.f2mgo.top", "media.example.com", "intocdn.top"}
HTML = """
<html><head>
<meta property="og:title" content="دانلود فیلم تنزینگ Tenzing 2026"/>
<meta property="og:description" content="داستان صعود اورست"/>
<script type="application/ld+json">{"@graph":[
 {"@type":"ImageObject","url":"https://intocdn.top/cover.jpg"}]}</script>
</head><body><h1>Tenzing 2026</h1>
<a href="https://www.f2mgo.top/genres/drama/">درام</a>
<a href="https://media.example.com/Movie/Tenzing.2026.720p.WEB-DL.Farsi.2Dubbed.mkv">دانلود</a>
<a href="https://media.example.com/Movie/Tenzing.2026.720p.10bit.x265.Farsi.2Dubbed.mkv">دانلود</a>
<a href="https://media.example.com/Movie/Tenzing.2026.1080p.10bit.x265.Farsi.Sub.mkv">دانلود</a>
<a href="https://media.example.com/Movie/Tenzing.2026.Dubbed.Audio.mka">صوت</a>
</body></html>
"""


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.doc = d.Extract()
        self.doc.feed(HTML)
        self.ip = patch.object(d.importer, "valid_url", side_effect=lambda u, hosts: u)
        self.ip.start()
        self.addCleanup(self.ip.stop)

    def test_versions_and_subtitle_labels(self):
        variants = [d.video_info(link) for link, _ in self.doc.anchors]
        v = [a for a in variants if a]
        self.assertEqual(len(v), 3)
        self.assertEqual([a["quality"] for a in v], ["720p","720p","1080p"])
        self.assertEqual(v[0]["dubbed"], "yes")
        self.assertEqual(v[1]["version"], "10bit x265")
        self.assertEqual(v[2]["subtitles"], "hardsub")
        self.assertEqual(v[0]["send_method"], "sendDocument")

    def test_large_file_excluded_not_downloaded(self):
        sizes = iter([1_200_000_000, 1_700_000_000, 2_400_000_000])
        with patch.object(d, "probe", side_effect=lambda *args: next(sizes)) as p:
            entry, skipped = d.manifest_for_page(PAGE, HOSTS, False, True, self.doc)
        self.assertEqual(len(entry["variants"]), 2)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(entry["genre"], "درام")
        self.assertEqual(entry["year"], 2026)
        self.assertFalse(entry["rights_ok"])
        self.assertEqual(entry["cover_url"], "https://intocdn.top/cover.jpg")
        self.assertEqual(p.call_count, 3)

    def test_rights_explicit_not_inferred_from_page(self):
        with patch.object(d, "probe", return_value=550_000_000):
            item, _ = d.manifest_for_page(PAGE, HOSTS, False, True, self.doc)
        self.assertFalse(item["rights_ok"])
        self.assertTrue(d.manifest_for_page(PAGE, HOSTS, True, True, self.doc)[0]["rights_ok"])

    def test_filename_ignores_unrelated_assets(self):
        self.assertIsNone(d.video_info("https://media.example.com/movie.Dubbed.Audio.mka"))
        self.assertIsNone(d.video_info("https://media.example.com/subtitle.srt"))
        self.assertIsNone(d.video_info("https://media.example.com/720p.html"))
        self.assertIsNone(d.video_info("https://media.example.com/evil.exe"))

    def test_latest_links_deduped(self):
        self.doc.anchors = [(PAGE, "Tenzing"), (PAGE, "again"),
                            ("https://www.f2mgo.top/11123/film/", "film")]
        with patch.object(d, "read_page", return_value=self.doc):
            self.assertEqual(d.feed_urls("https://www.f2mgo.top/", 5),
                             [PAGE, "https://www.f2mgo.top/11123/film/"])


if __name__ == "__main__":
    unittest.main()
