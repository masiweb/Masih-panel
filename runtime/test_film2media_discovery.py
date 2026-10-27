"""Pure offline tests for Film2Media metadata, subtitles, codecs and safety."""
import json
import unittest
from film2media_discovery import parse_post, check_page

PAGE = "https://www.f2mgo.top/88304/tenzing-2026/"
HEAD = """<meta property="og:title" content="دانلود فیلم تنزینگ: فاتح اورست Tenzing 2026 بدون سانسور">
<meta property="og:description" content="داستان تنزینگ">
<script type="application/ld+json">{"@type":"WebPage","thumbnailUrl":"https://intocdn.top/poster.jpg"}</script>
<div class="entry-genres"><a href="/genres/drama/">درام</a><a href="/genres/adventure/">ماجراجویی</a></div>
<h1 class="entry-title">Tenzing 2026</h1>"""
def a(path):
    return '<a download href="https://videos.example.com/' + path + '">دانلود مستقیم</a>'

class Film2MediaDiscoveryTests(unittest.TestCase):
    def test_quality_subtitle_codec_and_cover(self):
        body=HEAD+a("Movie/SUB/Tenzing.2026.480p.WEB-DL.Farsi.Sub.mkv")+a(
            "Movie/2DUB/Tenzing.2026.720p.10bit.x265.Dubbed.mkv")+a(
            "Movie/2DUB/Tenzing.2026.1080p.Dubbed.mkv")+a(
            "Movie/SUB/Tenzing.2026.audio.mka")
        item=parse_post(body,PAGE)
        self.assertFalse(item["rights_ok"])
        self.assertEqual(item["media_type"],"movie")
        self.assertEqual(item["year"],2026)
        self.assertEqual(item["genre"],"درام، ماجراجویی")
        self.assertEqual(item["cover_url"],"https://intocdn.top/poster.jpg")
        self.assertEqual(len(item["variants"]),3)
        self.assertEqual(item["variants"][0]["subtitles"],"hardsub")
        self.assertEqual(item["variants"][1]["dubbed"],"yes")
        self.assertIn("x265",item["variants"][1]["version"])
    def test_no_video_and_bad_page(self):
        with self.assertRaises(ValueError):parse_post(HEAD,PAGE)
        for link in ("https://localhost/movie/","http://www.f2mgo.top/movie/",
                     "https://www.f2mgo.top.evil.test/post/"):
            with self.assertRaises(ValueError):check_page(link)
    def test_deduplicates_same_variant(self):
        link=a("Movie/SUB/Tenzing.2026.720p.Farsi.Sub.mkv")
        self.assertEqual(len(parse_post(HEAD+link+link,PAGE)["variants"]),1)
    def test_episodes_require_complete_season_id(self):
        html=HEAD+a("Series/S01E03.720p.WEB-DL.mkv")
        item=parse_post(html,PAGE)
        self.assertEqual(item["media_type"],"series")
        self.assertEqual((item["variants"][0]["season"],item["variants"][0]["episode"]),(1,3))

if __name__=="__main__":unittest.main()
