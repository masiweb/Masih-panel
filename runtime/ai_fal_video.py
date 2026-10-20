#!/usr/bin/env python3
"""Fixed-endpoint fal video backend; video generation is billable, not free."""
import json,re,urllib.request,urllib.error
from urllib.parse import urlsplit
ALLOWED_MODEL_IDS={
 "fal-ai/wan/v2.2-a14b/text-to-video",
 "fal-ai/kling-video/v3/standard/text-to-video",
}
PREFIX="https://queue.fal.run/"
VALID_ID=re.compile(r"[0-9a-fA-F-]{20,80}\Z")
MEDIA_HOSTS={"storage.googleapis.com","v3.fal.media","fal.media","cdn.fal.ai"}

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

def _request(url,key,body=None):
 req=urllib.request.Request(url,headers={"Authorization":"Key "+key,
    "Content-Type":"application/json"},
    data=json.dumps(body).encode() if body is not None else None)
 try:
  with opener.open(req,timeout=35) as resp:
   raw=resp.read(150000)
   return json.loads(raw)
 except (urllib.error.HTTPError,urllib.error.URLError,ValueError,TimeoutError):
  raise ValueError("سرویس ویدیو در دسترس نیست؛ اعتباری از کاربر کسر نشد") from None

def submit(model,key,prompt):
 if model not in ALLOWED_MODEL_IDS:raise ValueError("مدل ویدیو پشتیبانی نمی‌شود")
 if not isinstance(prompt,str) or not 4<=len(prompt)<=1200:raise ValueError("توضیح ویدیو نامعتبر است")
 payload={"prompt":prompt,"duration":"5"} if "kling" in model else {"prompt":prompt}
 info=_request(PREFIX+model,key,payload)
 ident=info.get("request_id")
 if not isinstance(ident,str) or not VALID_ID.fullmatch(ident):
  raise ValueError("شناسه صف پردازش ویدیو نامعتبر است")
 return ident

def status(model,key,ident):
 if model not in ALLOWED_MODEL_IDS or not VALID_ID.fullmatch(ident):
  raise ValueError("شناسه پردازش ویدیو نامعتبر است")
 out=_request(PREFIX+model+"/requests/"+ident+"/status",key)
 value=out.get("status")
 if value in ("IN_QUEUE","IN_PROGRESS","PENDING"):return "pending",None
 if value not in ("COMPLETED",):raise ValueError("تولید ویدیو ناموفق بود")
 result=_request(PREFIX+model+"/requests/"+ident,key)
 url=(result.get("video") or {}).get("url")
 if not isinstance(url,str):raise ValueError("نشانی خروجی ویدیو دریافت نشد")
 return "complete",url

def download_video(url,limit=18_000_000):
 parsed=urlsplit(url)
 if parsed.scheme!="https" or parsed.hostname not in MEDIA_HOSTS or parsed.port not in (None,443):
  raise ValueError("مبدأ فایل ویدیو معتبر نیست")
 try:
  with opener.open(urllib.request.Request(url,headers={"User-Agent":"MasihaBot/1"}),timeout=50) as resp:
   data=resp.read(limit+1)
   if not data or len(data)>limit:raise ValueError("فایل خروجی از سقف مجاز بزرگ‌تر است")
   return data
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
  raise ValueError("دانلود خروجی ویدیو ناموفق بود") from None
