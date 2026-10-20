#!/usr/bin/python3
"""Safe, hot-reloading Persian text overrides. Never change Telegram callback_data."""
import json
import os
import re
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("bot_texts.fa.json")
OVERRIDE_FILE = Path(os.environ.get(
    "MASIHA_BOT_TEXTS_FILE",
    "/etc/masiha-vpn-extension/sales/bot_texts.fa.json"
))
_CACHE = {"signature": None, "mapping": {}}

def _text_map():
    sources = (DEFAULT_FILE, OVERRIDE_FILE)
    signatures = []
    for path in sources:
        try:
            st = path.stat()
            signatures.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            signatures.append((str(path), None, None))
    signature = tuple(signatures)
    if signature == _CACHE["signature"]:
        return _CACHE["mapping"]
    combined = {"messages": {}, "buttons": {}, "phrases": {}}
    for path in sources:
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            for section in combined:
                items = doc.get(section, {})
                if not isinstance(items, dict):
                    continue
                for key, value in items.items():
                    if (isinstance(key, str) and isinstance(value, str)
                        and 1 <= len(key) <= 2000 and len(value) <= 4000):
                        combined[section][key] = value
        except (OSError, ValueError, UnicodeError):
            # A malformed optional override must not interrupt bot service.
            continue
    _CACHE.update(signature=signature, mapping=combined)
    return combined

def _render(value, kind):
    if not isinstance(value, str):
        return value
    mapping = _text_map()
    exact = mapping["buttons" if kind == "button" else "messages"]
    if value in exact:
        return exact[value]
    changed = {k: v for k, v in mapping["phrases"].items() if k != v}
    if not changed:
        return value
    # Longest-first, single pass: replacements cannot recursively change IDs.
    regex = re.compile("|".join(re.escape(k) for k in sorted(changed, key=len, reverse=True)))
    return regex.sub(lambda m: changed[m.group()], value)

def rewrite_payload(payload):
    """Copy only display text/captions/button labels, never callback IDs or URLs."""
    p = dict(payload)
    for field in ("text", "caption"):
        if isinstance(p.get(field), str):
            p[field] = _render(p[field], "message")[:4096 if field == "text" else 1024]
    markup = p.get("reply_markup")
    if isinstance(markup, dict):
        markup = dict(markup)
        for container in ("inline_keyboard", "keyboard"):
            if isinstance(markup.get(container), list):
                markup[container] = [
                    [{**b, "text": _render(b.get("text"), "button")} if isinstance(b, dict)
                      else b for b in row] for row in markup[container] if isinstance(row, list)
                ]
        p["reply_markup"] = markup
    return p