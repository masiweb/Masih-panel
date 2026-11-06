#!/usr/bin/env python3
"""Direct, authenticated server-to-server encrypted Masiha backups over SSH.

SSH host keys are pinned by an administrator-confirmed fingerprint.
Passwords live only in a 0600 tmpfs handoff file, consumed then deleted.
The remote backup is staged in web-imports and INSPECTED, never restored
or activated automatically. No backup bytes pass through the browser.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

RUN = Path("/run/masiha-backup-transfer")
JOBS = Path("/root/masiha-backups/web-jobs")
BACKUPS = Path("/root/masiha-backups")
IMPORTS = "/root/masiha-backups/web-imports"
CLI = Path("/opt/masiha-vpn-extension/full_backup.py")
HEX = re.compile(r"^[a-f0-9]{32}$")
USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEY_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{30,64}$")
PROGRESS_BYTES = re.compile(r"^\s*([0-9,]+)\s+([0-9]{1,3})%")
HOST_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")
MAX_RUN = 8 * 3600


def validate_target(host: str, username: str, port: int):
    if not isinstance(host, str) or len(host) > 48:
        raise ValueError("آدرس مقصد باید IP معتبر باشد")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("IP مقصد معتبر نیست") from exc
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
        raise ValueError("اتصال به این IP مجاز نیست")
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise ValueError("نام کاربری SSH معتبر نیست")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("پورت SSH معتبر نیست")
    return str(ip), username, port


def probe_host(host: str, username: str = "root", port: int = 22):
    host, username, port = validate_target(host, username, port)
    result = subprocess.run(
        ["ssh-keyscan", "-T", "7", "-p", str(port), "-t", "ed25519,ecdsa,rsa", host],
        capture_output=True, timeout=20, text=True, check=False)
    lines = [line for line in result.stdout.splitlines()
             if line and not line.startswith("#")]
    clean = []
    for line in lines:
        parts = line.split()
        if (len(parts) == 3 and parts[1] in HOST_KEY_TYPES and
                parts[0] in (host, f"[{host}]:{port}", f"[{host}]")):
            clean.append(line)
    if not clean:
        raise RuntimeError("کلید SSH سرور مقصد دریافت نشد؛ پورت و دسترسی را بررسی کنید")
    priority = {n:i for i,n in enumerate(HOST_KEY_TYPES)}
    clean.sort(key=lambda x: priority[x.split()[1]])
    chosen = clean[0]
    with tempfile.TemporaryDirectory(prefix="masiha-ssh-fingerprint-") as td:
        path = Path(td)/"hostkey"
        path.write_text(chosen+"\n")
        path.chmod(0o600)
        raw = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(path)],
                             capture_output=True,timeout=8, text=True,check=True).stdout
    fields = raw.split()
    if len(fields) < 3 or not KEY_FINGERPRINT.fullmatch(fields[1]):
        raise RuntimeError("اثر انگشت کلید SSH نامعتبر است")
    return {
        "host": host, "username": username, "port": port,
        "fingerprint": fields[1],
        "key_type": chosen.split()[1],
        "known_host": chosen,
    }


def create_transfer_handoff(job, settings):
    if not isinstance(job, str) or not HEX.fullmatch(job):
        raise ValueError("شناسه عملیات نامعتبر است")
    if not isinstance(settings, dict):
        raise ValueError("اطلاعات مقصد لازم است")
    host, username, port = validate_target(
        settings.get("host"), settings.get("username", "root"), settings.get("port", 22))
    password = settings.get("password")
    if not isinstance(password, str) or not (1 <= len(password) <= 1024) or "\x00" in password:
        raise ValueError("رمز عبور SSH معتبر نیست")
    fingerprint = settings.get("fingerprint")
    if not isinstance(fingerprint, str) or not KEY_FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("ابتدا اثرانگشت SSH مقصد را بررسی و تأیید کنید")
    if not isinstance(settings.get("include_media", True), bool):
        raise ValueError("گزینه بکاپ کامل نامعتبر است")
    retry_job=settings.get("retry_job")
    if retry_job is not None:
        if not isinstance(retry_job,str) or not HEX.fullmatch(retry_job):
            raise ValueError("شناسه بازبینی بکاپ نامعتبر است")
        status_path=JOBS/(retry_job+".json")
        try:
            old=json.loads(status_path.read_text())
        except (OSError,ValueError) as exc:
            raise ValueError("عملیات انتقال قبلی پیدا نشد") from exc
        if old.get("action")!="transfer" or old.get("state")!="failed":
            raise ValueError("فقط انتقال ناموفق قبلی قابل بررسی مجدد است")
    # tmpfs with root-only permissions: credentials never reach the disk/DB.
    if not RUN.is_dir():
        RUN.mkdir(mode=0o700)
    os.chmod(RUN, 0o700)
    path = RUN/(job+".json")
    content = json.dumps({
        "host": host, "username": username, "port": port,
        "password": password, "fingerprint": fingerprint,
        "include_media": settings.get("include_media", True),
        "retry_job": retry_job,
    }, ensure_ascii=False).encode()
    fd = os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def consume_handoff(job):
    if not isinstance(job, str) or not HEX.fullmatch(job):
        raise ValueError("Invalid job")
    path = RUN/(job+".json")
    raw = path.read_bytes()
    path.unlink()
    result = json.loads(raw)
    validate_target(result["host"],result["username"],result["port"])
    if not KEY_FINGERPRINT.fullmatch(result["fingerprint"]):
        raise ValueError("Invalid fingerprint")
    return result


def _askpass_script():
    return Path("/opt/masiha-vpn-extension/transfer_askpass.py")


def _ssh_args(cfg, known_hosts):
    return [
        "ssh", "-T", "-n", "-p", str(cfg["port"]),
        "-o", "BatchMode=no",
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile="+str(known_hosts),
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=20",
        "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR",
    ]


def _remote(cfg):
    host = cfg["host"]
    if ":" in host:
        host = "["+host+"]"
    return cfg["username"]+"@"+host


def _remote_command(script, use_sudo=False):
    # All filenames used in the remote shell are fixed constants or the
    # server-generated 32-hex job ID; never interpolate user-supplied shell.
    # Non-root users must have NOPASSWD sudo (no password embedded in commands).
    prefix="sudo -n " if use_sudo else ""
    return prefix+"sh -c "+shlex.quote(script)


def _env_with_askpass(secret_path):
    env = os.environ.copy()
    env.update({
        "SSH_ASKPASS": str(_askpass_script()),
        "SSH_ASKPASS_REQUIRE":"force",
        "DISPLAY":":0",
        "MASIHA_SSH_PASSWORD_FILE":str(secret_path),
    })
    return env


def _launch(cmd, env, timeout=90):
    return subprocess.run(cmd,env=env,stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,timeout=timeout,check=False,
                          start_new_session=True)


def ssh_command(cfg,known_hosts,env,script,timeout=90):
    cmd=_ssh_args(cfg,known_hosts)+[_remote(cfg),
                                    _remote_command(script,cfg["username"]!="root")]
    p=_launch(cmd,env,timeout)
    if p.returncode:
        raise RuntimeError("SSH اتصال یا اجرای دستور مقصد موفق نبود")
    return p.stdout.strip()


def _progress_report(job,phase,detail,extra=None):
    from web_backup_worker import status
    metadata={"phase":phase}
    if extra:
        metadata.update(extra)
    status(job,"running",detail,metadata=metadata,action="transfer")


def _rsync_file(cfg,known_hosts,env,source,remote_name,job,part,total_size):
    # rsync args contain no secret. .part is retained for resumed transport.
    ssh = _ssh_args(cfg,known_hosts)
    # For rsync, SSH -n is fine: rsync uses stdin as transport, so remove -n.
    ssh.remove("-n")
    remote_shell=" ".join(shlex.quote(c) for c in ssh)
    remote_path=IMPORTS+"/"+remote_name
    cmd=[
        "rsync","-a","--no-perms","--no-owner","--no-group","--partial",
        "--append-verify","--info=progress2","--timeout=120",
        "-e",remote_shell,
        *(["--rsync-path=sudo -n rsync"] if cfg["username"]!="root" else []),
        str(source),_remote(cfg)+":"+remote_path,
    ]
    process=subprocess.Popen(cmd,env=env,stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             start_new_session=True)
    start=time.monotonic()
    last=0.0
    prev=0
    try:
        while True:
            buf=process.stdout.read1(8192)
            if not buf:
                break
            for piece in re.split(br"[\r\n]",buf):
                try:
                    token=piece.decode("ascii","ignore")
                    m=PROGRESS_BYTES.search(token)
                    if m:
                        prev=max(prev,int(m.group(1).replace(",","")))
                except (ValueError,UnicodeError):
                    continue
            now=time.monotonic()
            if now-last>=2:
                rate=prev/max(now-start,1)
                _progress_report(job,"uploading",
                                 f"ارسال {part}: {prev/1048576:.0f} از {total_size/1048576:.0f} مگابایت",
                                 {"sent_bytes":min(prev,total_size),
                                  "total_bytes":total_size,"rate_bytes_s":int(rate)})
                last=now
            if now-start>MAX_RUN:
                process.kill()
                raise RuntimeError("زمان انتقال بیش از حد مجاز شد")
        code=process.wait(timeout=45)
        if code:
            raise RuntimeError("انتقال فایل با rsync ناموفق بود؛ با اجرای مجدد می‌توان ادامه داد")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()


class RemoteInspectionError(RuntimeError):
    """Known public status only. Never propagate remote logs verbatim."""


def _source_archive_for_job(previous):
    log=JOBS/(previous+".backup.log")
    if not log.is_file():
        raise RemoteInspectionError("گزارش بکاپ قبلی در مبدأ موجود نیست")
    matches=re.findall(r"^BACKUP_READY (.+)$",log.read_text(errors="replace"),re.M)
    if len(matches)!=1:
        raise RemoteInspectionError("نام فایل بکاپ قبلی قابل شناسایی نیست")
    original=Path(matches[0])
    if original.parent!=BACKUPS or not re.fullmatch(
            r"masiha-full-[a-zA-Z0-9_.-]+\.tar\.gz\.gpg",original.name):
        raise RemoteInspectionError("مسیر بکاپ قبلی معتبر نیست")
    if original.is_symlink() or not original.is_file():
        raise RemoteInspectionError("بکاپ قبلی در سرور مبدأ حذف شده است")
    return original


def _remote_worker_hint(cfg,known,env,old_job):
    # Evaluate failure categories ON destination, and transmit fixed labels
    # only. No raw traceback, configuration, usernames or secrets reach UI.
    script=f"""python3 - <<'PY'
from pathlib import Path
p=Path('/root/masiha-backups/web-jobs/{old_job}.log')
try: raw=p.read_bytes()[-32768:].decode('utf-8','replace').lower()
except OSError:raw=''
if 'no space left on device' in raw or 'not enough free space' in raw:
 print('DISK')
elif 'bad session key' in raw or 'decryption failed' in raw or 'gpg: decryption failed' in raw:
 print('DECRYPT')
elif 'checksum failed' in raw or 'sha256 mismatch' in raw:
 print('HASH')
elif 'blockingioerror' in raw or 'resource temporarily unavailable' in raw:
 print('BUSY')
elif 'no module named' in raw or 'module not found' in raw or 'file not found' in raw:
 print('COMPONENT')
elif 'database' in raw and ('mismatch' in raw or 'incomplete' in raw):
 print('SCHEMA')
else:print('OTHER')
PY"""
    labels={
       "DISK":"فضای آزاد مقصد برای بازکردن و بررسی بکاپ کافی نیست",
       "DECRYPT":"خطا در رمزگشایی بکاپ روی مقصد؛ کلید و فرمت بررسی شود",
       "HASH":"صحت یکی از فایل‌های داخل آرشیو بکاپ تأیید نشد",
       "BUSY":"موتور بکاپ مقصد درگیر یک عملیات هم‌زمان بوده است؛ اکنون قابل بررسی مجدد است",
       "COMPONENT":"ابزار یا فایل ضروری برای بررسی بکاپ در مقصد موجود نیست",
       "SCHEMA":"نسخه یا ساختار داده بکاپ با بررسی مقصد سازگار نیست",
       "OTHER":"گزارش فنی بررسی بکاپ در مقصد نیاز به بررسی دارد",
    }
    try:
        result=ssh_command(cfg,known,env,script,40).strip()
    except RuntimeError:
        result="OTHER"
    return labels.get(result,labels["OTHER"])


def _remote_inspection_status(cfg,known,env,old_job):
    raw=ssh_command(cfg,known,env,
        "cat "+shlex.quote('/root/masiha-backups/web-jobs/'+old_job+'.json')+
        " 2>/dev/null || true",30)
    if not raw:
        return {}
    try:
        data=json.loads(raw)
        return data if isinstance(data,dict) else {}
    except ValueError:
        return {}


def _wait_remote_verified(cfg,known,env,job,old_job,host,file_size,digest):
    from web_backup_worker import status
    for _ in range(285):
        time.sleep(6)
        try:
            state=_remote_inspection_status(cfg,known,env,old_job)
        except RuntimeError:
            continue
        if state.get("state")=="done" and state.get("action")=="inspect":
            metadata={"host":host,"remote_job":old_job,"bytes":file_size,
                      "sha256":digest,"phase":"completed",
                      "destination_ready_for_restore":True}
            status(job,"done","فایل روی مقصد تأیید شد؛ آماده بازیابی دستی، بدون ارسال دوباره",
                   metadata=metadata,action="transfer")
            return
        if state.get("state")=="failed":
            msg=_remote_worker_hint(cfg,known,env,old_job)
            raise RemoteInspectionError("فایل قبلاً ارسال شده است؛ "+msg+
                                        "؛ ارسال دوباره لازم نیست")
    raise RemoteInspectionError("بررسی بکاپ مقصد هنوز به پایان نرسیده؛ فایل در مقصد باقی است")


def _reinspect_remote(job,previous,cfg,known,env,host):
    previous_state=JOBS/(previous+".json")
    try:
        state=json.loads(previous_state.read_text())
    except (OSError,ValueError) as exc:
        raise RemoteInspectionError("انتقال قبلی روی سرور مبدأ شناسایی نشد") from exc
    if state.get("action")!="transfer" or state.get("state")!="failed":
        raise RemoteInspectionError("فقط انتقال ناموفق قبلی را می‌توان مجدداً بررسی کرد")
    archive=_source_archive_for_job(previous)
    key=Path("/root/.masiha-backup.key")
    if not key.is_file():
        raise RemoteInspectionError("کلید بکاپ در سرور مبدأ موجود نیست")
    with archive.open("rb") as f:
        digest=hashlib.file_digest(f,"sha256").hexdigest()
    with key.open("rb") as f:
        keydigest=hashlib.file_digest(f,"sha256").hexdigest()
    _progress_report(job,"remote-check","کنترل فایل قبلاً منتقل‌شده بدون ارسال مجدد",
                     {"host":host,"remote_job":previous,"transport_verified":True})
    script=f"""set -eu; umask 077
cd {shlex.quote(IMPORTS)}
test -f {previous}.gpg && test -f {previous}.key
test "$(sha256sum {previous}.gpg | cut -d' ' -f1)" = {digest}
test "$(sha256sum {previous}.key | cut -d' ' -f1)" = {keydigest}
test -f /opt/masiha-vpn-extension/web_backup_worker.py
rm -f /root/masiha-backups/web-jobs/{previous}.json
/usr/bin/systemd-run --quiet --collect --unit=masiha-webbackup-reinspect-{job} --property=Type=exec /usr/bin/python3 /opt/masiha-vpn-extension/web_backup_worker.py inspect {previous}
echo REINSPECT_STARTED
"""
    if "REINSPECT_STARTED" not in ssh_command(cfg,known,env,script,180):
        raise RemoteInspectionError("شروع بازبینی فایل منتقل‌شده در مقصد ناموفق بود")
    _progress_report(job,"remote-inspect","در حال بررسی مجدد آرشیو روی مقصد، بدون ارسال فایل",
                     {"host":host,"remote_job":previous,"transport_verified":True})
    _wait_remote_verified(cfg,known,env,job,previous,host,archive.stat().st_size,digest)


def run_transfer(job):
    from web_backup_worker import status
    cfg=consume_handoff(job)
    host,username,port=validate_target(cfg["host"],cfg["username"],cfg["port"])
    fingerprint=cfg["fingerprint"]
    if not _askpass_script().is_file():
        raise RuntimeError("برنامه SSH Askpass نصب نشده است")
    backup_key=Path("/root/.masiha-backup.key")
    backup_key.parent.mkdir(parents=True,exist_ok=True)
    _progress_report(job,"verifying-ssh","بررسی هویت SSH مقصد")
    scanned=probe_host(host,username,port)
    if scanned["fingerprint"]!=fingerprint:
        raise RuntimeError("اثر انگشت SSH مقصد تغییر کرده است؛ انتقال متوقف شد")
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    JOBS.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix="masiha-transfer-",dir="/run") as tmp:
        td=Path(tmp)
        known=td/"known_hosts"
        known.write_text(scanned["known_host"]+"\n")
        known.chmod(0o600)
        secret=td/"ssh_password"
        secret.write_text(cfg["password"])
        secret.chmod(0o600)
        cfg.pop("password")
        env=_env_with_askpass(secret)
        if cfg.get("retry_job"):
            _reinspect_remote(job,cfg["retry_job"],cfg,known,env,host)
            return
        # Prepare the target using only the supplied root/sudo account.
        _progress_report(job,"target-check","آزمایش SSH و فضای مقصد")
        setup=f"""set -eu; umask 077
test "$(id -u)" = 0 || exit 3
install -d -m 0700 {shlex.quote(IMPORTS)}
command -v rsync >/dev/null || exit 4
test -f /opt/masiha-vpn-extension/web_backup_worker.py || exit 4
test -f /opt/masiha-vpn-extension/full_backup.py || exit 4
df -Pk {shlex.quote(IMPORTS)} | tail -1
"""
        output=ssh_command(cfg,known,env,setup,60)
        disk_kb=int(output.splitlines()[-1].split()[3])
        _progress_report(job,"backup","در حال تهیه بکاپ رمزگذاری‌شده روی همین سرور")
        before=set(BACKUPS.glob("masiha-full-*.tar.gz.gpg"))
        backup_log=JOBS/(job+".backup.log")
        args=["/usr/bin/python3",str(CLI),"backup","--output-dir",str(BACKUPS),
              "--key-file",str(backup_key)]
        if not cfg["include_media"]:
            args.append("--skip-media")
        with backup_log.open("wb") as log:
            process=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,
                                   timeout=5400,check=False)
        if process.returncode:
            raise RuntimeError("ساخت بکاپ ناموفق بود؛ گزارش در سرور مبدأ ثبت شد")
        matches=re.findall(r"^BACKUP_READY (.+)$",backup_log.read_text(errors="replace"),re.M)
        if len(matches)!=1:
            raise RuntimeError("نام فایل بکاپ ساخته‌شده مشخص نشد")
        archive=Path(matches[0])
        if archive.parent!=BACKUPS or archive not in (set(BACKUPS.glob("masiha-full-*.tar.gz.gpg"))-before):
            raise RuntimeError("فایل بکاپ جدید قابل تأیید نیست")
        if archive.is_symlink() or not archive.is_file() or not backup_key.is_file():
            raise RuntimeError("بکاپ یا کلید رمزگذاری یافت نشد")
        file_size=archive.stat().st_size
        if disk_kb*1024 < file_size*2 + 128*1048576:
            raise RuntimeError("فضای آزاد مقصد برای دریافت و بررسی بکاپ کافی نیست")
        with archive.open("rb") as inp:
            digest=hashlib.file_digest(inp,"sha256").hexdigest()
        with backup_key.open("rb") as inp:
            keydigest=hashlib.file_digest(inp,"sha256").hexdigest()
        parts=[
            (archive,job+".gpg.part",file_size,digest),
            (backup_key,job+".key.part",backup_key.stat().st_size,keydigest),
        ]
        for i,(source,remote_file,sz,_) in enumerate(parts):
            for attempt in range(1,4):
                _progress_report(job,"uploading",
                                 f"ارسال مستقیم فایل {i+1} از ۲ فایل (تلاش {attempt})")
                try:
                    _rsync_file(cfg,known,env,source,remote_file,job,i+1,sz)
                    break
                except RuntimeError:
                    if attempt==3:
                        raise
                    time.sleep(4)
                    # --append-verify resumes the remote .part and verifies
                    # existing bytes instead of starting from zero.
        _progress_report(job,"verifying","کنترل SHA-256 فایل و کلید روی سرور مقصد")
        remote_script=f"""set -eu;umask 077
cd {shlex.quote(IMPORTS)}
test "$(sha256sum {job}.gpg.part | cut -d' ' -f1)" = {digest} || exit 5
test "$(sha256sum {job}.key.part | cut -d' ' -f1)" = {keydigest} || exit 6
mv -f {job}.gpg.part {job}.gpg
mv -f {job}.key.part {job}.key
chmod 0600 {job}.gpg {job}.key
/usr/bin/systemd-run --quiet --collect --unit=masiha-webbackup-inspect-{job} --property=Type=exec /usr/bin/python3 /opt/masiha-vpn-extension/web_backup_worker.py inspect {job}
echo TRANSFER_READY
"""
        output=ssh_command(cfg,known,env,remote_script,180)
        if "TRANSFER_READY" not in output:
            raise RuntimeError("صحت فایل مقصد تأیید نشد")
        _progress_report(job,"remote-inspect","فایل روی مقصد رسید و SHA-256 تأیید شد؛ در حال بررسی محتوا",
                         {"host":host,"remote_job":job,"transport_verified":True,
                          "bytes":file_size,"sha256":digest})
        # The remote inspection is asynchronous, so poll a bounded period.
        for _ in range(150):
            time.sleep(6)
            try:
                state_json=ssh_command(cfg,known,env,
                    f"cat {shlex.quote('/root/masiha-backups/web-jobs/'+job+'.json')} 2>/dev/null || true",30)
                state=json.loads(state_json) if state_json else {}
            except (ValueError,RuntimeError):
                continue
            if state.get("state")=="done" and state.get("action")=="inspect":
                metadata={"host":host,"remote_job":job,"bytes":file_size,
                          "sha256":digest,"phase":"completed",
                          "destination_ready_for_restore":True}
                status(job,"done","بکاپ مستقیماً منتقل و در مقصد بررسی شد؛ بازیابی انجام نشده است",
                       metadata=metadata,action="transfer")
                return
            if state.get("state")=="failed":
                reason=_remote_worker_hint(cfg,known,env,job)
                raise RemoteInspectionError("فایل با SHA-256 روی مقصد تأیید شد؛ "+reason+
                                            "؛ ارسال مجدد لازم نیست")
        raise RuntimeError("فایل منتقل شد اما بررسی مقصد به‌موقع پایان نیافت")


def main():
    import sys
    import fcntl
    os.umask(0o077)
    if len(sys.argv)!=2 or not HEX.fullmatch(sys.argv[1]):
        raise SystemExit(2)
    job=sys.argv[1]
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(BACKUPS/"web-work.lock","a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            run_transfer(job)
        except Exception as exc:
            from web_backup_worker import status
            metadata=None
            try:
                current=json.loads((JOBS/(job+".json")).read_text())
                if current.get("action")=="transfer" and isinstance(current.get("metadata"),dict):
                    metadata=current["metadata"]
            except (OSError,ValueError):
                pass
            detail=(str(exc) if isinstance(exc,RemoteInspectionError) else
                    "انتقال در مرحله "+str((metadata or {}).get("phase","اتصال"))+
                    " ناموفق بود؛ وضعیت SSH و گزارش فنی سرور را بررسی کنید")
            status(job,"failed",detail,metadata=metadata,action="transfer")
            raise


if __name__=="__main__":
    main()
