#!/usr/bin/python3
"""Regenerate editable defaults without overwriting existing customizations."""
import ast
import json
import os
import re
from pathlib import Path
from bot_texts import OVERRIDE_FILE
from sales import MESSAGE_DEFAULTS

SOURCE = Path(__file__).resolve().parent
PERSIAN = re.compile(r"[\u0600-\u06ff]")
SKIP = re.compile(r"\b(SELECT|CREATE TABLE|UPDATE|INSERT INTO|DELETE FROM|JOIN|WHERE|PRAGMA)\b",re.I)

def extract():
    labels=set()
    phrases=set()
    for path in SOURCE.glob("*.py"):
        if path.name.startswith("test_") or path.name in ("bot_texts.py","bot_texts_export.py"):
            continue
        try:
            syntax = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError,UnicodeError):
            continue
        for node in ast.walk(syntax):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                s=node.value
                if 1 <= len(s) <= 500 and PERSIAN.search(s) and not SKIP.search(s):
                    phrases.add(s)
            if isinstance(node,(ast.Tuple,ast.List)) and len(node.elts)>=2:
                first=node.elts[0]
                if isinstance(first,ast.Constant) and isinstance(first.value,str):
                    s=first.value
                    if 1 <= len(s) <= 150 and PERSIAN.search(s) and not SKIP.search(s):
                        labels.add(s)
    return labels,phrases

def export(path=OVERRIDE_FILE):
    path=Path(path)
    previous={}
    if path.exists():
        previous=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous,dict):
            raise ValueError("catalog root must be an object")
    labels,phrases=extract()
    doc={"_help":"Edit only values (right side). Keys are original texts. This file hot-reloads. Keep valid UTF-8 JSON; do not edit callback_data.",
         "messages":{k:v for k,v in MESSAGE_DEFAULTS.items()},
         "buttons":{s:s for s in sorted(labels)},
         "phrases":{s:s for s in sorted(phrases) if s not in labels}}
    # message keys in this catalog are ORIGINAL full response texts, not IDs;
    # the panel's keyed message templates remain higher-level editable settings.
    doc["messages"]={v:v for v in MESSAGE_DEFAULTS.values()}
    for section in ("messages","buttons","phrases"):
        if isinstance(previous.get(section),dict):
            doc[section].update(previous[section])
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=path.with_name(path.name+".tmp")
    temp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.chmod(temp,0o600)
    temp.replace(path)
    return {section:len(doc[section]) for section in ("messages","buttons","phrases")}

if __name__=="__main__":
    import sys
    print(json.dumps(export(Path(sys.argv[1]) if len(sys.argv)>1 else OVERRIDE_FILE),ensure_ascii=False))