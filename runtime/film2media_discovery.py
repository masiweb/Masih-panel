#!/usr/bin/env python3
"""Extract Film2Media public post metadata and video links without downloading.

Returned manifest is unapproved: publishing requires explicit rights approval.
"""
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PAGE_HOSTS = {"f2mgo.top", "www.f2mgo.top"}
VIDEO_EXTS = (".mkv", ".mp4", ".webm", ".mov")
MAX_HTML = 2_000_000


def check_page(url):
    p = urlsplit(url)
    if (not isinstance(url, str) or len(url)>1800 or p.scheme!="https"
        or p.hostname not in PAGE_HOSTS or p.username or p.password
        or p.port not in (None,443) or p.fragment):
        raise ValueError("Only public Film2Media HTTPS pages are accepted")
    return url


class SafePageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_page(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class FilmHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta={};self.scripts=[];self.script=None
        self.h1="";self.in_h1=False;self.poster="";self.downloads=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="meta":
            k=a.get("property") or a.get("name")
            if k and a.get("content"):self.meta[k.lower()]=a["content"]
        if tag=="img" and not self.poster and "wp-post-image" in a.get("class",""):
            self.poster=a.get("src","")
        if tag=="h1" and "entry-title" in a.get("class",""):self.in_h1=True
        if tag=="script" and a.get("type")=="application/ld+json":self.script=[]
        if tag=="a" and "download" in a:
            link=unescape(a.get("href",""))
            if urlsplit(link).path.lower().endswith(VIDEO_EXTS):
                self.downloads.append(link)
    def handle_endtag(self,tag):
        if tag=="h1":self.in_h1=False
        if tag=="script" and self.script is not None:
            self.scripts.append("".join(self.script));self.script=None
    def handle_data(self,data):
        if self.in_h1:self.h1+=data
        if self.script is not None:self.script.append(data)


def schema_poster(scripts):
    for raw in scripts:
        try:schema=json.loads(raw)
        except (ValueError,TypeError):continue
        if not isinstance(schema,dict):continue
        for node in schema.get("@graph",[schema]):
            if not isinstance(node,dict):continue
            if node.get("@type")=="WebPage":url=node.get("thumbnailUrl")
            elif node.get("@type")=="ImageObject":url=node.get("contentUrl") or node.get("url")
            else:continue
            if isinstance(url,str) and url.startswith("https://"):return url
    return ""


def parse_post(html,url):
    """No I/O; returns all distinct direct video quality/version combinations."""
    check_page(url)
    if isinstance(html,bytes):html=html.decode("utf-8","replace")
    if not isinstance(html,str) or len(html)>MAX_HTML:raise ValueError("Invalid HTML")
    p=FilmHTML();p.feed(html)
    if not p.downloads:raise ValueError("No direct video links; possibly blocked page")
    original=" ".join(unescape(p.h1).split())
    title=unescape(p.meta.get("og:title","") or original).strip()
    title=re.sub(r"^دانلود\s+(?:فیلم|سریال)\s+","",title)
    title=re.split(r"\s+(?:بدون سانسور|با زیرنویس|با دوبله|دانلود رایگان)\b",title,maxsplit=1)[0].strip()
    year=re.search(r"\b(?:19|20)\d{2}\b",original or title)
    pic=schema_poster(p.scripts) or p.poster or p.meta.get("og:image","")
    poster=pic if pic.startswith("https://") else ""
    genre=""
    section=re.search(r'<div[^>]*class=["\'][^"\']*entry-genres[^"\']*["\'][^>]*>(.{0,3500})',html,re.I|re.S)
    if section:
        names=[unescape(re.sub(r"<[^>]+>","",v)).strip() for v in re.findall(
            r'<a\b[^>]*href=["\'][^"\']*/genres/[^"\']*["\'][^>]*>(.*?)</a>',section.group(1),re.I|re.S)]
        genre="، ".join(dict.fromkeys(n for n in names if n))[:350]
    match=re.search(r"/(\d{3,12})/[^/?#]+/?$",urlsplit(url).path)
    key="f2mgo-"+(match.group(1) if match else re.sub(
        r"[^a-z0-9-]","-",urlsplit(url).path.lower()).strip("-")[:140])
    versions=[];seen=set()
    for link in p.downloads:
        filename=urlsplit(link).path.rsplit("/",1)[-1]
        q=re.search(r"(?<!\d)(480|720|1080|1440|2160)p(?!\d)",filename,re.I)
        if not q:continue
        path=urlsplit(link).path
        dubbed=("yes" if re.search(r"/(?:2DUB|GDUB|DUB)/|(?:2dubbed|dubbed)",path,re.I)
                else "no" if re.search(r"/SUB/|farsi\.sub|/ORG/",path,re.I)
                else "unknown")
        subs=("hardsub" if re.search(r"/SUB/|farsi\.sub|hardsub",path,re.I)
              else "softsub" if re.search("softsub",path,re.I) else "none")
        version=" ".join(label for pat,label in [
            ("10bit","10bit"),("x265|h265|hevc","x265"),
            ("x264|h264","x264"),("WEB[-.]DL","WEB-DL"),
            ("BluRay","BluRay"),("PSA","PSA"),("YIFY","YIFY")]
            if re.search(pat,filename,re.I))
        episode=re.search(r"S(\d{1,2})[. _-]?E(\d{1,3})",filename,re.I)
        season,ep=(int(episode.group(1)),int(episode.group(2))) if episode else (0,0)
        signature=(season,ep,q.group(1),version,dubbed,subs)
        if signature in seen:continue
        seen.add(signature)
        versions.append({"quality":q.group(1)+"p","url":link,"version":version,
            "dubbed":dubbed,"subtitles":subs,"season":season,"episode":ep,
            "send_method":"sendDocument"})
    if not versions:raise ValueError("No named video qualities in post")
    kind="series" if any(v["season"] for v in versions) else "movie"
    if kind=="series" and any(not v["season"] or not v["episode"] for v in versions):
        raise ValueError("Series video needs an SxxExx filename")
    return {"source":"film2media","rights_ok":False,"source_page":url,
        "external_key":key,"media_type":kind,"title":(title or original or key)[:240],
        "original_title":original[:240],"year":int(year.group()) if year else 0,
        "genre":genre,"director":"","actors":"",
        "description":p.meta.get("og:description","")[:3500],
        "rating":None,"cover_url":poster,"variants":versions[:100]}


def discover(url):
    check_page(url)
    opener=build_opener(SafePageRedirect())
    with opener.open(Request(url,headers={"User-Agent":"Mozilla/5.0",
                        "Accept":"text/html"}),timeout=30) as response:
        check_page(response.url)
        if response.status!=200 or "text/html" not in response.headers.get("Content-Type",""):
            raise ValueError("No accessible Film2Media HTML")
        content=response.read(MAX_HTML+1)
    if len(content)>MAX_HTML:raise ValueError("Film page exceeds safety limit")
    return parse_post(content,url)
