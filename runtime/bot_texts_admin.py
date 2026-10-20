#!/usr/bin/python3
"""Private, admin-authenticated catalog operations for the existing sales API.

Only original Persian source strings are keys. Never exposes/edits
callback_data, Telegram file IDs, payment details or application settings.
"""
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from bot_texts import DEFAULT_FILE, OVERRIDE_FILE

KINDS = ("messages", "buttons", "phrases")
MAX_SIZE = 2_000_000
MAX_EDIT = 4000

def _read():
    result = {"messages": {}, "buttons": {}, "phrases": {}}
    for file in (DEFAULT_FILE, OVERRIDE_FILE):
        try:
            if file.stat().st_size > MAX_SIZE:
                raise ValueError("حجم فایل متون بیش از حد مجاز است")
            payload = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("فایل متن‌های ربات JSON معتبر نیست؛ ابتدا آن را اصلاح کنید") from exc
        if not isinstance(payload, dict):
            raise ValueError("ساختار فایل متن‌های ربات معتبر نیست")
        for kind in KINDS:
            values = payload.get(kind, {})
            if not isinstance(values, dict):
                raise ValueError("دسته‌بندی متن‌ها معتبر نیست")
            result[kind].update({k: v for k, v in values.items()
                if isinstance(k, str) and isinstance(v, str)})
    return result

def _load_override():
    try:
        if OVERRIDE_FILE.stat().st_size > MAX_SIZE:
            raise ValueError("حجم فایل متن‌ها بیش از حد مجاز است")
        doc = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("فایل JSON فعلی نامعتبر است؛ تغییرات ذخیره نشد") from exc
    if not isinstance(doc, dict):
        raise ValueError("ساختار فایل JSON معتبر نیست")
    return doc

@contextmanager
def _lock():
    OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = OVERRIDE_FILE.with_name(".bot_texts.web.lock")
    with lock_path.open("a+b") as f:
        os.chmod(lock_path, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def _write(doc):
    payload = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_SIZE:
        raise ValueError("حجم فایل متن‌ها بیش از سقف مجاز است")
    handle, temporary = tempfile.mkstemp(prefix=".bot-texts-", suffix=".tmp", dir=OVERRIDE_FILE.parent)
    try:
        with os.fdopen(handle, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, OVERRIDE_FILE)
        # Ensure the rename is durable, not just the file bytes.
        dir_fd = os.open(OVERRIDE_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def _validate(kind, key, value, all_texts):
    if kind not in KINDS or not isinstance(key, str) or key not in all_texts[kind]:
        raise ValueError("شناسه متن در فهرست پیش‌فرض‌ها وجود ندارد")
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_EDIT:
        raise ValueError("متن جدید باید غیرخالی و حداکثر ۴۰۰۰ کاراکتر باشد")
    if any(ord(x) < 32 and x not in ("\n", "\r", "\t") for x in value):
        raise ValueError("کاراکتر کنترلی غیرمجاز در متن وجود دارد")
    if kind == "buttons" and ("\n" in value or len(value) > 64):
        raise ValueError("عنوان دکمه باید یک‌خطی و حداکثر ۶۴ کاراکتر باشد")
    # Preserve named variables and actual hyperlinks embedded in the original.
    placeholders = re.findall(r"\{[a-zA-Z_][a-zA-Z_0-9]*\}", key)
    if any(value.count(token) != key.count(token) for token in set(placeholders)):
        raise ValueError("متغیرهای متن اصلی را حذف یا تکرار نکنید")
    links = re.findall(r"https?://[^\s\"'<>{}]+", key)
    if any(url not in value for url in links):
        raise ValueError("پیوندهای اصلی متن باید بدون تغییر باقی بمانند")
    return value

def listing(request):
    all_texts = _read()
    kind = request.get("kind", "all")
    if kind not in ("all",) + KINDS:
        raise ValueError("دسته متن نامعتبر است")
    query = request.get("query", "")
    if not isinstance(query, str) or len(query) > 120:
        raise ValueError("عبارت جستجو نامعتبر است")
    query = query.strip().casefold()
    changed_only = request.get("changed_only", False)
    if type(changed_only) is not bool:
        raise ValueError("فیلتر تغییرات نامعتبر است")
    page = request.get("page", 1)
    size = request.get("page_size", 20)
    if type(page) is not int or page < 1 or page > 1_000_000:
        raise ValueError("شماره صفحه نامعتبر است")
    if type(size) is not int or not 1 <= size <= 50:
        raise ValueError("تعداد در صفحه نامعتبر است")
    categories = KINDS if kind == "all" else (kind,)
    stats = {section: {
        "total": len(all_texts[section]),
        "changed": sum(1 for k, v in all_texts[section].items() if k != v)
    } for section in KINDS}
    rows = []
    for category in categories:
        for original, current in sorted(all_texts[category].items()):
            modified = current != original
            if changed_only and not modified:
                continue
            if query and query not in original.casefold() and query not in current.casefold():
                continue
            rows.append({"kind": category, "original": original,
                         "current": current, "changed": modified})
    start = (page - 1) * size
    return {"rows": rows[start:start+size], "total": len(rows), "page": page,
            "page_size": size, "stats": stats}

def edit(request):
    kind = request.get("kind")
    key = request.get("key")
    operation = request.get("operation", "save")
    if operation not in ("save", "reset"):
        raise ValueError("عملیات ویرایش نامعتبر است")
    expected = request.get("expected")
    if not isinstance(expected, str):
        raise ValueError("متن فعلی برای بررسی همزمانی لازم است")
    with _lock():
        all_texts = _read()
        if kind not in KINDS or not isinstance(key, str) or key not in all_texts[kind]:
            raise ValueError("متن موردنظر یافت نشد")
        actual = all_texts[kind][key]
        if expected != actual:
            raise ValueError("این متن توسط شخص دیگری تغییر کرده است؛ فهرست را تازه‌سازی کنید")
        value = key if operation == "reset" else _validate(kind, key, request.get("value"), all_texts)
        doc = _load_override()
        values = doc.setdefault(kind, {})
        if not isinstance(values, dict):
            raise ValueError("ساختار فایل متن‌ها معتبر نیست")
        # Keep original keys and customizations for other sections untouched.
        values[key] = value
        _write(doc)
    return {"kind": kind, "original": key, "current": value,
            "changed": value != key}

def refresh_catalog():
    from bot_texts_export import export
    with _lock():
        _load_override()  # Reject invalid JSON rather than erasing custom edits.
        return export(OVERRIDE_FILE)