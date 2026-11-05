#!/usr/bin/env python3
"""Authenticated panel-only film import and Film2Media catalogue management."""
import json
import os
import re
import socket
import ipaddress
import subprocess
import sys
import uuid
from pathlib import Path

import film_importer as fi

ROOT=Path('/opt/masiha-vpn-extension')

def status():
    config=fi.settings()
    with fi.connect() as c:
        groups=[dict(r) for r in c.execute("""
            SELECT t.id,t.external_key,t.title,t.original_title,t.media_type,
                   t.genre,t.year,t.director,t.actors,t.rating,t.country,t.certification,
                   t.description,t.cover_url,t.created,t.rights_ok,
                   COUNT(v.id) AS variants
            FROM film_titles t LEFT JOIN film_variants v ON v.title_id=t.id
            GROUP BY t.id ORDER BY t.id DESC LIMIT 60""")]
        for item in groups:
            item["variants"]=[dict(r) for r in c.execute("""
                SELECT v.id,v.season,v.episode,v.quality,v.version,
                       v.dubbed,v.subtitles,v.language,v.send_method,
                       v.file_bytes,v.duration,v.width,v.height,v.video_codec,
                       v.audio_codec,v.stream_ready,v.uploaded,
                       j.state,j.attempts,j.last_error
                FROM film_variants v LEFT JOIN film_import_jobs j ON j.variant_id=v.id
                WHERE v.title_id=? ORDER BY v.season,v.episode,v.quality""",(item["id"],))]
        counts=fi.stats(c)
        scanrow=c.execute("SELECT * FROM film_scan_runs ORDER BY updated DESC LIMIT 1").fetchone()
        scan=dict(scanrow) if scanrow else None
        items=[]
        if scan:
            items=[dict(r) for r in c.execute("""
              SELECT external_key,title,media_type,variant_count,state,error
              FROM film_scan_items WHERE scan_id=? ORDER BY id DESC LIMIT 100""",(scan["id"],))]
    return {"local_api":config["local_bot_api"],"max_bytes":fi.MAX_FILE,
      "download_hosts":sorted(config["hosts"]),"counts":counts,"titles":groups,
      "free_bytes":__import__("shutil").disk_usage(fi.TEMP).free,
      "scan":scan,"scan_items":items}

def add_host(req):
    host=req.get("hostname","")
    if not isinstance(host,str):raise ValueError("Invalid hostname")
    host=host.strip().lower()
    wildcard=host.startswith("*.")
    base=host[2:] if wildcard else host
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",base):
        raise ValueError("Hostname format invalid")
    config=fi.settings()
    if host in config["hosts"]:return status()
    if len(config["hosts"])>=30:raise ValueError("Maximum 30 authorized hosts")
    if wildcard:
        raise ValueError("Wildcard CDN domains are added automatically after catalogue scan")
    fi.valid_url("https://"+host+"/source.mp4",config["hosts"]|{host})
    path=fi.CONFIG;data=json.loads(path.read_text())
    data["download_hosts"]=sorted(config["hosts"]|{host})
    temp=path.with_name(path.name+".next");temp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    temp.chmod(0o600);os.replace(temp,path)
    return status()

def add_title(req):
    entry=req.get("entry")
    if not isinstance(entry,dict):raise ValueError("Invalid film entry")
    if entry.get("rights_ok") is not True:raise ValueError("Only content with confirmed publishing rights is allowed")
    if entry.get("source")!="film2media":raise ValueError("Unrecognized approved media source")
    if not fi.settings()["local_bot_api"]:raise ValueError("Telegram Local API must be enabled")
    with fi.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:added=fi.stage(c,entry,fi.settings()["hosts"]);c.commit()
        except Exception:c.rollback();raise
    result=status();result["added_variants"]=added
    return result

def spawn_catalog(action,run_id):
    fi.TEMP.mkdir(mode=0o700,parents=True,exist_ok=True)
    logpath=fi.TEMP/("catalog-"+run_id+".log")
    log=logpath.open("ab")
    try:
        subprocess.Popen([sys.executable,str(ROOT/"film_catalog.py"),action,run_id],
          cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
          start_new_session=True,close_fds=True)
    finally:log.close()

def scan_start(req):
    scope=req.get("scope","all")
    if scope not in ("all","movie","series"):raise ValueError("Invalid catalogue scope")
    limit=req.get("limit",100)
    if type(limit) is not int or not (limit==0 or 1<=limit<=10000):
        raise ValueError("Scan limit must be 1..10000 or 0 for the full archive")
    with fi.connect() as c:
        active=c.execute("SELECT id FROM film_scan_runs WHERE status IN ('queued','running') ORDER BY started DESC LIMIT 1").fetchone()
        if active:raise ValueError("A catalogue scan is already running")
        run_id=uuid.uuid4().hex[:16]
        c.execute("""INSERT INTO film_scan_runs(id,scope,item_limit,status,updated)
          VALUES(?,?,?,'queued',?)""",(run_id,scope,limit,fi.now()));c.commit()
    spawn_catalog("scan",run_id)
    return status()

def add_all(req):
    if req.get("rights_ok") is not True:
        raise ValueError("Explicit confirmation of publishing rights for all scanned results is required")
    run_id=req.get("scan_id")
    if not isinstance(run_id,str) or not re.fullmatch(r"[a-f0-9]{16}",run_id):
        raise ValueError("Invalid scan ID")
    with fi.connect() as c:
        row=c.execute("SELECT * FROM film_scan_runs WHERE id=?",(run_id,)).fetchone()
        if not row or row["status"]!="done":raise ValueError("Wait for the catalogue scan to finish")
        if row["add_status"]=="running":raise ValueError("Bulk add is already running")
        remaining=c.execute("SELECT COUNT(*) FROM film_scan_items WHERE scan_id=? AND state='discovered'",(run_id,)).fetchone()[0]
        if not remaining:raise ValueError("There are no unqueued discovered titles")
        c.execute("UPDATE film_scan_runs SET add_status='queued',updated=? WHERE id=?",(fi.now(),run_id));c.commit()
    spawn_catalog("add-all",run_id)
    return status()

def retry(req):
    ident=req.get("variant_id")
    if type(ident) is not int or ident<=0:raise ValueError("Invalid variant id")
    with fi.connect() as c:
        row=c.execute("""SELECT j.state,j.last_error,v.file_id FROM film_import_jobs j
                         JOIN film_variants v ON v.id=j.variant_id WHERE j.variant_id=?""",(ident,)).fetchone()
        if not row or row["state"] not in ("failed","blocked"):raise ValueError("Only failed jobs may be retried")
        if (row["last_error"] or "").startswith("SKIPPED_SIZE_LIMIT:"):
            raise ValueError("این نسخه به دلیل عبور از سقف حجم تلگرام رد شده و دوباره پردازش نمی‌شود.")
        c.execute("""UPDATE film_import_jobs SET state='pending',attempts=0,
                     last_error='',updated=? WHERE variant_id=?""",(fi.now(),ident));c.commit()
    return status()

def handle(req):
    if not isinstance(req,dict):raise ValueError("Request must be object")
    action=req.get("action")
    if action=="status":return status()
    if action=="add":return add_title(req)
    if action=="add_host":return add_host(req)
    if action=="retry":return retry(req)
    if action=="scan_start":return scan_start(req)
    if action=="add_all":return add_all(req)
    raise ValueError("Invalid film management action")

if __name__=="__main__":
    try:
        req=json.loads(sys.stdin.read(65000))
        print(json.dumps({"success":True,"obj":handle(req)},ensure_ascii=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({"success":False,"msg":str(exc)[:240]},ensure_ascii=False))
