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


def status(job,state,detail="",metadata=None,action=""):
    JOBS.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=JOBS/(job+".json")
    temp=JOBS/(job+".tmp")
    data={"job":job, "state":state, "detail":str(detail)[-220:],
          "at":int(time.time())}
    if metadata:
        data["metadata"]=metadata
    if action:
        data["action"]=action
    temp.write_text(json.dumps(data,ensure_ascii=False))
    os.chmod(temp,0o600)
    os.replace(temp,path)


def friendly_failure(logfile):
    # Do not reflect arbitrary error strings: logs can contain paths, bot
    # tokens, private service settings or user content. Use fixed categories.
    try:
        data=logfile.read_bytes()[-16384:].decode("utf-8","replace").lower()
    except OSError:
        data=""
    if "no space left on device" in data or "not enough free space" in data:
        return "فضای دیسک کافی نیست؛ فضای آزاد سرور را بررسی کنید."
    if "bad session key" in data or "decryption failed" in data or "no secret key" in data:
        return "کلید رمزگشایی صحیح نیست یا فایل بکاپ آسیب دیده است."
    if "sha256 mismatch" in data or "checksum failed" in data or "archive is incomplete" in data:
        return "صحت فایل بکاپ تأیید نشد؛ دوباره انتقال دهید."
    if "destination tls certificate" in data:
        return "ابتدا گواهی HTTPS معتبر برای دامنه سرور مقصد فعال کنید."
    if "destination component is not installed" in data:
        return "ابتدا VPN Manager را روی سرور مقصد نصب کنید."
    if "foreign key mismatch" in data or "cross-version" in data:
        return "ساختار دیتابیس مبدأ با نسخه مقصد سازگار نیست؛ لاگ فنی را بررسی کنید."
    if "unsupported backup format" in data or "invalid manifest" in data:
        return "فرمت بکاپ با نسخه موتور بازیابی مقصد سازگار نیست؛ سرور مقصد را آپدیت کنید."
    if "permission denied" in data:
        return "دسترسی خواندن یا نوشتن فایل‌های بکاپ در مقصد کافی نیست."
    if "blockingioerror" in data or "resource temporarily unavailable" in data:
        return "موتور بکاپ مقصد مشغول یک عملیات هم‌زمان است؛ پس از پایان دوباره بررسی کنید."
    if "no module named" in data or "module not found" in data or "file not found" in data:
        return "یکی از فایل‌ها یا وابستگی‌های موتور بازیابی در مقصد موجود نیست."
    if "not a gzip file" in data or "unexpected end of data" in data or "tarfile" in data:
        return "ساختار آرشیو فشرده در مقصد قابل خواندن نیست."
    return "اعتبارسنجی محتوای بکاپ در مقصد ناموفق بود؛ گزارش فنی روی مقصد در web-jobs نگهداری شده است."


def main():
    os.umask(0o077)
    if len(sys.argv)!=3:
        raise ValueError("expected action and job")
    action,job=sys.argv[1:]
    if not HEX.fullmatch(job) or action not in {"create","create-full","inspect","restore"}:
        raise ValueError("invalid job")
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    with open(ROOT/"web-work.lock","a+b") as lock:
        # Jobs must not compete with each other for restore/backup snapshots.
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        status(job,"running",action=action)
        args=["/usr/bin/python3",str(CLI)]
        if action in ("create","create-full"):
            args+=["backup","--output-dir",str(ROOT),
                   "--key-file","/root/.masiha-backup.key"]
            if action=="create":
                args.append("--skip-media")
        else:
            archive=IMPORTS/(job+".gpg")
            key=IMPORTS/(job+".key")
            if not archive.is_file() or not key.is_file():
                raise ValueError("Uploaded archive or key missing")
            args+=["inspect" if action=="inspect" else "restore",str(archive),
                   "--key-file",str(key)]
            if action=="restore":
                # Portable mode preserves destination domain/certificates and
                # never starts Telegram polling or source-managed remote nodes.
                args+=["--portable","--confirm-restore"]
        output=ROOT/"web-jobs"/(job+".log")
        with open(output,"wb") as log:
            proc=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,
                                timeout=1800,check=False)
        if proc.returncode:
            # Only a short sanitized error; never report tokens or key contents.
            status(job,"failed",friendly_failure(output),action=action)
        else:
            metadata=None
            if action=="inspect":
                # Extract only the explicit, validated summary line; never
                # reveal the underlying backup log or archive secrets.
                import re
                text=output.read_text(errors="replace")
                matches=re.findall(r'^BACKUP_SUMMARY_JSON (.+)$',text,re.M)
                if len(matches)!=1:
                    raise ValueError("Backup summary was not produced")
                metadata=json.loads(matches[0])
                status(job,"done","رمزگشایی و بررسی فایل موفق؛ آماده بازیابی روی دامنه مقصد",
                       metadata=metadata,action=action)
            else:
                status(job,"done","بکاپ آماده شد" if action.startswith("create") else
                       "اطلاعات بازیابی شد؛ ربات تا انتقال نهایی غیرفعال است",action=action)
    return 0


if __name__=="__main__":
    try:
        main()
    except Exception:
        if len(sys.argv)==3 and HEX.fullmatch(sys.argv[2]):
            status(sys.argv[2],"failed","خطا در اجرای عملیات یا عملیات هم‌زمان")
        raise
