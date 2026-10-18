#!/usr/bin/env python3
"""Independent, authenticated Masiha UI API, outside the upstream x-ui binary."""
import cgi
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, unquote

# The domain and native upstream address come from the deployed extension.
CONFIG = json.loads(Path("/etc/masiha-vpn-extension/config.json").read_text())
HOST = CONFIG["domain"]
if not isinstance(HOST, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,250}", HOST):
    raise RuntimeError("Invalid extension domain")
PANEL = "http://127.0.0.1:62053/" + CONFIG.get("panel_path", "/xui/").strip("/")
BIND = ("127.0.0.1", 61794)
BACKUPS = Path("/root/masiha-backups")
IMPORTS = BACKUPS / "web-imports"
JOBS = BACKUPS / "web-jobs"
SECRET_FILE = Path("/root/.masiha-backup.key")
HEX = re.compile(r"^[a-f0-9]{32}$")
ARCHIVE = re.compile(r"^masiha-full-[a-zA-Z0-9_.-]+\.tar\.gz\.gpg$")
PATH_BASE = "/xui/panel/api/"
MAX_UPLOAD = 512 << 20
PAY_SLOTS = threading.BoundedSemaphore(4)
LOG = __import__("logging").getLogger("masiha-sidecar")
os.umask(0o077)


def envelope(success, obj=None, msg=""):
    result = {"success": bool(success), "msg": msg}
    if obj is not None:
        result["obj"] = obj
    return result


def json_command(script, request, timeout=65):
    if not isinstance(request, dict):
        raise ValueError("Invalid request")
    body = json.dumps(request, ensure_ascii=False).encode()
    if len(body) > 65536:
        raise ValueError("Request exceeds size limit")
    p = subprocess.run(["/usr/bin/python3", "/opt/masiha-vpn-extension/" + script],
                       input=body, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=timeout, check=False)
    if p.returncode or not p.stdout or len(p.stdout) > 8_000_000:
        raise RuntimeError("Management process failed; check local journal")
    return json.loads(p.stdout)


def user_is_only_admin():
    # All valid sessions must belong to the sole admin. Fail closed if admins
    # create additional x-ui users; before enabling multi-user support the
    # native admin identity must be cryptographically verified.
    try:
        con = sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute("SELECT id FROM users ORDER BY id LIMIT 2").fetchall()
            return len(rows) == 1 and rows[0][0] == 1
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return False


def upstream_panel_token(cookie):
    if not cookie or len(cookie) > 8192 or not user_is_only_admin():
        return None
    req = urllib.request.Request(
        PANEL + "/panel/csrf-token",
        headers={"Host": HOST, "Cookie": cookie,
                 "X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json"})
    # Reject redirects to the login page. Do not follow to untrusted hosts.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=5) as r:
            if r.status != 200:
                return None
            payload = json.loads(r.read(2048))
            token = payload.get("obj")
            return token if payload.get("success") is True and isinstance(token, str) and 10 <= len(token) <= 512 else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def launch(action, job):
    if action not in ("create", "inspect", "restore") or not HEX.fullmatch(job):
        raise ValueError("Invalid backup job")
    subprocess.run(["/usr/bin/systemd-run", "--quiet", "--collect",
                    "--unit=masiha-webbackup-" + action + "-" + job,
                    "--property=Type=exec", "/usr/bin/python3",
                    "/opt/masiha-vpn-extension/web_backup_worker.py",
                    action, job], check=True, timeout=12,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def private(path):
    try:
        st = path.lstat()
        return path.is_file() and not path.is_symlink() and st.st_mode & 0o077 == 0
    except OSError:
        return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        # Do not record cookies, secrets, authorities, or posted data.
        LOG.info("http %s %s", self.command, self.path.split("?")[0][:145])

    def send_bytes(self, code, content, content_type="application/json; charset=utf-8", headers=None):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def error_json(self, code, msg):
        self.send_bytes(code, envelope(False, msg=msg))

    def auth(self, changing=False):
        if self.headers.get("Host") != HOST:
            self.error_json(403, "Invalid host")
            return False
        token = upstream_panel_token(self.headers.get("Cookie", ""))
        if not token:
            self.error_json(401, "ورود مدیر به پنل x-ui الزامی است.")
            return False
        if changing:
            # The native x-ui CSRF token is bound to the x-ui session.
            supplied = self.headers.get("X-CSRF-Token", "")
            origin = self.headers.get("Origin")
            referer = self.headers.get("Referer")
            if not supplied or not hmac.compare_digest(supplied, token):
                self.error_json(403, "CSRF token is missing or invalid")
                return False
            if origin and origin != "https://" + HOST:
                self.error_json(403, "Origin not allowed")
                return False
            if referer and not referer.startswith("https://" + HOST + "/"):
                self.error_json(403, "Referer not allowed")
                return False
            if self.headers.get("X-Requested-With") != "XMLHttpRequest":
                self.error_json(403, "X-Requested-With required")
                return False
        return True

    def read_json(self, max_length=65536):
        length = int(self.headers.get("Content-Length", "-1"))
        if length < 0 or length > max_length:
            raise ValueError("Invalid Content-Length")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("Content-Type must be JSON")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.send_bytes(200, {"up": True, "native_xui": "independent"})
        if path == "/xui/sales-payment":
            return self.payment()
        if not self.auth():
            return
        if path == PATH_BASE + "masiha-backup/status":
            backups = [{"name": f.name, "size": f.stat().st_size,
                        "created": __import__("datetime").datetime.fromtimestamp(f.stat().st_mtime,
                                __import__("datetime").timezone.utc).isoformat()}
                       for f in BACKUPS.iterdir() if ARCHIVE.fullmatch(f.name) and private(f)]
            backups.sort(key=lambda x: x["created"], reverse=True)
            jobs = []
            if JOBS.is_dir():
                for f in JOBS.glob("*.json"):
                    if HEX.fullmatch(f.stem) and private(f):
                        try:
                            jobs.append(json.loads(f.read_text()))
                        except (OSError, ValueError):
                            pass
            return self.send_bytes(200, envelope(True, {"backups": backups, "jobs": jobs, "has_key": private(SECRET_FILE)}))
        prefix = PATH_BASE + "masiha-backup/download/"
        if path.startswith(prefix):
            name = unquote(path[len(prefix):])
            if name == "backup-secret.key":
                target = SECRET_FILE
            elif ARCHIVE.fullmatch(name):
                target = BACKUPS / name
            else:
                return self.error_json(404, "Not found")
            if not private(target):
                return self.error_json(404, "Not found")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Content-Disposition", 'attachment; filename="' + name + '"')
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                with target.open("rb") as f:
                    while data := f.read(131072):
                        self.wfile.write(data)
            except (OSError, BrokenPipeError):
                pass
            return
        return self.error_json(404, "Unknown route")

    def payment(self):
        params = parse_qs(urlsplit(self.path).query)
        if params.get("Status", [""])[0] != "OK":
            return self.send_bytes(200, "پرداخت لغو شد یا کامل نشد. به ربات برگردید.".encode(), "text/plain; charset=utf-8")
        authority = params.get("Authority", [""])[0]
        if not 10 <= len(authority) <= 100 or not re.fullmatch(r"[A-Za-z0-9-]+", authority):
            return self.send_bytes(400, "شناسه نامعتبر".encode(), "text/plain; charset=utf-8")
        if not PAY_SLOTS.acquire(blocking=False):
            return self.send_bytes(429, "کمی بعد دوباره تلاش کنید".encode(), "text/plain; charset=utf-8")
        try:
            p = subprocess.run(["/usr/bin/python3", "/opt/masiha-vpn-extension/sales.py", "payment"],
                               input=json.dumps({"authority": authority}).encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35)
            data = json.loads(p.stdout) if p.returncode == 0 else {}
            result = str(data.get("obj") or data.get("msg") or "خطا در تأیید پرداخت")
            return self.send_bytes(200 if data.get("success") else 400,
                                   result[:2000].encode(), "text/plain; charset=utf-8")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return self.send_bytes(502, "خطا در بررسی پرداخت".encode(), "text/plain; charset=utf-8")
        finally:
            PAY_SLOTS.release()

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.auth(changing=True):
            return
        try:
            if path == PATH_BASE + "masiha-vpn":
                body = self.read_json(16384)
                return self.send_bytes(200, json_command("manager.py", body))
            if path == PATH_BASE + "sales-bot":
                body = self.read_json(65536)
                return self.send_bytes(200, json_command("sales.py", body))
            if path == PATH_BASE + "masiha-backup/create":
                self.read_json(2048)
                job = secrets.token_hex(16)
                launch("create", job)
                return self.send_bytes(202, envelope(True, {"job": job}))
            if path == PATH_BASE + "masiha-backup/import":
                return self.import_backup()
            if path == PATH_BASE + "masiha-backup/restore":
                body = self.read_json(2048)
                job = body.get("job", "")
                if body.get("confirm") != "RESTORE" or not isinstance(job, str) or not HEX.fullmatch(job):
                    return self.error_json(400, "Type RESTORE to confirm")
                state = JOBS / (job + ".json")
                try:
                    verified = json.loads(state.read_text()).get("state") == "done"
                except (OSError, ValueError):
                    verified = False
                if not verified or not all(private(IMPORTS / (job + ext)) for ext in (".gpg", ".key")):
                    return self.error_json(409, "Verify imported archive first")
                launch("restore", job)
                return self.send_bytes(202, envelope(True, {"job": job}))
            return self.error_json(404, "Unknown route")
        except (ValueError, KeyError, TypeError) as exc:
            return self.error_json(400, str(exc)[:200])
        except (OSError, RuntimeError, subprocess.SubprocessError, sqlite3.Error):
            LOG.exception("Internal sidecar operation failed")
            return self.error_json(503, "Operation failed; inspect sidecar logs")

    def import_backup(self):
        ct = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "-1"))
        if not ct.startswith("multipart/form-data;") or not (1024 <= length <= MAX_UPLOAD):
            return self.error_json(413, "Invalid upload size or Content-Type")
        IMPORTS.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = secrets.token_hex(16)
        created = []
        try:
            # cgi.FieldStorage stores uploaded files in temporary disk files,
            # rather than retaining huge multipart payloads in memory.
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
                "REQUEST_METHOD": "POST", "CONTENT_TYPE": ct, "CONTENT_LENGTH": str(length)},
                keep_blank_values=True)
            if set(form.keys()) != {"archive", "key"}:
                raise ValueError("Archive and key required")
            for field, extension, cap in (("archive", ".gpg", MAX_UPLOAD - 4096), ("key", ".key", 4096)):
                item = form[field]
                if isinstance(item, list) or not item.file or not item.filename:
                    raise ValueError("Exactly one file per field")
                destination = IMPORTS / (job + extension)
                created.append(destination)
                with destination.open("xb") as dest:
                    os.chmod(destination, 0o600)
                    size = 0
                    while b := item.file.read(131072):
                        size += len(b)
                        if size > cap:
                            raise ValueError("File exceeds size limit")
                        dest.write(b)
                    dest.flush()
                    os.fsync(dest.fileno())
                if size < (1024 if field == "archive" else 16):
                    raise ValueError("Uploaded file is too small")
            launch("inspect", job)
            return self.send_bytes(202, envelope(True, {"job": job}))
        except (OSError, ValueError, subprocess.SubprocessError):
            for f in created:
                f.unlink(missing_ok=True)
            return self.error_json(400, "Upload failed; check archive and key")


if __name__ == "__main__":
    from logging import basicConfig, INFO
    basicConfig(level=INFO)
    server = ThreadingHTTPServer(BIND, Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)
