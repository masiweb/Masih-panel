#!/usr/bin/env python3
"""Web panel background backup/restore tasks. Invoked ONLY via systemd-run.
Never expose secret files directly or accept arbitrary filesystem paths.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

ROOT=Path("/root/masiha-backups")
JOBS=ROOT/"web-jobs"
IMPORTS=ROOT/"web-imports"
CLI=Path("/opt/masiha-vpn-extension/full_backup.py")
HEX=re.compile(r"^[a-f0-9]{32}$")


def status(job,state,detail=""):
    JOBS.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=JOBS/(job+".json")
    temp=JOBS/(job+".tmp")
    temp.write_text(json.dumps({
        "job":job, "state":state, "detail":str(detail)[-220:],
        "at":int(time.time())
    },ensure_ascii=False))
    os.chmod(temp,0o600)
    os.replace(temp,path)


def main():
    os.umask(0o077)
    if len(sys.argv)!=3:
        raise ValueError("expected action and job")
    action,job=sys.argv[1:]
    if not HEX.fullmatch(job) or action not in {"create","inspect","restore"}:
        raise ValueError("invalid job")
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    with open(ROOT/"web-work.lock","a+b") as lock:
        # Jobs must not compete with each other for restore/backup snapshots.
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        status(job,"running")
        args=["/usr/bin/python3",str(CLI)]
        if action=="create":
            args+=["backup","--skip-media","--output-dir",str(ROOT),
                   "--key-file","/root/.masiha-backup.key"]
        else:
            archive=IMPORTS/(job+".gpg")
            key=IMPORTS/(job+".key")
            if not archive.is_file() or not key.is_file():
                raise ValueError("Uploaded archive or key missing")
            args+=["verify" if action=="inspect" else "restore",str(archive),
                   "--key-file",str(key)]
            if action=="restore":
                # Default restore does not start the Telegram worker: source
                # must be stopped to prevent duplicated consumption of updates.
                args+=["--confirm-restore"]
        output=ROOT/"web-jobs"/(job+".log")
        with open(output,"wb") as log:
            proc=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,
                                timeout=1800,check=False)
        if proc.returncode:
            # Only a short sanitized error; never report tokens or key contents.
            status(job,"failed","عملیات ناموفق بود؛ لاگ محلی را بررسی کنید.")
        else:
            status(job,"done")
    return 0


if __name__=="__main__":
    try:
        main()
    except Exception:
        if len(sys.argv)==3 and HEX.fullmatch(sys.argv[2]):
            status(sys.argv[2],"failed","خطا در اجرای عملیات یا عملیات هم‌زمان")
        raise
