#!/usr/bin/python3
"""Masiha film pipeline: per-title metadata, per-episode quality, bot-scoped file IDs.

Input is an operator-reviewed JSON manifest with licensed direct HTTPS file URLs.
Film2Media access is checked directly; download only approved video versions.
"""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

MAX_FILE = 1_950_000_000
RESERVE = 3 * 1024 * 1024 * 1024
CHUNK = 256 * 1024
SOURCE = "film2media"
USER_AGENT = "MasihaLicensedFilmTransfer/1.0"
LOCAL_API = "127.0.0.1"
LOCAL_API_PORT = 8081
TEMP = Path("/var/tmp/masiha-films")
CONFIG = Path("/etc/masiha-vpn-extension/sales/film_import.json")
QUALITY = re.compile(r"^(?:480|720|1080|1440|2160)p$")
SAFE_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,159}$")
VIDEO_TYPES = {"sendVideo": "video", "sendDocument": "document"}

def now():
    return int(time.time())

def connect():
    import sales
    c = sales.conn()
    setup(c)
    return c

def setup(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS film_titles(
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_key TEXT NOT NULL,
        media_type TEXT NOT NULL CHECK(media_type IN ('movie','series')),
        title TEXT NOT NULL, original_title TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '', year INTEGER NOT NULL DEFAULT 0,
        director TEXT NOT NULL DEFAULT '', actors TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '', rating REAL,
        rating_source TEXT NOT NULL DEFAULT '', cover_url TEXT NOT NULL DEFAULT '',
        cover_id TEXT NOT NULL DEFAULT '', rights_ok INTEGER NOT NULL DEFAULT 0,
        source_page TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL,
        UNIQUE(source,external_key)
    );
    CREATE TABLE IF NOT EXISTS film_variants(
        id INTEGER PRIMARY KEY, title_id INTEGER NOT NULL,
        season INTEGER NOT NULL DEFAULT 0, episode INTEGER NOT NULL DEFAULT 0,
        quality TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
        dubbed TEXT NOT NULL DEFAULT 'unknown', subtitles TEXT NOT NULL DEFAULT 'unknown',
        language TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL,
        send_method TEXT NOT NULL DEFAULT 'sendDocument'
          CHECK(send_method IN ('sendDocument','sendVideo')),
        file_id TEXT NOT NULL DEFAULT '', file_unique_id TEXT NOT NULL DEFAULT '',
        file_bytes INTEGER NOT NULL DEFAULT 0, uploaded INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL,
        UNIQUE(title_id,season,episode,quality,version,dubbed,subtitles,language)
    );
    CREATE TABLE IF NOT EXISTS film_import_jobs(
        variant_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending'
           CHECK(state IN ('pending','processing','uploaded','done','failed','blocked')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS film_jobs_state ON film_import_jobs(state,updated);
    CREATE INDEX IF NOT EXISTS film_variant_title ON film_variants(title_id,season,episode);
    """)
    c.commit()

def settings():
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise RuntimeError("Film import configuration unavailable") from None
    hosts = data.get("download_hosts", [])
    if not isinstance(hosts, list) or len(hosts) > 30:
        raise ValueError("Configure exact permitted download_hosts first")
    for host in hosts:
        if not isinstance(host,str) or not re.fullmatch(r"[a-zA-Z0-9.-]{3,253}",host) or ".." in host:
            raise ValueError("Unsafe permitted host")
    return {"hosts": {h.lower() for h in hosts},
            "local_bot_api": bool(data.get("local_bot_api", False))}

def valid_url(url, hosts):
    if not isinstance(url,str) or len(url)>1800:
        raise ValueError("Invalid video URL")
    p=urlsplit(url)
    if p.scheme!="https" or p.username or p.password or p.port not in (None,443) or p.fragment:
        raise ValueError("Only HTTPS media URLs supported")
    h=(p.hostname or "").lower()
    if h not in hosts:
        raise ValueError("Source host not authorized in film_import.json")
    infos=socket.getaddrinfo(h,443,proto=socket.IPPROTO_TCP)
    if not infos or any(not ipaddress.ip_address(x[4][0]).is_global for x in infos):
        raise ValueError("Private/reserved media destination forbidden")
    return url

class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts=hosts
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_url(newurl,self.hosts)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _str(obj, key, limit):
    val=obj.get(key,"")
    if not isinstance(val,str) or len(val)>limit:
        raise ValueError("Bad "+key)
    return val.strip()

def stage(c, entry, hosts):
    """Parse verified manifest; no network, Telegram or user notifications."""
    if not isinstance(entry,dict) or entry.get("source")!=SOURCE:
        raise ValueError("Invalid film manifest source")
    if entry.get("rights_ok") is not True:
        raise ValueError("Explicit rights_ok:true required")
    key=_str(entry,"external_key",160)
    if not SAFE_KEY.fullmatch(key):raise ValueError("Invalid external_key")
    typ=entry.get("media_type")
    if typ not in ("movie","series"):raise ValueError("Invalid film type")
    title=_str(entry,"title",240)
    if len(title)<2:raise ValueError("Missing film title")
    rating=entry.get("rating")
    if rating is not None and (type(rating) not in (int,float) or not 0 <= rating <= 10):
        raise ValueError("Invalid rating")
    yr=entry.get("year",0)
    if type(yr) is not int or not 0<=yr<=2100:raise ValueError("Invalid year")
    variants=entry.get("variants")
    if not isinstance(variants,list) or not 1<=len(variants)<=100:
        raise ValueError("Provide valid film variants")
    titlevals=(SOURCE,key,typ,title,_str(entry,"original_title",240),
            _str(entry,"genre",350),yr,_str(entry,"director",250),
            _str(entry,"actors",600),_str(entry,"description",3500),rating,
            _str(entry,"rating_source",80),_str(entry,"cover_url",1800),
            _str(entry,"source_page",1800),now())
    prepared=[]
    for var in variants:
        if not isinstance(var,dict):raise ValueError("Invalid film variant")
        quality=var.get("quality")
        if not isinstance(quality,str) or not QUALITY.fullmatch(quality):
            raise ValueError("Invalid video quality")
        season=var.get("season",0);episode=var.get("episode",0)
        if type(season)!=int or type(episode)!=int or not 0<=season<200 or not 0<=episode<5000:
            raise ValueError("Invalid season/episode")
        if (typ=="series")!=(season>0 and episode>0):
            raise ValueError("Season and episode must match film type")
        dubbed=var.get("dubbed","unknown")
        if dubbed not in ("yes","no","unknown"):raise ValueError("Invalid dubbed")
        subs=var.get("subtitles","unknown")
        if subs not in ("none","hardsub","softsub","external","unknown"):
            raise ValueError("Invalid subtitle")
        method=var.get("send_method","sendDocument")
        if method not in VIDEO_TYPES:raise ValueError("Invalid send method")
        url=valid_url(var.get("url"),hosts)
        prepared.append((season,episode,quality,_str(var,"version",90),
            dubbed,subs,_str(var,"language",50),url,method,now()))
    if entry.get("cover_url"):
        valid_url(entry["cover_url"],hosts)
    if entry.get("source_page"):
        valid_url(entry["source_page"],hosts|{"f2mgo.top","www.f2mgo.top"})
    c.execute("""INSERT OR IGNORE INTO film_titles
      (source,external_key,media_type,title,original_title,genre,year,director,
      actors,description,rating,rating_source,cover_url,source_page,created,rights_ok)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",titlevals)
    parent=c.execute("SELECT id FROM film_titles WHERE source=? AND external_key=?",
                     (SOURCE,key)).fetchone()[0]
    added=0
    for v in prepared:
        cur=c.execute("""INSERT OR IGNORE INTO film_variants
          (title_id,season,episode,quality,version,dubbed,subtitles,language,
           source_url,send_method,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
           (parent,*v))
        if cur.rowcount:
            vid=cur.lastrowid
            c.execute("INSERT INTO film_import_jobs(variant_id,state,updated) VALUES(?,'pending',?)",(vid,now()))
            added+=1
    return added

def import_manifest(path):
    conf=settings()
    manifest=Path(path)
    if manifest.stat().st_size>2_000_000:raise ValueError("Manifest too large")
    data=json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data,list) or len(data)>500:raise ValueError("List of 1..500 titles required")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            added=sum(stage(c,item,conf["hosts"]) for item in data)
        except Exception:
            c.rollback()
            raise
        c.commit()
    return added

def _remaining_disk(path, incoming=0):
    available=shutil.disk_usage(path).free
    if available-incoming<RESERVE:raise RuntimeError("Insufficient disk free space")

def download(url,path,hosts):
    valid_url(url,hosts)
    openr=build_opener(SafeRedirect(hosts))
    request=Request(url,headers={"User-Agent":USER_AGENT,"Accept":"video/*,application/octet-stream"})
    total=0
    _remaining_disk(path.parent)
    with openr.open(request,timeout=35) as response:
        valid_url(response.url,hosts)
        if response.status!=200:raise RuntimeError("Video source HTTP "+str(response.status))
        expected=response.headers.get("Content-Length","")
        if expected.isdigit() and int(expected)>MAX_FILE:raise ValueError("Video exceeds Telegram local API limit")
        mime=(response.headers.get("Content-Type","").split(";")[0].strip().lower())
        if mime in ("text/html","application/json","text/plain"):
            raise ValueError("Video URL returned webpage, not media")
        with path.open("xb") as dest:
            while True:
                piece=response.read(CHUNK)
                if not piece:break
                total+=len(piece)
                if total>MAX_FILE:raise ValueError("Video exceeds 1950MB")
                if total%(24*CHUNK)==0:_remaining_disk(path.parent)
                dest.write(piece)
            dest.flush()
            os.fsync(dest.fileno())
    if total<64*1024:raise ValueError("Video file too small")
    if expected.isdigit() and int(expected)!=total:raise ValueError("Incomplete video download")
    return total

def upload(path,method,config,cfg,storage_chat):
    if not config["local_bot_api"]:raise RuntimeError("Local Bot API is not configured")
    if method not in VIDEO_TYPES:raise ValueError("Invalid method")
    # Intentionally use *local* Telegram API. Cloud multipart is limited to 50 MB.
    token=cfg.get("token")
    if not cfg.get("enabled") or not token:raise RuntimeError("Bot is not configured")
    size=path.stat().st_size
    if not 64*1024<=size<=MAX_FILE:raise ValueError("Invalid upload size")
    part=VIDEO_TYPES[method]
    boundary="MasihaFilm_"+os.urandom(12).hex()
    fields=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{storage_chat}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"disable_notification\"\r\n\r\ntrue\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{part}\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    end=f"\r\n--{boundary}--\r\n".encode()
    conn=http.client.HTTPConnection(LOCAL_API,LOCAL_API_PORT,timeout=850)
    try:
        conn.putrequest("POST","/bot"+token+"/"+method)
        conn.putheader("Content-Type","multipart/form-data; boundary="+boundary)
        conn.putheader("Content-Length",str(len(fields)+size+len(end)))
        conn.endheaders()
        conn.send(fields)
        with path.open("rb") as f:
            while partdata:=f.read(CHUNK):
                conn.send(partdata)
        conn.send(end)
        resp=conn.getresponse()
        data=resp.read(100_001)
        if resp.status!=200 or len(data)>100_000:raise RuntimeError("Telegram video upload HTTP "+str(resp.status))
        result=json.loads(data)
        if not result.get("ok"):raise RuntimeError("Telegram refused video upload")
        msg=result.get("result") or {}
        video=msg.get(part) or {}
        if method=="sendVideo" and isinstance(video,dict) and not video.get("file_id"):
            video=msg.get("document") or {}
        fid=video.get("file_id");uid=video.get("file_unique_id")
        if not isinstance(fid,str) or len(fid)<10 or not isinstance(uid,str) or len(uid)<5:
            raise RuntimeError("Telegram returned no persistent file IDs")
        return fid,uid,msg.get("message_id")
    finally:
        conn.close()

def upload_cover(url, hosts, cfg, storage_chat):
    """Store a compact cover in Telegram; raw bytes stay only in memory."""
    import io
    from PIL import Image,ImageOps
    from urllib.request import Request
    valid_url(url,hosts)
    openr=build_opener(SafeRedirect(hosts))
    with openr.open(Request(url,headers={"User-Agent":USER_AGENT,"Accept":"image/*"}),
                    timeout=20) as response:
        valid_url(response.url,hosts)
        if response.headers.get("Content-Length","").isdigit() and int(response.headers["Content-Length"])>2_500_000:
            raise ValueError("Cover too large")
        raw=response.read(2_500_001)
    if not 100<len(raw)<=2_500_000:
        raise ValueError("Invalid cover image bytes")
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>7_000_000:raise ValueError("Cover pixel limit")
        image.load()
        image=ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((600,800))
        out=io.BytesIO()
        image.save(out,format="JPEG",quality=80,optimize=True)
    photo=out.getvalue()
    if not 100<len(photo)<700_000:raise ValueError("Invalid compressed cover size")
    token=cfg.get("token")
    if not token:raise RuntimeError("Telegram bot token missing")
    boundary="MasihaFilmCover_"+os.urandom(9).hex()
    start=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{storage_chat}\r\n"
           f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"poster.jpg\"\r\n"
           f"Content-Type: image/jpeg\r\n\r\n").encode()
    finish=f"\r\n--{boundary}--\r\n".encode()
    conn=http.client.HTTPSConnection("api.telegram.org",context=ssl.create_default_context(),timeout=45)
    try:
        conn.putrequest("POST","/bot"+token+"/sendPhoto")
        conn.putheader("Content-Type","multipart/form-data; boundary="+boundary)
        conn.putheader("Content-Length",str(len(start)+len(photo)+len(finish)))
        conn.endheaders()
        conn.send(start);conn.send(photo);conn.send(finish)
        response=conn.getresponse()
        result=json.loads(response.read(60_000))
        if response.status!=200 or not result.get("ok"):raise RuntimeError("Telegram cover upload failed")
        pictures=result.get("result",{}).get("photo") or []
        if not pictures or not pictures[-1].get("file_id"):
            raise RuntimeError("Missing cover file_id")
        # Keep cover message in private storage until DB commit;
        # no local JPEG or source bytes are persisted.
        return pictures[-1]["file_id"]
    finally:
        conn.close()

def publish(c,variant_id):
    import media_library
    variant=c.execute("SELECT v.*,t.* FROM film_variants v JOIN film_titles t ON t.id=v.title_id WHERE v.id=?",
                       (variant_id,)).fetchone()
    if variant is None or not variant["file_id"]:raise ValueError("Not uploaded")
    if not variant["rights_ok"]:raise ValueError("Rights not granted")
    # One film row per movie, or per series episode; additional qualities share it.
    unique=hashlib.sha256(f"film:{variant['title_id']}:{variant['season']}:{variant['episode']}".encode()).hexdigest()
    r=c.execute("SELECT id,status FROM media_items WHERE kind='film' AND unique_id=?",(unique,)).fetchone()
    created=False
    if r:
        mid=r["id"]
        if r["status"]=="removed":raise ValueError("Film was removed by operator")
    else:
        mid=c.execute("""INSERT INTO media_items
           (owner_id,kind,telegram_method,file_id,unique_id,source_url,title,genre,
            media_type,season,episode,actors,year,language,dubbed,subtitles,director,
            description,cover_id,status,rights_ok,created)
           VALUES(0,'film',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'published',1,?)""",
           (variant["send_method"],variant["file_id"],unique,"",
            variant["title"],variant["genre"],variant["media_type"],
            variant["season"],variant["episode"],variant["actors"],variant["year"],
            variant["language"],variant["dubbed"],variant["subtitles"],variant["director"],
            variant["description"],variant["cover_id"],now())).lastrowid
        created=True
    c.execute("UPDATE film_variants SET uploaded=? WHERE id=?",(now(),variant_id))
    c.execute("UPDATE film_import_jobs SET state='done',last_error='',updated=? WHERE variant_id=?",(now(),variant_id))
    if created:
        media_library.broadcast_new_media(c,mid)
    return mid

def process_one(c, config):
    import sales
    import roz_telegram_upload as audio
    cfg=sales.settings(c)
    if not config["local_bot_api"]:
        return "waiting_for_local_bot_api"
    storage=audio.verified_storage_chat(cfg)
    job=c.execute("""SELECT j.variant_id,v.source_url,v.send_method
       FROM film_import_jobs j JOIN film_variants v ON v.id=j.variant_id
       WHERE j.state='pending' OR j.state='failed' AND j.attempts<3
       ORDER BY j.variant_id LIMIT 1""").fetchone()
    if not job:return "queue_empty"
    vid=job["variant_id"]
    c.execute("UPDATE film_import_jobs SET state='processing',updated=? WHERE variant_id=?",(now(),vid))
    c.commit()
    TEMP.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="job-",dir=TEMP) as d:
            extension=Path(urlsplit(job["source_url"]).path).suffix.lower()
            if extension not in (".mp4",".mkv",".m4v",".mov"):
                raise ValueError("Unsupported video extension")
            path=Path(d)/("media"+extension)
            download(job["source_url"],path,config["hosts"])
            # ffprobe only inspects the downloaded licensed file, never trusts HTTP MIME alone.
            import subprocess
            result=subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type",
                "-of","default=noprint_wrappers=1",str(path)],
                capture_output=True,text=True,timeout=35)
            if result.returncode or "codec_type=video" not in result.stdout:
                raise ValueError("Downloaded asset is not a video")
            fid,unique,message_id=upload(path,job["send_method"],config,cfg,storage)
            # Preserve idempotent ID state first, before touching user notifications.
            c.execute("""UPDATE film_variants SET file_id=?,file_unique_id=?,file_bytes=?
               WHERE id=?""",(fid,unique,path.stat().st_size,vid))
            c.execute("UPDATE film_import_jobs SET state='uploaded',updated=? WHERE variant_id=?",(now(),vid))
            c.commit()
            title=c.execute("SELECT t.id,t.cover_url,t.cover_id FROM film_titles t JOIN film_variants v ON v.title_id=t.id WHERE v.id=?",(vid,)).fetchone()
            if title and title["cover_url"] and not title["cover_id"]:
                try:
                    cover_id=upload_cover(title["cover_url"],config["hosts"],cfg,storage)
                    c.execute("UPDATE film_titles SET cover_id=? WHERE id=? AND cover_id=''",(cover_id,title["id"]))
                    c.commit()
                except (OSError,ValueError,RuntimeError,HTTPError,URLError,TimeoutError):
                    # Film publication is not lost if the optional cover fails.
                    print("FILM_COVER_DEFERRED",vid,flush=True)
            # TemporaryDirectory removes source media on both success and failure.
        c.execute("BEGIN IMMEDIATE")
        mid=publish(c,vid)
        c.commit()
        return "published:"+str(mid)
    except Exception as ex:
        c.rollback()
        # Never expose secrets or direct URLs in error logs.
        state="blocked" if isinstance(ex,ValueError) else "failed"
        c.execute("""UPDATE film_import_jobs SET state=?,attempts=attempts+1,
           last_error=?,updated=? WHERE variant_id=?""",(state,type(ex).__name__,now(),vid))
        c.commit()
        return state+":"+type(ex).__name__

def recover_uploaded(c):
    # A killed worker leaves the old processing lease, but the scratch file
    # is already gone; release only stale leases and retry a fresh download.
    c.execute("""UPDATE film_import_jobs SET state='failed',updated=?,last_error='worker_interrupted',
       attempts=attempts+1 WHERE state='processing' AND updated<?""",
       (now(),now()-3600))
    c.commit()
    rows=c.execute("""SELECT j.variant_id FROM film_import_jobs j JOIN film_variants v
      ON v.id=j.variant_id WHERE j.state IN ('uploaded','failed') AND LENGTH(v.file_id)>=10""").fetchall()
    for row in rows:
        c.execute("BEGIN IMMEDIATE")
        try:
            publish(c,row[0])
            c.commit()
        except Exception:
            c.rollback()
            raise
    return len(rows)

def stats(c):
    return {r[0]:r[1] for r in c.execute("SELECT state,COUNT(*) FROM film_import_jobs GROUP BY state")}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("setup","import-manifest","run-once","status"))
    parser.add_argument("path",nargs="?")
    args=parser.parse_args()
    if args.action=="import-manifest":
        if not args.path:parser.error("manifest path required")
        print("STAGED_VARIANTS",import_manifest(args.path))
        return
    with connect() as c:
        if args.action=="setup":print("SCHEMA_OK")
        elif args.action=="status":print(json.dumps(stats(c)))
        elif args.action=="run-once":
            recover_uploaded(c)
            print("WORKER_RESULT",process_one(c,settings()))
if __name__=="__main__":main()