#!/usr/bin/env python3
"""Hourly public catalog scan; only reviewed post IDs can enter the upload queue."""
import argparse
from html.parser import HTMLParser
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener

import film_importer as films
from film2media_discovery import discover, check_page, SafePageRedirect

POST = re.compile(r"^/(\d{3,12})/[^/]+/?$")
SITE = "https://www.f2mgo.top/"
UA = "MasihaFilmCatalog/1.0"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            url = dict(attrs).get("href", "")
            if url:
                self.urls.append(url)


def recent_posts(limit=10):
    check_page(SITE)
    op = build_opener(SafePageRedirect())
    with op.open(Request(SITE, headers={"User-Agent": UA}), timeout=25) as r:
        check_page(r.url)
        if r.status != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            raise RuntimeError("Film2Media home page not reachable")
        data = r.read(1_500_001)
        if len(data) > 1_500_000:
            raise ValueError("Film2Media home page too large")
    parser = Links()
    parser.feed(data.decode("utf-8", "replace"))
    found = []
    for href in parser.urls:
        url = urljoin(SITE, href)
        parts = urlsplit(url)
        if parts.hostname in ("www.f2mgo.top", "f2mgo.top") and POST.fullmatch(parts.path):
            if url not in found:
                found.append(url)
            if len(found) >= limit:
                break
    return found


def media_head(url, hosts):
    films.valid_url(url, hosts)
    with build_opener(films.SafeRedirect(hosts)).open(
        Request(url, headers={"User-Agent": UA}, method="HEAD"), timeout=22
    ) as r:
        films.valid_url(r.url, hosts)
        if r.status != 200:
            raise ValueError("Media HEAD rejected")
        if r.headers.get("Content-Type", "").split(";")[0].lower() in (
            "text/html", "application/json", "text/plain"
        ):
            raise ValueError("Media HEAD was not a video")
        length = r.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) < 65536:
            raise ValueError("Unknown media length")
        return int(length)


def approved_ids(path):
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list) or len(values) > 10000 or any(
        not isinstance(v, str) or not re.fullmatch(r"\d{3,12}", v) for v in values
    ):
        raise ValueError("Approvals must be a JSON list of numeric post IDs")
    return set(values)


def scan(limit, approval_path):
    conf = films.settings()
    rights = approved_ids(approval_path)
    report = {"scanned": [], "errors": [], "eligible_variants": 0, "queued": 0}
    for url in recent_posts(limit):
        try:
            manifest = discover(url)
            post = POST.fullmatch(urlsplit(url).path).group(1)
            approved = post in rights
            usable, omitted = [], []
            for variant in manifest["variants"]:
                try:
                    size = media_head(variant["url"], conf["hosts"])
                    if size > films.MAX_FILE:
                        omitted.append({"quality": variant["quality"],
                                        "version": variant.get("version", ""),
                                        "reason": "over_1950MB", "bytes": size})
                    else:
                        usable.append(variant)
                except (ValueError, OSError) as exc:
                    omitted.append({"quality": variant["quality"],
                                    "version": variant.get("version", ""),
                                    "reason": type(exc).__name__})
            result = {"post": post, "title": manifest["title"], "source_page": url,
                      "approved": approved, "uploadable": len(usable),
                      "skipped": omitted}
            report["eligible_variants"] += len(usable)
            if approved and usable:
                manifest["rights_ok"] = True
                manifest["variants"] = usable
                with films.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        staged = films.stage(db, manifest, conf["hosts"])
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise
                report["queued"] += staged
                result["queued"] = staged
            report["scanned"].append(result)
        except (ValueError, OSError, RuntimeError) as exc:
            report["errors"].append({"page": url, "reason": type(exc).__name__})
        time.sleep(1)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--approved-ids-file", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    if not 1 <= args.limit <= 10:
        p.error("--limit must be 1..10")
    result = scan(args.limit, args.approved_ids_file)
    target = Path(args.output)
    # Private directory is created by installer; atomic report replace.
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(target)
    print("FILM2MEDIA_SCAN",json.dumps(
        {k: result[k] for k in ("eligible_variants", "queued")},ensure_ascii=False))


if __name__ == "__main__":
    main()
