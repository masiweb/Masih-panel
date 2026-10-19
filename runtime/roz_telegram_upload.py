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
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

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
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_mp3_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


AUDIO_HTTP = build_opener(SafeRedirect)


def download_mp3(url, dest):
    validate_mp3_url(url)
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
            magic = f.read(3)
            # ID3 tag or standard MPEG Layer III sync byte.
            if not (magic == b"ID3" or
                    len(magic) >= 2 and magic[0] == 0xff and magic[1] & 0xe0 == 0xe0):
                raise ValueError("Download is not an MP3 audio stream")
    return downloaded


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
    conn = http.client.HTTPSConnection("api.telegram.org", timeout=120,
                                      context=ssl.create_default_context())
    try:
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


def stream_once(url, artist, title, settings, article_url=None):
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
            try:
                import audio_fingerprint
                audio_fingerprint.index_imported_source(article_url,path)
            except (ImportError,OSError,ValueError,RuntimeError,subprocess.SubprocessError):
                pass
        # Delete the *message* silently. file_id remains usable by the same bot,
        # as confirmed by real getFile check before full-archive activation.
        removed = delete_storage_message(settings, chat_id, response["message_id"])
        return response["file_id"], response["file_unique_id"], size, removed
