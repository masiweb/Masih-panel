#!/usr/bin/env python3
"""Read Film2Media's public pages and prepare verified, rights-gated film manifests.

Discovery alone never downloads a movie, uploads to Telegram or notifies users.
Staging is opt-in and requires explicit authorization for redistribution.
"""
import argparse
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import urljoin, urlsplit, unquote
from urllib.request import Request, build_opener
from pathlib import Path

import film_importer as importer

PAGE_HOSTS = {"f2mgo.top", "www.f2mgo.top"}
POST_PATH = re.compile(r"^/(\d{3,})/[^/]+/?$")
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".mov"}
QUALITY_RE = re.compile(r"(?<!\d)(480|720|1080|1440|2160)p(?!\w)", re.I)
EPISODE_RE = re.compile(r"S(\d{1,2})[._ -]?E(\d{1,3})", re.I)
USER_AGENT = "MasihaFilmDiscovery/1.0 (+public-pages; authorized-assets-only)"
MAX_PAGE_BYTES = 1_500_000


class Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.anchors = []
        self.h1s = []
        self.ldjson = []
        self._anchor = None
        self._h1 = None
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta.setdefault(key.lower(), attrs["content"].strip())
        elif tag == "a":
            self._anchor = [attrs.get("href", ""), ""]
        elif tag == "h1":
            self._h1 = ""
        elif tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._script = ""

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1] += data
        if self._h1 is not None:
            self._h1 += data
        if self._script is not None:
            self._script += data

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self.anchors.append(tuple(self._anchor))
            self._anchor = None
        elif tag == "h1" and self._h1 is not None:
            if self._h1.strip():
                self.h1s.append(self._h1.strip())
            self._h1 = None
        elif tag == "script" and self._script is not None:
            try:
                self.ldjson.append(json.loads(self._script))
            except (ValueError, TypeError):
                pass
            self._script = None


def read_page(url):
    importer.valid_url(url, PAGE_HOSTS)
    opener = build_opener(importer.SafeRedirect(PAGE_HOSTS))
    with opener.open(Request(url, headers={"User-Agent": USER_AGENT}), timeout=25) as res:
        importer.valid_url(res.url, PAGE_HOSTS)
        if res.status != 200:
            raise ValueError("Source page returned HTTP " + str(res.status))
        ctype = res.headers.get("Content-Type", "").lower()
        if "text/html" not in ctype:
            raise ValueError("Source did not return HTML")
        raw = res.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Source HTML too large")
    doc = Extract()
    doc.feed(raw.decode("utf-8", "replace"))
    if any("captcha" in x.lower() or "bot verification" in x.lower()
           for x in doc.h1s):
        raise ValueError("Source is a bot-verification page")
    return doc


def video_info(url):
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    ext = Path(filename).suffix.lower()
    if ext not in VIDEO_EXT:
        return None
    match = QUALITY_RE.search(filename)
    if not match:
        return None
    episode = EPISODE_RE.search(filename)
    version = []
    for part in ("WEB-DL", "WEBRip", "BluRay", "10bit", "x265", "x264",
                 "HEVC", "HDR", "6CH", "YIFY", "PSA"):
        if re.search(r"(?<![a-zA-Z0-9])" + re.escape(part) + r"(?![a-zA-Z0-9])",
                     filename, re.I):
            version.append(part)
    dub = bool(re.search(r"2Dubbed|Dubbed|Dooble|DUB", filename, re.I))
    subs = bool(re.search(r"Farsi[. _-]*Sub|Persian[. _-]*Sub|HardSub",
                          filename, re.I))
    return {
        "quality": match.group(1) + "p",
        "version": " ".join(version)[:90],
        "dubbed": "yes" if dub else "no" if subs else "unknown",
        "subtitles": "hardsub" if subs else "none" if dub else "unknown",
        "language": "fa" if dub else "original",
        "send_method": "sendDocument" if ext == ".mkv" else "sendVideo",
        "url": url,
        "season": int(episode.group(1)) if episode else 0,
        "episode": int(episode.group(2)) if episode else 0,
    }


def probe(url, hosts):
    """HEAD only. Follow only previously allowlisted domains and public IPs."""
    importer.valid_url(url, hosts)
    opener = build_opener(importer.SafeRedirect(hosts))
    req = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    with opener.open(req, timeout=20) as response:
        importer.valid_url(response.url, hosts)
        if response.status != 200:
            raise ValueError("Media HEAD HTTP " + str(response.status))
        ctype = response.headers.get("Content-Type", "").lower().split(";")[0]
        if ctype in {"text/html", "application/json", "text/plain"}:
            raise ValueError("HEAD returned non-media content")
        length = response.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) <= 0:
            raise ValueError("Media content length missing")
        return int(length)


def schema_image(doc):
    for schema in doc.ldjson:
        objects = schema.get("@graph", [schema]) if isinstance(schema, dict) else []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") == "ImageObject":
                url = obj.get("contentUrl") or obj.get("url")
                if isinstance(url, str):
                    return url
            if obj.get("@type") == "WebPage" and isinstance(obj.get("thumbnailUrl"), str):
                return obj["thumbnailUrl"]
    return doc.meta.get("og:image", "")


def manifest_for_page(url, hosts, approved=False, verify_sizes=True, page_doc=None):
    path = urlsplit(url).path
    match = POST_PATH.fullmatch(path)
    if not match:
        raise ValueError("Expected a Film2Media movie/episode article URL")
    doc = page_doc if page_doc is not None else read_page(url)
    title = (doc.h1s[0] if doc.h1s else doc.meta.get("og:title", "")).strip()
    title = re.sub(r"^دانلود (?:فیلم|سریال)\s+", "", title).strip()
    title = re.sub(r"\s+(?:بدون سانسور|با زیرنویس فارسی|دوبله فارسی).*$", "", title).strip()
    if len(title) < 2:
        raise ValueError("Missing title")
    year_match = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", title)
    year = int(year_match.group(1)) if year_match else 0
    original = re.sub(r"[^\x00-\x7F]+", " ", title).strip()
    media_type = "series" if re.search(r"سریال|series", doc.meta.get("og:title", ""), re.I) else "movie"
    cover = schema_image(doc)
    # Missing/disallowed cover is optional, never blocks valid video sources.
    try:
        importer.valid_url(cover, hosts)
    except (ValueError, OSError):
        cover = ""
    variants, skipped, seen = [], [], set()
    for href, _ in doc.anchors:
        link = urljoin(url, href.strip())
        info = video_info(link)
        if info is None or link in seen:
            continue
        seen.add(link)
        try:
            importer.valid_url(link, hosts)
            if media_type == "series" and not info["season"]:
                raise ValueError("Episode number not identified")
            if media_type == "movie" and info["season"]:
                raise ValueError("Episode link on movie page")
            if verify_sizes:
                size = probe(link, hosts)
                if size > importer.MAX_FILE:
                    raise ValueError("Exceeds Telegram local upload limit")
                info["file_bytes_expected"] = size
            # Manifest accepts known, stable keys only; expected size is
            # informational and intentionally not stored as a DB field.
            variants.append(info)
        except (ValueError, OSError) as e:
            skipped.append({"quality": info["quality"], "version": info["version"],
                            "dubbed": info["dubbed"], "subtitles": info["subtitles"],
                            "reason": str(e)})
    if not variants:
        raise ValueError("No safe and uploadable video variants on the page")
    genres = list(dict.fromkeys(text.strip() for href, text in doc.anchors
                  if "/genres/" in urlsplit(urljoin(url, href)).path
                  and text.strip()))[:12]
    entry = {
        "source": importer.SOURCE,
        "external_key": "post-" + match.group(1),
        "rights_ok": bool(approved),
        "media_type": media_type,
        "title": title[:240],
        "original_title": original[:240],
        "year": year,
        "genre": "، ".join(genres)[:350],
        "director": "",
        "actors": "",
        "description": doc.meta.get("og:description", "")[:3500],
        "cover_url": cover,
        "source_page": url,
        "variants": variants,
    }
    return entry, skipped


def feed_urls(home, limit=5):
    doc = read_page(home)
    out = []
    for link, _ in doc.anchors:
        url = urljoin(home, link.strip())
        if urlsplit(url).hostname in PAGE_HOSTS and POST_PATH.fullmatch(urlsplit(url).path):
            if url not in out:
                out.append(url)
            if len(out) >= limit:
                break
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--page", help="Single Film2Media article")
    group.add_argument("--feed", action="store_true", help="Read newest public homepage articles")
    parser.add_argument("--limit", type=int, default=5, help="Maximum homepage articles (1..10)")
    parser.add_argument("--output", help="Write manifest as JSON for review")
    parser.add_argument("--stage", action="store_true", help="Put authorized, verified versions into DB queue")
    parser.add_argument("--rights-approved", action="store_true",
                        help="Operator confirms redistribution rights for a SINGLE page")
    parser.add_argument("--approved-ids-file",
                        help="JSON array of approved Film2Media post IDs for automated feed import")
    parser.add_argument("--no-head", action="store_true", help="Metadata preview only: do not probe sizes")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")
    if args.feed and args.rights_approved:
        parser.error("Automated feed must use reviewed --approved-ids-file, not blanket rights")
    if args.stage and (args.no_head or not (args.rights_approved or args.approved_ids_file)):
        parser.error("Staging requires verified sizes and explicit approved posts")
    approved_ids = set()
    if args.approved_ids_file:
        data = json.loads(Path(args.approved_ids_file).read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) > 10000 or any(
            not isinstance(v, str) or not re.fullmatch(r"\d{3,}", v) for v in data
        ):
            raise ValueError("Approved post IDs must be a JSON list of numeric strings")
        approved_ids = set(data)
    hosts = importer.settings()["hosts"]
    pages = [args.page] if args.page else feed_urls("https://www.f2mgo.top/", args.limit)
    manifest, errors, skipped = [], [], []
    for url in pages:
        try:
            match = POST_PATH.fullmatch(urlsplit(url).path)
            approved = args.rights_approved or bool(match and match.group(1) in approved_ids)
            item, exclusions = manifest_for_page(url, hosts, approved, not args.no_head)
            skipped.extend({"page": url, **s} for s in exclusions)
            manifest.append(item)
        except (ValueError, OSError) as e:
            errors.append({"page": url, "reason": str(e)})
        if args.feed:
            time.sleep(1)
    if args.output:
        target = Path(args.output)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    if args.stage and manifest:
        with importer.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                queued = sum(importer.stage(c, item, hosts)
                             for item in manifest if item["rights_ok"])
            except Exception:
                c.rollback()
                raise
            c.commit()
    else:
        queued = 0
    print(json.dumps({"titles": len(manifest),
                       "variants": sum(len(i["variants"]) for i in manifest),
                       "skipped": skipped, "errors": errors, "queued": queued,
                       "manifest": manifest if not args.output else args.output},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
