#!/usr/bin/env python3
"""Safely stream licensed MP3 via a short-lived local file into Telegram Bot API.

Raw MP3 bytes never enter SQLite. Telegram is the durable media store; only the
bot-scoped file_id is retained in media_items. Inputs never cause SSRF.
"""
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import signal
import ssl
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

_STORAGE_SEND_LOCK=threading.Lock()
_STORAGE_LAST=0.0
_TRANSCODE_LOCK=threading.BoundedSemaphore(1)

def storage_send_pace():
    """Bound sendAudio + sendPhoto to one per 1.15s for one storage chat."""
    global _STORAGE_LAST
    with _STORAGE_SEND_LOCK:
        wait=_STORAGE_LAST+1.15-time.monotonic()
        if wait>0:
            time.sleep(wait)
        _STORAGE_LAST=time.monotonic()

MAX_AUDIO = 47 * 1024 * 1024  # Telegram multipart sendAudio <= 50 MB
CHUNK = 64 * 1024
USER_AGENT = "MasihaBotCatalog/2.0 (licensed RozMusic audio transfer)"


def validate_mp3_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.username or p.password or
        p.port not in (None, 443) or p.fragment):
        raise ValueError("Unsafe audio source URL")
    host = (p.hostname or "").lower()
    if host != "rozmusic.com" and not host.endswith(".rozmusic.com"):
        raise ValueError("Audio host is not authorized RozMusic")
    if not p.path.lower().endswith(".mp3"):
        raise ValueError("Audio source must be MP3")
    # Keep the original host/scheme untouched; only percent-encode unsafe
    # characters in already validated media paths and queries. Music sources
    # frequently publish raw spaces and parentheses inside *.mp3 URLs.
    encoded_path = quote(p.path, safe="/%:@!$&'()*+,;=-._~")
    encoded_query = quote(p.query, safe="/%:@!$&'()*+,;=?-._~")
    safe_url = urlunsplit((p.scheme, p.netloc, encoded_path, encoded_query, ""))
    if urlsplit(safe_url).hostname != host:
        raise ValueError("Untrusted normalized audio host")
    return safe_url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = validate_mp3_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


AUDIO_HTTP = build_opener(SafeRedirect)


def download_mp3(url, dest):
    url = validate_mp3_url(url)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "audio/mpeg,*/*;q=0.5"})
    with AUDIO_HTTP.open(req, timeout=60) as response:
        validate_mp3_url(response.url)
        size_header = response.headers.get("Content-Length", "")
        if size_header.isdigit() and int(size_header) > MAX_AUDIO:
            raise ValueError("Music exceeds Telegram 50 MB upload limit")
        downloaded = 0
        with Path(dest).open("xb") as output:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_AUDIO:
                    raise ValueError("Downloaded audio exceeds Telegram upload size")
                output.write(chunk)
        if downloaded < 4096:
            raise ValueError("Downloaded file too small to be a music file")
        with Path(dest).open("rb") as f:
            magic = f.read(12)
        if magic[:3] == b"ID3" or (len(magic) >= 2 and magic[0] == 0xff and magic[1] & 0xe0 == 0xe0):
            return downloaded
        # One licensed source mislabels M4A (MP4/ftyp) audio as .mp3.
        # Convert only this known container type; never publish non-audio.
        if magic[4:8] != b"ftyp":
            raise ValueError("Download is not an MP3 audio stream")
        converted = Path(dest).with_name("converted-track.mp3")
        try:
            with _TRANSCODE_LOCK:
                subprocess.run(["/usr/bin/ffmpeg","-nostdin","-hide_banner",
                    "-loglevel","error","-i",str(dest),"-map","0:a:0",
                    "-vn","-sn","-dn","-c:a","libmp3lame","-b:a","160k",
                    "-threads","1","-f","mp3",str(converted)],
                    check=True,timeout=120,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            converted_size = converted.stat().st_size
            if not 4096 <= converted_size <= MAX_AUDIO:
                raise ValueError("Converted MP3 exceeds allowed size")
            with converted.open("rb") as f:
                prefix=f.read(3)
            if prefix != b"ID3" and not (len(prefix)>=2 and prefix[0]==0xff and prefix[1]&0xe0==0xe0):
                raise ValueError("Converted audio is not MP3")
            os.replace(converted,dest)
            return converted_size
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,FileNotFoundError) as exc:
            raise ValueError("Source M4A audio could not be safely converted") from exc
        finally:
            converted.unlink(missing_ok=True)


def multipart_fields(fields, boundary):
    # Keep metadata in memory and stream the audio body separately.
    parts = []
    for key, value in fields.items():
        val = str(value)
        parts.append(("--" + boundary + "\r\n"
            + 'Content-Disposition: form-data; name="' + key + '"\r\n'
            + "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            + val + "\r\n").encode("utf-8"))
    header = ('--' + boundary + "\r\n"
              'Content-Disposition: form-data; name="audio"; filename="track.mp3"\r\n'
              "Content-Type: audio/mpeg\r\n\r\n").encode()
    tail = ("\r\n--" + boundary + "--\r\n").encode()
    return b"".join(parts) + header, tail


def upload_to_telegram(path, artist, title, token, chat_id):
    """Multipart upload, max ~47 MB RAM-free. Return bot-scoped Telegram IDs."""
    if not token or not chat_id:
        raise RuntimeError("Bot credentials and private storage chat are required")
    file_size = Path(path).stat().st_size
    if not 4096 <= file_size <= MAX_AUDIO:
        raise ValueError("Invalid MP3 file size for Telegram")
    boundary = "Masiha_" + secrets.token_hex(16)
    prefix, suffix = multipart_fields(
        {"chat_id": chat_id, "title": title[:128], "performer": artist[:128],
         "disable_notification": "true"},
        boundary)
    # Python http.client handles HTTPS certificate verification.
    import sales
    conn=(http.client.HTTPConnection("127.0.0.1",8081,timeout=120)
          if sales.telegram_local_enabled()
          else http.client.HTTPSConnection("api.telegram.org",timeout=120,
                                           context=ssl.create_default_context()))
    if not sales.telegram_local_enabled():
        conn._create_connection = sales._telegram_connect
    try:
        storage_send_pace()
        conn.putrequest("POST", "/bot" + token + "/sendAudio", skip_accept_encoding=True)
        conn.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        conn.putheader("Content-Length", str(len(prefix) + file_size + len(suffix)))
        conn.endheaders()
        conn.send(prefix)
        with Path(path).open("rb") as source:
            while part := source.read(CHUNK):
                conn.send(part)
        conn.send(suffix)
        response = conn.getresponse()
        raw = response.read(200_000)
        if response.status != 200:
            raise RuntimeError("Telegram upload failed HTTP " + str(response.status))
        result = json.loads(raw)
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected the audio upload")
        message = result.get("result") or {}
        audio = message.get("audio") or {}
        file_id = audio.get("file_id")
        unique_id = audio.get("file_unique_id")
        if not (isinstance(file_id, str) and file_id and
                isinstance(unique_id, str) and unique_id):
            raise RuntimeError("Telegram response contains no reusable audio file_id")
        return {"file_id": file_id, "file_unique_id": unique_id,
                "message_id": message.get("message_id")}
    finally:
        conn.close()


def local_cache_snapshot(settings, kind="videos"):
    """List known Local Bot API file names without triggering getFile downloads."""
    import re
    import sales
    if kind not in ("music", "videos") or not sales.telegram_local_enabled():
        return None
    token = settings.get("token", "")
    if not re.fullmatch(r"[0-9]{6,12}:[A-Za-z0-9_-]{20,}", token):
        return None
    folder = Path("/var/lib/telegram-bot-api") / token / kind
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        return (time.time(), frozenset(p.name for p in folder.iterdir()
                 if re.fullmatch(r"file_[0-9]+", p.name)))
    except OSError:
        return None


def cleanup_uploaded_from_snapshot(settings, kind, snapshot, expected_size):
    """Delete only freshly created exact-size video cache after durable DB commit.

    No getFile call: getFile for large old videos can itself fill the disk.
    """
    import re
    import stat
    if kind != "videos" or snapshot is None or not expected_size:
        return False
    started, old_names = snapshot
    token = settings.get("token", "")
    if not re.fullmatch(r"[0-9]{6,12}:[A-Za-z0-9_-]{20,}", token):
        return False
    folder = Path("/var/lib/telegram-bot-api") / token / kind
    if folder.is_symlink() or not folder.is_dir():
        return False
    removed = False
    try:
        for p in folder.iterdir():
            if p.name in old_names or not re.fullmatch(r"file_[0-9]+", p.name):
                continue
            meta = p.lstat()
            if (stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and
                meta.st_size == expected_size and meta.st_mtime >= started - 3):
                p.unlink()
                removed = True
    except OSError:
        return removed
    return removed


def cleanup_uploaded_local_cache(settings, file_id, kind="music"):
    """Remove a confirmed Telegram upload's local cache after DB commit.

    Telegram file_id is the durable reference. Never remove an arbitrary path
    returned by getFile: only a regular file_N in this bot's own API cache.
    """
    import re
    import stat
    import sales
    if kind not in ("music", "videos") or not sales.telegram_local_enabled():
        return False
    token = settings.get("token", "")
    if not re.fullmatch(r"[0-9]{6,12}:[A-Za-z0-9_-]{20,}", token):
        return False
    expected = Path("/var/lib/telegram-bot-api") / token / kind
    try:
        info = sales.tg(settings, "getFile", {"file_id": file_id})
        path = Path(info.get("file_path") or "")
        if not path.is_absolute() or path.parent != expected:
            return False
        if not re.fullmatch(r"file_[0-9]+", path.name):
            return False
        if expected.is_symlink() or path.is_symlink():
            return False
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            return False
        path.unlink()
        return True
    except (OSError, ValueError, TypeError, KeyError):
        # A housekeeping timer handles old files when local API is unavailable.
        return False


def delete_storage_message(settings, chat_id, message_id):
    if not isinstance(message_id, int):
        return False
    import sales
    try:
        result = sales.tg(settings, "deleteMessage",
                          {"chat_id": chat_id, "message_id": message_id})
        return result is True
    except Exception:
        # Nonfatal: the MP3 is already hosted by Telegram and has a file_id.
        return False


def verified_storage_chat(settings):
    """Use an explicitly configured storage channel; otherwise existing bot admin."""
    explicit = settings.get("roz_storage_chat")
    if explicit is not None and explicit != "":
        import sales
        chat = sales.normalize_channel(str(explicit))
        return chat
    admins = settings.get("admins") or []
    if not admins:
        raise RuntimeError("No bot admin chat available for temporary Telegram upload")
    return admins[0]


def stream_once(url, artist, title, settings, article_url=None, fingerprint_status=None):
    """Even on a failed transfer or interruption, remove the temporary MP3."""
    if not settings.get("token") or not settings.get("enabled"):
        raise RuntimeError("Bot is not enabled or its token is not configured")
    chat_id = verified_storage_chat(settings)
    with tempfile.TemporaryDirectory(prefix="masiha-roz-mp3-", dir="/tmp") as temp:
        path = Path(temp) / "track.mp3"
        size = download_mp3(url, path)
        response = upload_to_telegram(path, artist, title, settings["token"], chat_id)
        # The temporary MP3 is available only here: persist a fingerprint,
        # never the original audio. An indexing failure must not lose media.
        if article_url:
            indexed = False
            try:
                import audio_fingerprint
                indexed = bool(audio_fingerprint.index_imported_source(article_url,path))
            except (ImportError,OSError,ValueError,RuntimeError,subprocess.SubprocessError):
                pass
            # Results are per-job: no global/thread-local status races.
            if isinstance(fingerprint_status,list):
                fingerprint_status.append(indexed)
        # Delete the *message* silently. file_id remains usable by the same bot,
        # as confirmed by real getFile check before full-archive activation.
        removed = delete_storage_message(settings, chat_id, response["message_id"])
        return response["file_id"], response["file_unique_id"], size, removed



def upload_cover(url, settings):
    """Upload one resized JPEG to the bot's storage chat and keep photo file_id.

    Download only licensed-source HTTPS images. Cover bytes live in RAM only.
    """
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError
    url = validate_cover_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/png,image/webp"})
    with AUDIO_HTTP.open(request, timeout=18) as response:
        validate_cover_url(response.url)
        if response.headers.get("Content-Length", "").isdigit() and int(response.headers["Content-Length"]) > 2_000_000:
            raise ValueError("Cover file too large")
        raw = response.read(2_000_001)
    if not 100 <= len(raw) <= 2_000_000:
        raise ValueError("Cover file size invalid")
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 4_000_000:
            raise ValueError("Cover dimensions too large")
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((320,320))
        out = io.BytesIO()
        image.save(out,format="JPEG",quality=78,optimize=True)
        photo = out.getvalue()
    if len(photo)>190_000:
        raise ValueError("Compressed cover exceeds 190KB")
    chat_id = verified_storage_chat(settings)
    boundary = "MasihaCover_" + secrets.token_hex(12)
    front, back = multipart_fields({"chat_id":chat_id,"disable_notification":"true"},boundary)
    # Multipart_fields sets field name 'audio'. Change the binary part
    # only: photo is a distinct Telegram method and never confuses file IDs.
    front = front.replace(b'name="audio"; filename="track.mp3"',b'name="photo"; filename="cover.jpg"')
    front = front.replace(b"Content-Type: audio/mpeg",b"Content-Type: image/jpeg")
    import sales
    conn=(http.client.HTTPConnection("127.0.0.1",8081,timeout=35)
          if sales.telegram_local_enabled()
          else http.client.HTTPSConnection("api.telegram.org",timeout=35,context=ssl.create_default_context()))
    if not sales.telegram_local_enabled():
        conn._create_connection = sales._telegram_connect
    try:
        storage_send_pace()
        conn.putrequest("POST","/bot"+settings["token"]+"/sendPhoto",skip_accept_encoding=True)
        conn.putheader("Content-Type","multipart/form-data; boundary="+boundary)
        conn.putheader("Content-Length",str(len(front)+len(photo)+len(back)))
        conn.endheaders()
        conn.send(front)
        conn.send(photo)
        conn.send(back)
        response=conn.getresponse()
        result=json.loads(response.read(40_000))
        if response.status!=200 or not result.get("ok"):
            raise RuntimeError("Telegram cover HTTP "+str(response.status))
        message=result.get("result") or {}
        photos=message.get("photo") or []
        if not photos or not photos[-1].get("file_id"):
            raise RuntimeError("Telegram cover has no photo file_id")
        delete_storage_message(settings,chat_id,message.get("message_id"))
        return photos[-1]["file_id"]
    finally:
        conn.close()


def validate_cover_url(url):
    p=urlsplit(url)
    if p.scheme!="https" or p.username or p.password or p.port not in (None,443) or p.fragment:
        raise ValueError("Unsafe cover URL")
    host=(p.hostname or "").lower()
    if host!="rozmusic.com" and not host.endswith(".rozmusic.com"):
        raise ValueError("Untrusted cover host")
    if not p.path.lower().endswith((".jpg",".jpeg",".png",".webp")):
        raise ValueError("Invalid cover extension")
    return url