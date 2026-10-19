#!/usr/bin/env python3
"""Locally computed music fingerprints; no paid recognition API or durable MP3s.

Public voice recognition is matched ONLY against indexed, published media.
Background worker is isolated from the Telegram polling loop.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path("/var/lib/masiha-audio-fp")
DATABASE = ROOT / "fingerprints.pklz"
LOCK = ROOT / "fingerprints.lock"
ENGINE = Path("/opt/masiha-audio-fp/audfprint/audfprint.py")
MAX_DOWNLOAD = 19_900_000
MAX_QUERY = 9_000_000
MODEL_SETTINGS = ("--hashbits","17","--bucketsize","40","--density","7","--maxtimebits","15")
NAME = re.compile(r"(?<![a-zA-Z0-9])(?:m(?P<id>[0-9]{1,12})|a(?P<alias>[a-f0-9]{24}))\.(?:mp3|ogg|wav)\b")
MATCH = re.compile(r"Matched\s+.+?\s+as\s+(\S+)\s+at\s+.+?\s+with\s+(\d+)\s+of\s+(\d+)\s+(?:common\s+)?hashes",re.I)
MIN_MATCHES = 9

def setup(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS audio_fp_index (
      media_id INTEGER PRIMARY KEY,
      indexed INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'new',
      attempts INTEGER NOT NULL DEFAULT 0,
      error TEXT NOT NULL DEFAULT '',
      updated INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS audio_fp_queries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL, update_id INTEGER NOT NULL UNIQUE,
      file_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
      message TEXT NOT NULL DEFAULT '', media_id INTEGER NOT NULL DEFAULT 0,
      created INTEGER NOT NULL, updated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS audio_fp_queries_state ON audio_fp_queries(state,id);
    """)

def available():
    return ENGINE.is_file() and shutil.which("ffmpeg") is not None

def enqueue(c,uid,update_id,file):
    if not isinstance(file,dict):raise ValueError("پیام صوتی معتبر نیست")
    fid=file.get("file_id")
    size=file.get("file_size",0)
    duration=file.get("duration",0)
    if not isinstance(fid,str) or not 10<=len(fid)<=256 or any(ord(x)<33 for x in fid):
        raise ValueError("شناسه فایل صوتی نامعتبر است")
    if type(size) is not int or size<1 or size>MAX_QUERY:
        raise ValueError("اندازه فایل صوتی بیش از حد مجاز است (حداکثر ۹ مگابایت)")
    if type(duration) is not int or not 5<=duration<=45:
        raise ValueError("۵ تا ۴۵ ثانیه از صدای واقعی آهنگ را ارسال کنید")
    if not available():
        raise ValueError("سرویس شناسایی صوتی هنوز در دسترس نیست")
    if not DATABASE.exists():
        raise ValueError("نمایه صوتی آهنگ‌ها هنوز آماده نشده است؛ ابتدا چند آهنگ باید اثرانگشت‌گذاری شوند")
    ts=int(time.time())
    c.execute("""INSERT OR IGNORE INTO audio_fp_queries
     (user_id,update_id,file_id,created) VALUES(?,?,?,?)""",(uid,update_id,fid,ts))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,url):return None

OPENER=build_opener(NoRedirect())

def download_telegram(c,file_id,dest,limit):
    import sales
    settings=sales.settings(c)
    tok=settings.get("token")
    if not tok or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,}",tok):
        raise ValueError("توکن ربات تنظیم نشده است")
    info=sales.tg(settings,"getFile",{"file_id":file_id})
    path=info.get("file_path")
    if not isinstance(path,str) or not re.fullmatch(r"[a-zA-Z0-9_./-]{1,240}",path) or ".." in path or path.startswith("/"):
        raise ValueError("مسیر فایل تلگرام نامعتبر است")
    filesize=info.get("file_size",0)
    if type(filesize) is int and filesize>limit:raise ValueError("فایل بزرگ‌تر از سقف دانلود صوت است")
    url="https://api.telegram.org/file/bot"+tok+"/"+quote(path,safe="/")
    try:
        with OPENER.open(Request(url,headers={"User-Agent":"MasihaAudioFingerprint/1.0"}),timeout=22) as resp:
            length=0
            with open(dest,"xb") as out:
                while True:
                    buf=resp.read(65536)
                    if not buf:break
                    length+=len(buf)
                    if length>limit:raise ValueError("فایل صوتی بیش از حد بزرگ است")
                    out.write(buf)
        if length<1200:raise ValueError("نمونه صوتی معتبر نیست")
        return length
    except Exception:
        Path(dest).unlink(missing_ok=True)
        raise

@contextlib.contextmanager
def lock_db():
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(LOCK,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def _command(args,timeout=60):
    if not available():raise RuntimeError("Fingerprint engine dependencies unavailable")
    result=subprocess.run(["/usr/bin/python3",str(ENGINE)]+args,
                          check=False,capture_output=True,text=True,timeout=timeout,
                          env={**os.environ,"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1"})
    if result.returncode:
        raise RuntimeError("Audio fingerprint engine failed ("+str(result.returncode)+")")
    return (result.stdout+"\n"+result.stderr)[-12000:]

def index_audio(audio_path,marker):
    """Index once, never retain original music bytes; locking prevents races."""
    if not re.fullmatch(r"m[0-9]{1,12}|a[a-f0-9]{24}",marker):
        raise ValueError("Bad fingerprint reference marker")
    with lock_db():
        with tempfile.TemporaryDirectory(prefix="masiha-fp-",dir="/tmp") as d:
            source=Path(d)/(marker+".mp3")
            try:os.link(audio_path,source)
            except OSError:shutil.copyfile(audio_path,source)
            operation="add" if DATABASE.exists() else "new"
            args=[operation,"--dbase",str(DATABASE)]
            if operation=="new":args+=list(MODEL_SETTINGS)
            args+=["--ncores","1","-v","0",str(source)]
            if DATABASE.exists():shutil.copy2(DATABASE,ROOT/"last-good.pklz")
            try:
                output=_command(args,timeout=85)
                if not DATABASE.exists() or DATABASE.stat().st_size<1000:
                    raise RuntimeError("Fingerprint database was not created")
            except Exception:
                backup=ROOT/"last-good.pklz"
                if backup.exists():shutil.copy2(backup,DATABASE)
                else:DATABASE.unlink(missing_ok=True)
                raise
            return output

def index_imported_source(article_url,audio_path):
    """Called on the existing temp MP3 before import deletes it."""
    if not available():return False
    alias=hashlib.sha256(article_url.encode()).hexdigest()[:24]
    index_audio(audio_path,"a"+alias)
    return True

def recognize_audio(audio_path):
    with lock_db():
        if not DATABASE.exists():return None
        output=_command(["match","--dbase",str(DATABASE),
                         "--min-count",str(MIN_MATCHES),"--max-matches","1",
                         "--ncores","1","-v","1",str(audio_path)],timeout=55)
    match=MATCH.search(output)
    if not match or int(match.group(2))<MIN_MATCHES:return None
    stem=Path(match.group(1)).name
    return stem

def resolve_match(c,marker):
    match=NAME.search(marker)
    if not match:return None
    if match.group("id"):
        row=c.execute("""SELECT id,title,artist FROM media_items
          WHERE id=? AND kind='music' AND status='published'""",
         (int(match.group("id")),)).fetchone()
        return row
    alias=match.group("alias")
    urls=c.execute("""SELECT s.article_url,m.id,m.title,m.artist FROM roz_catalog_sources s
         JOIN media_items m ON m.id=s.media_id
         WHERE m.kind='music' AND m.status='published'""").fetchall()
    for row in urls:
        if hashlib.sha256(row["article_url"].encode()).hexdigest()[:24]==alias:return row
    return None

def notify(c,uid,body,mid=None,dedupe=None):
    import sales
    buttons=[[("▶️ پخش موزیک پیدا‌شده","med:play:"+str(mid))]] if mid else []
    buttons.append([("🎙 شناسایی موزیک دیگر","med:voicefind"),("🎵 موزیک","med:music")])
    sales.enqueue(c,uid,body,buttons,key=dedupe)

def process_query(c,row):
    uid=row["user_id"];ident=row["id"]
    with tempfile.TemporaryDirectory(prefix="masiha-fp-query-",dir="/tmp") as d:
        dest=Path(d)/"query.ogg"
        download_telegram(c,row["file_id"],dest,MAX_QUERY)
        matched=recognize_audio(dest)
    media=resolve_match(c,matched) if matched else None
    if media:
        body=("🎵 موزیک شناسایی شد: "+media["title"]+
              (" | "+media["artist"] if media["artist"] else ""))
        notify(c,uid,body,media["id"],"audio-fp:result:"+str(ident))
        c.execute("""UPDATE audio_fp_queries SET state='done',media_id=?,message='matched',updated=?
          WHERE id=?""",(media["id"],int(time.time()),ident))
    else:
        indexed=c.execute("SELECT COUNT(*) FROM audio_fp_index WHERE indexed=1").fetchone()[0]
        total=c.execute("SELECT COUNT(*) FROM media_items WHERE kind='music' AND status='published'").fetchone()[0]
        note= "این موزیک در بخش نمایه‌گذاری‌شده آرشیو پیدا نشد."
        if indexed<total:note+="\nℹ️ شناسایی صوتی آرشیو در حال تکمیل است."
        notify(c,uid,"🔎 "+note,dedupe="audio-fp:result:"+str(ident))
        c.execute("UPDATE audio_fp_queries SET state='done',message='not_found',updated=? WHERE id=?",
                  (int(time.time()),ident))

def process_reference(c,row):
    mid=row["id"]
    with tempfile.TemporaryDirectory(prefix="masiha-fp-ref-",dir="/tmp") as d:
        path=Path(d)/"original.mp3"
        download_telegram(c,row["file_id"],path,MAX_DOWNLOAD)
        index_audio(path,"m"+str(mid))
    c.execute("""INSERT INTO audio_fp_index(media_id,indexed,state,updated) VALUES(?,1,'done',?)
     ON CONFLICT(media_id) DO UPDATE SET indexed=1,state='done',error='',updated=excluded.updated""",
     (mid,int(time.time())))

def run_once():
    if not available():return "dependencies_missing"
    import sales
    with sales.conn() as c:
        setup(c)
        item=c.execute("SELECT * FROM audio_fp_queries WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
        if item:
            try:process_query(c,item)
            except Exception as ex:
                c.execute("UPDATE audio_fp_queries SET state='error',message=?,updated=? WHERE id=?",
                    (type(ex).__name__,int(time.time()),item["id"]))
                notify(c,item["user_id"],"⚠️ شناسایی صوتی این نمونه انجام نشد؛ با یک نمونه واضح‌تر دوباره امتحان کنید.",
                       dedupe="audio-fp:error:"+str(item["id"]))
            return "query_processed"
        media=c.execute("""SELECT m.id,m.file_id FROM media_items m
             LEFT JOIN audio_fp_index i ON i.media_id=m.id
             WHERE m.kind='music' AND m.status='published'
             AND m.telegram_method IN ('sendAudio','sendVoice','sendDocument')
             AND m.file_id!=''
             AND (i.media_id IS NULL OR (i.state='error' AND i.attempts<2))
             ORDER BY m.id LIMIT 1""").fetchone()
        if not media:return "idle"
        try:
            process_reference(c,media)
            return "indexed:"+str(media["id"])
        except Exception as ex:
            old=c.execute("SELECT attempts FROM audio_fp_index WHERE media_id=?",(media["id"],)).fetchone()
            attempts=(old["attempts"] if old else 0)+1
            c.execute("""INSERT INTO audio_fp_index(media_id,state,error,attempts,updated)
                 VALUES(?,'error',?,?,?) ON CONFLICT(media_id) DO UPDATE SET
                 state='error',error=excluded.error,attempts=excluded.attempts,updated=excluded.updated""",
                 (media["id"],type(ex).__name__,attempts,int(time.time())))
            return "index_failed:"+str(media["id"])+":"+type(ex).__name__

if __name__=="__main__":
    if len(sys.argv)==2 and sys.argv[1]=="worker":
        print(run_once(),flush=True)
    elif len(sys.argv)==3 and sys.argv[1]=="match":
        print(recognize_audio(Path(sys.argv[2])))
    else:
        raise SystemExit("usage: audio_fingerprint.py worker|match <file>")
