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
_DNS_CACHE = {}

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
        cover_id TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '',
        certification TEXT NOT NULL DEFAULT '', silent_import INTEGER NOT NULL DEFAULT 0,
        rights_ok INTEGER NOT NULL DEFAULT 0,
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
        file_bytes INTEGER NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0,
        width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
        video_codec TEXT NOT NULL DEFAULT '', audio_codec TEXT NOT NULL DEFAULT '',
        stream_ready INTEGER NOT NULL DEFAULT 0, uploaded INTEGER NOT NULL DEFAULT 0,
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
    CREATE TABLE IF NOT EXISTS film_scan_runs(
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, item_limit INTEGER NOT NULL DEFAULT 100,
        status TEXT NOT NULL DEFAULT 'queued', total INTEGER NOT NULL DEFAULT 0,
        processed INTEGER NOT NULL DEFAULT 0, discovered INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
        started INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0,
        add_status TEXT NOT NULL DEFAULT 'idle', add_total INTEGER NOT NULL DEFAULT 0,
        add_processed INTEGER NOT NULL DEFAULT 0, add_variants INTEGER NOT NULL DEFAULT 0,
        add_errors INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS film_scan_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,
        external_key TEXT NOT NULL, title TEXT NOT NULL, media_type TEXT NOT NULL,
        variant_count INTEGER NOT NULL DEFAULT 0, entry_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'discovered', error TEXT NOT NULL DEFAULT '',
        UNIQUE(scan_id,external_key)
    );
    CREATE INDEX IF NOT EXISTS film_scan_items_run ON film_scan_items(scan_id,state,id);
    """)
    # Online additive migrations for databases created by earlier releases.
    migrations={
      'film_titles': {'country':"TEXT NOT NULL DEFAULT ''",'certification':"TEXT NOT NULL DEFAULT ''",'silent_import':'INTEGER NOT NULL DEFAULT 0'},
      'film_variants': {'duration':'REAL NOT NULL DEFAULT 0','width':'INTEGER NOT NULL DEFAULT 0',
        'height':'INTEGER NOT NULL DEFAULT 0','video_codec':"TEXT NOT NULL DEFAULT ''",
        'audio_codec':"TEXT NOT NULL DEFAULT ''",'stream_ready':'INTEGER NOT NULL DEFAULT 0'},
    }
    for table, columns in migrations.items():
        existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for name, ddl in columns.items():
            if name not in existing:c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+ddl)
    media_cols={r[1] for r in c.execute('PRAGMA table_info(media_items)')}
    if media_cols and 'film_title_id' not in media_cols:
        c.execute("ALTER TABLE media_items ADD COLUMN film_title_id INTEGER NOT NULL DEFAULT 0")
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
        candidate=host[2:] if isinstance(host,str) and host.startswith("*.") else host
        if not isinstance(candidate,str) or not re.fullmatch(r"[a-zA-Z0-9.-]{3,253}",candidate) or ".." in candidate:
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
    allowed=(h in hosts or any(x.startswith("*.") and h.endswith("."+x[2:]) and h!=x[2:] for x in hosts))
    if not allowed:
        raise ValueError("Source host not authorized in film_import.json")
    cached=_DNS_CACHE.get(h)
    if cached and cached[0]>time.time():infos=cached[1]
    else:
        infos=socket.getaddrinfo(h,443,proto=socket.IPPROTO_TCP)
        _DNS_CACHE[h]=(time.time()+600,infos)
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
    if not isinstance(variants,list) or not 1<=len(variants)<=2000:
        raise ValueError("Provide 1..2000 film variants")
    titlevals=(SOURCE,key,typ,title,_str(entry,"original_title",240),
            _str(entry,"genre",350),yr,_str(entry,"director",250),
            _str(entry,"actors",600),_str(entry,"description",3500),rating,
            _str(entry,"rating_source",80),_str(entry,"cover_url",1800),
            _str(entry,"source_page",1800),now(),_str(entry,"country",120),
            _str(entry,"certification",40),int(bool(entry.get("silent_import",False))))
    prepared=[]
    seen_quality_versions=set()
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
        version_type="dubbed" if dubbed=="yes" else "original"
        identity=(season,episode,quality,version_type)
        if identity in seen_quality_versions:
            continue
        seen_quality_versions.add(identity)
        subs=var.get("subtitles","unknown")
        if subs not in ("none","hardsub","softsub","external","unknown"):
            raise ValueError("Invalid subtitle")
        method=var.get("send_method","sendVideo")
        if method!="sendVideo":raise ValueError("Film2Media files must be queued as streamable sendVideo")
        url=valid_url(var.get("url"),hosts)
        prepared.append((season,episode,quality,_str(var,"version",90),
            dubbed,subs,_str(var,"language",50),url,method,now()))
    if entry.get("cover_url"):
        valid_url(entry["cover_url"],hosts)
    if entry.get("source_page"):
        valid_url(entry["source_page"],hosts|{"f2mgo.top","www.f2mgo.top"})
    c.execute("""INSERT OR IGNORE INTO film_titles
      (source,external_key,media_type,title,original_title,genre,year,director,
      actors,description,rating,rating_source,cover_url,source_page,created,country,certification,silent_import,rights_ok)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",titlevals)
    parent=c.execute("SELECT id FROM film_titles WHERE source=? AND external_key=?",
                     (SOURCE,key)).fetchone()[0]
    added=0
    for v in prepared:
        version_type="dubbed" if v[4]=="yes" else "original"
        existing=c.execute("""SELECT 1 FROM film_variants
          WHERE title_id=? AND season=? AND episode=? AND quality=?
            AND CASE WHEN dubbed='yes' THEN 'dubbed' ELSE 'original' END=?
          LIMIT 1""",(parent,v[0],v[1],v[2],version_type)).fetchone()
        if existing:
            continue
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
        if expected.isdigit() and int(expected)>MAX_FILE:raise ValueError("SKIPPED_SIZE_LIMIT: source exceeds Telegram Local API 1950 MB limit")
        mime=(response.headers.get("Content-Type","").split(";")[0].strip().lower())
        if mime in ("text/html","application/json","text/plain"):
            raise ValueError("Video URL returned webpage, not media")
        with path.open("xb") as dest:
            while True:
                piece=response.read(CHUNK)
                if not piece:break
                total+=len(piece)
                if total>MAX_FILE:raise ValueError("SKIPPED_SIZE_LIMIT: source exceeds Telegram Local API 1950 MB limit")
                if total%(24*CHUNK)==0:_remaining_disk(path.parent)
                dest.write(piece)
            dest.flush()
            os.fsync(dest.fileno())
    if total<64*1024:raise ValueError("Video file too small")
    if expected.isdigit() and int(expected)!=total:raise ValueError("Incomplete video download")
    return total

def upload(path,method,config,cfg,storage_chat,media_info=None):
    if not config["local_bot_api"]:raise RuntimeError("Local Bot API is not configured")
    if method!="sendVideo":raise ValueError("Film2Media uploads must use sendVideo")
    token=cfg.get("token")
    if not cfg.get("enabled") or not token:raise RuntimeError("Bot is not configured")
    size=path.stat().st_size
    if not 64*1024<=size<=MAX_FILE:raise ValueError("Invalid upload size")
    info=media_info or {}
    boundary="MasihaFilm_"+os.urandom(12).hex()
    def field(name,value):
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
    fields=field("chat_id",storage_chat)+field("disable_notification","true")+field("supports_streaming","true")
    if info.get("duration"):fields+=field("duration",max(1,int(info["duration"])))
    if info.get("width"):fields+=field("width",int(info["width"]))
    if info.get("height"):fields+=field("height",int(info["height"]))
    fields+=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"masiha-video.mp4\"\r\n"
            f"Content-Type: video/mp4\r\n\r\n").encode()
    finish=f"\r\n--{boundary}--\r\n".encode()
    conn=http.client.HTTPConnection(LOCAL_API,LOCAL_API_PORT,timeout=1200)
    try:
        conn.putrequest("POST","/bot"+token+"/sendVideo")
        conn.putheader("Content-Type","multipart/form-data; boundary="+boundary)
        conn.putheader("Content-Length",str(len(fields)+size+len(finish)))
        conn.endheaders();conn.send(fields)
        with path.open("rb") as f:
            while partdata:=f.read(CHUNK):conn.send(partdata)
        conn.send(finish)
        resp=conn.getresponse();data=resp.read(100_001)
        if resp.status!=200 or len(data)>100_000:raise RuntimeError("Telegram sendVideo HTTP "+str(resp.status))
        result=json.loads(data)
        if not result.get("ok"):raise RuntimeError("Telegram rejected streamable video")
        msg=result.get("result") or {};video=msg.get("video") or {}
        fid=video.get("file_id");uid=video.get("file_unique_id")
        if not isinstance(fid,str) or len(fid)<10 or not isinstance(uid,str) or len(uid)<5:
            raise RuntimeError("Telegram did not return a video file_id")
        if not video.get("duration") or not video.get("width") or not video.get("height"):
            raise RuntimeError("Telegram did not recognize uploaded MP4 as a video")
        return fid,uid,msg.get("message_id")
    finally:conn.close()

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
    import sales
    conn=(http.client.HTTPConnection("127.0.0.1",8081,timeout=45)
          if sales.telegram_local_enabled()
          else http.client.HTTPSConnection("api.telegram.org",context=ssl.create_default_context(),timeout=45))
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

def _episode_upload_state(c,title_id,season,episode):
    return c.execute("""SELECT COUNT(*) AS total,
      SUM(CASE WHEN j.state IN ('done','blocked') THEN 1 ELSE 0 END) AS terminal,
      SUM(CASE WHEN j.state='done' AND v.uploaded>0 AND v.stream_ready=1 AND v.file_id<>''
        THEN 1 ELSE 0 END) AS ready
      FROM film_variants v LEFT JOIN film_import_jobs j ON j.variant_id=v.id
      WHERE v.title_id=? AND v.season=? AND v.episode=?""",
      (title_id,season,episode)).fetchone()


def finalize_episode(c,title_id,season,episode):
    """Publish one movie/episode only after every staged quality is terminal."""
    import media_library
    state=_episode_upload_state(c,title_id,season,episode)
    total=int(state["total"] or 0)
    terminal=int(state["terminal"] or 0)
    ready=int(state["ready"] or 0)
    if not total or terminal!=total or not ready:
        return None

    title=c.execute("SELECT * FROM film_titles WHERE id=?",(title_id,)).fetchone()
    if title is None or not title["rights_ok"]:
        return None
    # If a poster URL exists, wait for its Telegram upload so the announcement
    # can be a proper poster card. retry_missing_covers() retries this safely.
    if title["cover_url"] and not title["cover_id"]:
        return None

    primary=c.execute("""SELECT v.* FROM film_variants v
      JOIN film_import_jobs j ON j.variant_id=v.id
      WHERE v.title_id=? AND v.season=? AND v.episode=?
        AND j.state='done' AND v.uploaded>0 AND v.stream_ready=1 AND v.file_id<>''
      ORDER BY CAST(REPLACE(v.quality,'p','') AS INTEGER),v.id LIMIT 1""",
      (title_id,season,episode)).fetchone()
    if not primary:
        return None

    unique=hashlib.sha256(f"film:{title_id}:{season}:{episode}".encode()).hexdigest()
    existing=c.execute("SELECT id,status FROM media_items WHERE kind='film' AND unique_id=?",
                       (unique,)).fetchone()
    if existing and existing["status"]=="removed":
        return None

    values=(primary["file_id"],title["title"],title["genre"],title["media_type"],
      season,episode,title["actors"],title["year"],primary["language"],
      primary["dubbed"],primary["subtitles"],title["director"],title["description"],
      title["cover_id"],title_id)
    was_published=bool(existing and existing["status"]=="published")
    if existing:
        mid=existing["id"]
        c.execute("""UPDATE media_items SET telegram_method='sendVideo',file_id=?,title=?,genre=?,
          media_type=?,season=?,episode=?,actors=?,year=?,language=?,dubbed=?,subtitles=?,
          director=?,description=?,cover_id=?,film_title_id=?,rights_ok=1,status='published'
          WHERE id=?""",(*values,mid))
    else:
        mid=c.execute("""INSERT INTO media_items
          (owner_id,kind,telegram_method,file_id,unique_id,source_url,title,genre,
           media_type,season,episode,actors,year,language,dubbed,subtitles,director,
           description,cover_id,status,rights_ok,created,film_title_id)
          VALUES(0,'film','sendVideo',?,?, '',?,?,?,?,?,?,?,?,?,?,?,?,?,'published',1,?,?)""",
          (values[0],unique,*values[1:14],now(),values[14])).lastrowid

    # Exactly one announcement per movie or episode, after its complete quality
    # set is ready. Existing published items never re-notify for later variants.
    if not was_published and not title["silent_import"]:
        media_library.broadcast_new_media(c,mid)
    return mid


def publish(c,variant_id):
    variant=c.execute("""SELECT v.*,t.rights_ok AS title_rights_ok
      FROM film_variants v JOIN film_titles t ON t.id=v.title_id WHERE v.id=?""",
      (variant_id,)).fetchone()
    if variant is None or not variant["file_id"] or not variant["stream_ready"]:
        raise ValueError("Streamable video is not uploaded")
    if not variant["title_rights_ok"]:
        raise ValueError("Rights not granted")
    c.execute("UPDATE film_variants SET uploaded=? WHERE id=?",(now(),variant_id))
    c.execute("UPDATE film_import_jobs SET state='done',last_error='',updated=? WHERE variant_id=?",
              (now(),variant_id))
    return finalize_episode(c,variant["title_id"],variant["season"],variant["episode"])


def retry_missing_covers(c,config,cfg,storage):
    """Retry poster uploads and finalize any now-complete movie/episode."""
    rows=c.execute("""SELECT DISTINCT t.id,t.cover_url FROM film_titles t
      JOIN film_variants v ON v.title_id=t.id
      JOIN film_import_jobs j ON j.variant_id=v.id
      WHERE t.cover_url<>'' AND t.cover_id=''
        AND j.state='done' AND v.uploaded>0 AND v.stream_ready=1 AND v.file_id<>''
      ORDER BY t.id LIMIT 10""").fetchall()
    for title in rows:
        try:
            cover_id=upload_cover(title["cover_url"],config["hosts"],cfg,storage)
            c.execute("UPDATE film_titles SET cover_id=? WHERE id=? AND cover_id=''",
                      (cover_id,title["id"]))
            c.commit()
            episodes=c.execute("""SELECT DISTINCT season,episode FROM film_variants
              WHERE title_id=? ORDER BY season,episode""",(title["id"],)).fetchall()
            for episode in episodes:
                c.execute("BEGIN IMMEDIATE")
                try:
                    finalize_episode(c,title["id"],episode["season"],episode["episode"])
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
        except (OSError,ValueError,RuntimeError,HTTPError,URLError,TimeoutError):
            print("FILM_COVER_DEFERRED",title["id"],flush=True)


def probe_media(path):
    import subprocess
    result=subprocess.run(["ffprobe","-v","error","-show_entries",
      "format=duration,format_name,size:stream=codec_type,codec_name,profile,width,height,pix_fmt,channels,sample_rate",
      "-of","json",str(path)],capture_output=True,text=True,timeout=90)
    if result.returncode:raise ValueError("ffprobe_rejected_media")
    try:data=json.loads(result.stdout)
    except ValueError:raise ValueError("ffprobe_invalid_metadata") from None
    streams=data.get("streams") or []
    video=next((x for x in streams if x.get("codec_type")=="video"),None)
    audio=next((x for x in streams if x.get("codec_type")=="audio"),{})
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("Downloaded asset is not a playable video")
    try:duration=float((data.get("format") or {}).get("duration") or 0)
    except (TypeError,ValueError):duration=0
    if duration<=0 or duration>60*60*24*7:raise ValueError("Invalid video duration")
    return {"duration":duration,"width":int(video["width"]),"height":int(video["height"]),
      "video_codec":video.get("codec_name", ""),"audio_codec":audio.get("codec_name", ""),
      "pix_fmt":video.get("pix_fmt", ""),"format_name":(data.get("format") or {}).get("format_name", "")}

def prepare_streamable(source,output):
    """Create a fast-start MP4 for Telegram streaming; remux HEVC/H.264 without video re-encode."""
    import subprocess
    original=probe_media(source)
    codec=original["video_codec"]
    can_remux=codec in ("h264","hevc")
    base=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y","-i",str(source),
      "-map","0:v:0","-map","0:a:0?","-map_metadata","0","-c:v","copy"]
    if codec=="hevc":base += ["-tag:v","hvc1"]
    base += ["-c:a","aac","-b:a","160k","-ac","2"]
    # Preserve an embedded subtitle as MP4 mov_text when possible.
    if can_remux:
      cmd=base+["-map","0:s:0?","-c:s","mov_text","-movflags","+faststart",str(output)]
      r=subprocess.run(cmd,capture_output=True,text=True,timeout=2400)
      if r.returncode:
        output.unlink(missing_ok=True)
        cmd=base+["-sn","-movflags","+faststart",str(output)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=2400)
      if r.returncode:can_remux=False;output.unlink(missing_ok=True)
    if not can_remux:
      output.unlink(missing_ok=True)
      cmd=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y","-i",str(source),
        "-map","0:v:0","-map","0:a:0?","-sn","-map_metadata","0",
        "-vf","format=yuv420p","-c:v","libx264","-preset","ultrafast","-crf","25",
        "-threads","1","-c:a","aac","-b:a","128k","-ac","2",
        "-movflags","+faststart",str(output)]
      r=subprocess.run(cmd,capture_output=True,text=True,timeout=7200)
      if r.returncode:raise RuntimeError("ffmpeg_mp4_conversion_failed")
    if not output.exists() or output.stat().st_size<64*1024:
      raise ValueError("Invalid MP4 output")
    if output.stat().st_size>MAX_FILE:
      # Do not re-encode oversized variants just to force an upload.
      # Skip this quality; other qualities for the same title remain eligible.
      output.unlink(missing_ok=True)
      raise ValueError("SKIPPED_SIZE_LIMIT: streamable MP4 exceeds Telegram Local API 1950 MB limit")
    result=probe_media(output)
    if "mp4" not in result["format_name"] or result["video_codec"] not in ("h264","hevc"):
      raise ValueError("MP4 is not compatible with Telegram streaming")
    return result

def process_one(c, config):
    import sales
    import roz_telegram_upload as audio
    cfg=sales.settings(c)
    if not config["local_bot_api"]:return "waiting_for_local_bot_api"
    storage=audio.verified_storage_chat(cfg)
    retry_missing_covers(c,config,cfg,storage)
    job=c.execute("""SELECT j.variant_id,v.source_url,v.send_method,j.attempts
       FROM film_import_jobs j JOIN film_variants v ON v.id=j.variant_id
       WHERE j.state='pending' OR (j.state='failed' AND j.attempts<3)
       ORDER BY CASE WHEN j.state='failed' THEN 0 ELSE 1 END,j.updated,j.variant_id LIMIT 1""").fetchone()
    if not job:return "queue_empty"
    vid=job["variant_id"]
    c.execute("UPDATE film_import_jobs SET state='processing',updated=? WHERE variant_id=?",(now(),vid));c.commit()
    TEMP.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
      with tempfile.TemporaryDirectory(prefix="job-",dir=TEMP) as d:
        source=Path(d)/"source.bin";output=Path(d)/"stream.mp4"
        download(job["source_url"],source,config["hosts"])
        info=prepare_streamable(source,output)
        fid,unique,message_id=upload(output,"sendVideo",config,cfg,storage,info)
        c.execute("""UPDATE film_variants SET send_method='sendVideo',file_id=?,file_unique_id=?,
          file_bytes=?,duration=?,width=?,height=?,video_codec=?,audio_codec=?,stream_ready=1,uploaded=?
          WHERE id=?""",(fid,unique,output.stat().st_size,info["duration"],info["width"],
          info["height"],info["video_codec"],info["audio_codec"],now(),vid))
        c.execute("UPDATE film_import_jobs SET state='uploaded',updated=? WHERE variant_id=?",(now(),vid));c.commit()
        title=c.execute("SELECT t.id,t.cover_url,t.cover_id FROM film_titles t JOIN film_variants v ON v.title_id=t.id WHERE v.id=?",(vid,)).fetchone()
        if title and title["cover_url"] and not title["cover_id"]:
          try:
            cover_id=upload_cover(title["cover_url"],config["hosts"],cfg,storage)
            c.execute("UPDATE film_titles SET cover_id=? WHERE id=? AND cover_id=''",(cover_id,title["id"]));c.commit()
          except (OSError,ValueError,RuntimeError,HTTPError,URLError,TimeoutError):
            print("FILM_COVER_DEFERRED",vid,flush=True)
      c.execute("BEGIN IMMEDIATE");mid=publish(c,vid);c.commit()
      return "published:"+str(mid) if mid else "uploaded_waiting_for_episode_qualities"
    except Exception as ex:
      c.rollback();state="blocked" if isinstance(ex,ValueError) else "failed"
      detail=str(ex) if isinstance(ex,(ValueError,RuntimeError)) else type(ex).__name__
      detail=re.sub(r"https?://\S+","[source-url]",detail)
      detail=re.sub(r"/var/tmp/masiha-films/\S+","[temporary-file]",detail)
      if isinstance(ex,HTTPError):
        stored_error="HTTPError:"+str(ex.code)+":source temporarily unavailable"
      else:
        stored_error=detail if detail.startswith("SKIPPED_SIZE_LIMIT:") else type(ex).__name__+':'+detail
      previous=c.execute("SELECT attempts FROM film_import_jobs WHERE variant_id=?",(vid,)).fetchone()
      attempts=(int(previous["attempts"]) if previous else 0)+1
      if state=="failed" and attempts>=3:
        state="blocked"
        stored_error="FAILED_AFTER_RETRIES:"+stored_error
      context=c.execute("SELECT title_id,season,episode FROM film_variants WHERE id=?",(vid,)).fetchone()
      c.execute("""UPDATE film_import_jobs SET state=?,attempts=?,last_error=?,updated=?
        WHERE variant_id=?""",(state,attempts,stored_error[:180],now(),vid));c.commit()
      if state=="blocked" and context:
        c.execute("BEGIN IMMEDIATE")
        try:
          finalize_episode(c,context["title_id"],context["season"],context["episode"])
          c.commit()
        except Exception:
          c.rollback()
          raise
      return state+":"+type(ex).__name__

def recover_uploaded(c):
    # A killed worker leaves the old processing lease, but the scratch file
    # is already gone; release only stale leases and retry a fresh download.
    c.execute("""UPDATE film_import_jobs SET state='failed',updated=?,last_error='worker_interrupted',
       attempts=attempts+1 WHERE state='processing' AND updated<?""",
       (now(),now()-3600))
    exhausted=c.execute("""SELECT DISTINCT v.title_id,v.season,v.episode
      FROM film_import_jobs j JOIN film_variants v ON v.id=j.variant_id
      WHERE j.state='failed' AND j.attempts>=3""").fetchall()
    c.execute("""UPDATE film_import_jobs SET state='blocked',
      last_error=CASE WHEN last_error='' THEN 'FAILED_AFTER_RETRIES' ELSE 'FAILED_AFTER_RETRIES:'||last_error END,
      updated=? WHERE state='failed' AND attempts>=3""",(now(),))
    c.commit()
    for episode in exhausted:
        c.execute("BEGIN IMMEDIATE")
        try:
            finalize_episode(c,episode["title_id"],episode["season"],episode["episode"])
            c.commit()
        except Exception:
            c.rollback()
            raise
    rows=c.execute("""SELECT j.variant_id FROM film_import_jobs j JOIN film_variants v
      ON v.id=j.variant_id WHERE j.state IN ('uploaded','failed')
      AND LENGTH(v.file_id)>=10 AND v.stream_ready=1""").fetchall()
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
    counts={r[0]:r[1] for r in c.execute("SELECT state,COUNT(*) FROM film_import_jobs GROUP BY state")}
    counts["size_skipped"]=c.execute(
      "SELECT COUNT(*) FROM film_import_jobs WHERE state='blocked' AND last_error LIKE 'SKIPPED_SIZE_LIMIT:%'"
    ).fetchone()[0]
    if "blocked" in counts:
        counts["blocked"]=max(0,counts["blocked"]-counts["size_skipped"])
    return counts

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