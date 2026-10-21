#!/usr/bin/env python3
"""Licensed AcoustID lookup: short-lived Chromaprint -> metadata -> safe catalog match.

Never persist audio bytes or raw fingerprints and never log the client key.
The 'fingerprint_id' field in audio_fp_index is a local audfprint marker,
NOT a Chromaprint fingerprint accepted by AcoustID.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

CONFIG_FILE=Path("/etc/masiha-vpn-extension/sales/audio_recognition.json")
API_URL="https://api.acoustid.org/v2/lookup"
FPCALC="/usr/bin/fpcalc"
MAX_RESPONSE=160_000

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        return None

OPENER=build_opener(NoRedirect())

def client_key():
    """Disabled until operator supplies a key licensed for intended usage."""
    try:
        doc=json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeError, OSError):
        return ""
    if not isinstance(doc,dict) or doc.get("provider")!="acoustid" or doc.get("enabled") is not True:
        return ""
    key=doc.get("client_key","")
    if isinstance(key,str) and re.fullmatch(r"[A-Za-z0-9_-]{6,128}",key):
        return key
    return ""

def configured():
    return bool(client_key())

def chromaprint(audio_file):
    if not Path(FPCALC).is_file():
        raise RuntimeError("Chromaprint fpcalc is unavailable")
    p=subprocess.run([FPCALC,"-json",str(audio_file)],capture_output=True,text=True,
                     timeout=27,check=False)
    if p.returncode:
        raise RuntimeError("Chromaprint fingerprint extraction failed")
    payload=json.loads(p.stdout[:100000])
    duration=payload.get("duration")
    fp=payload.get("fingerprint")
    if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not 5<=duration<=300:
        raise ValueError("Invalid audio duration for fingerprint")
    if not isinstance(fp,str) or not 12<=len(fp)<=16000 or not re.fullmatch(r"[A-Za-z0-9_-]+",fp):
        raise ValueError("Invalid Chromaprint fingerprint")
    return int(round(duration)),fp

def lookup(fingerprint,duration,key):
    form=urlencode({"client":key,"duration":duration,"fingerprint":fingerprint,
                    "meta":"recordings","format":"json"}).encode("ascii")
    request=Request(API_URL,data=form,headers={
        "Content-Type":"application/x-www-form-urlencoded",
        "User-Agent":"MasihaMusicBot/1.0","Accept":"application/json",
    },method="POST")
    try:
        with OPENER.open(request,timeout=16) as response:
            if response.geturl()!=API_URL:
                raise RuntimeError("Unexpected AcoustID response endpoint")
            raw=response.read(MAX_RESPONSE+1)
    except HTTPError as ex:
        # Do not expose secret keys or the remote request URL in user errors.
        raise RuntimeError("AcoustID HTTP "+str(ex.code)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("AcoustID connection failed") from None
    if len(raw)>MAX_RESPONSE:
        raise RuntimeError("AcoustID response too large")
    try:
        result=json.loads(raw)
    except (ValueError,UnicodeError):
        raise RuntimeError("Invalid AcoustID response") from None
    if not isinstance(result,dict) or result.get("status")!="ok":
        raise RuntimeError("AcoustID lookup rejected request")
    candidates=[]
    for record in result.get("results",[])[:12]:
        if not isinstance(record,dict):continue
        score=record.get("score",0)
        if not isinstance(score,(int,float)) or score<0.75:continue
        for track in record.get("recordings",[])[:5]:
            if not isinstance(track,dict):continue
            title=track.get("title","")
            names=[a.get("name","") for a in track.get("artists",[])[:3] if isinstance(a,dict)]
            artist=" و ".join(n for n in names if isinstance(n,str) and n.strip())
            if not isinstance(title,str) or not 1<len(title)<=250:continue
            candidates.append({"title":title.strip(),"artist":artist[:250],"confidence":float(score)})
    candidates.sort(key=lambda a:a["confidence"],reverse=True)
    unique=set()
    output=[]
    for item in candidates:
        key=(item["title"].casefold(),item["artist"].casefold())
        if key not in unique:
            output.append(item)
            unique.add(key)
    return output[:6]

def identify(audio_file):
    key=client_key()
    if not key:return []
    seconds,fingerprint=chromaprint(audio_file)
    # No audio bytes, Telegram IDs, or other personal data leave for the API.
    return lookup(fingerprint,seconds,key)

def normalize(s):
    if not isinstance(s,str):return ""
    s=s.translate(str.maketrans({"ي":"ی","ك":"ک","ۀ":"ه","ة":"ه","أ":"ا","إ":"ا","آ":"ا","‌":" ","ـ":""}))
    return " ".join(re.findall(r"[^\W_]+",s.casefold(),flags=re.UNICODE))

def similarity(a,b):
    if not a or not b:return 0.0
    if a==b:return 1.0
    return difflib.SequenceMatcher(None,a,b,autojunk=False).ratio()

def match_catalog(c,candidates):
    """Match only published music; require strong agreement on title and artist.

    Reject guesses based on artist alone or fuzzy title alone.
    """
    if not candidates:return None
    rows=c.execute("""SELECT id,title,artist FROM media_items WHERE kind='music'
            AND status='published' AND file_id<>'' ORDER BY id DESC""").fetchall()
    best=None
    best_score=0.0
    for candidate in candidates:
        title=normalize(candidate["title"])
        artist=normalize(candidate.get("artist",""))
        if not title:continue
        for row in rows:
            t=normalize(row["title"])
            a=normalize(row["artist"])
            title_score=similarity(title,t)
            if title_score<0.86:continue
            if artist:
                artist_score=similarity(artist,a)
                if artist_score<0.75:continue
            else:
                # No singer from API: only exact title, and only when unique.
                if title_score<1.0:continue
                artist_score=0.0
                if sum(1 for rr in rows if normalize(rr["title"])==title)>1:continue
            confidence=candidate.get("confidence",0)
            score=title_score*0.70 + (artist_score if artist else 1)*0.20 + confidence*0.10
            if score>best_score:
                best=(row,candidate)
                best_score=score
    return best