#!/usr/bin/python3
"""Optional OpenAI-compatible media workers. No media generation unless
explicitly enabled by admin and the existing API credential is configured.

Successful delivery consumes exactly one subscription credit OR points.
A provider failure never consumes the user's own credits. 8s video cap.
"""
import base64,json,os,re,secrets,tempfile,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
import ai_commerce as commerce
import gamification as loyalty

API="https://api.openai.com/v1"
MAX_MEDIA=18_000_000
VIDEO_SECONDS=8
def setup(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS ai_media_jobs(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 uid INTEGER NOT NULL,update_id INTEGER NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('image','video')),
 prompt TEXT NOT NULL,file_id TEXT NOT NULL DEFAULT '',
 input_seconds INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'queued',
 provider_id TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL
 );
 CREATE INDEX IF NOT EXISTS ai_media_jobs_pending ON ai_media_jobs(status,id);
 CREATE TABLE IF NOT EXISTS ai_media_drafts(
 uid INTEGER PRIMARY KEY,kind TEXT NOT NULL,file_id TEXT NOT NULL,
 seconds INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL
 );
 """)

def _cfg(c):
 import sales
 config=sales.settings(c)
 if not config.get("ai_media_enabled"):
  raise ValueError("ساخت تصویر و ویدیو هنوز از سوی مدیر فعال نشده است")
 return config

def _balance(c,uid,kind):
 import sales
 cfg=sales.settings(c)
 if uid in cfg["admins"]:return True
 cost=int(cfg.get("ai_cost_"+kind,0))
 return bool(commerce.available(c,uid,kind)) or cost==0 or loyalty.balance(c,uid)>=cost

def menu(c,uid,kind):
 from sales import enqueue
 if kind not in ("image","video"):raise ValueError("نوع تولید نامعتبر است")
 config=_cfg(c)
 import ai_models
 chosen=ai_models.resolved(c,uid,kind)
 ai_models.provider_key(c,chosen["provider"])
 if not _balance(c,uid,kind):
  raise ValueError("اعتبار کافی برای درخواست "+("تصویر" if kind=="image" else "ویدیو")+" ندارید")
 c.execute("UPDATE users SET state=? WHERE id=?",("ai:"+kind+":input",uid))
 cost=config.get("ai_cost_"+kind,0)
 enqueue(c,uid,
  ("🖼 ساخت یا ویرایش تصویر: متن درخواست یا عکس همراه کپشن را ارسال کنید." if kind=="image" else
   "🎬 ساخت ویدیوی کوتاه از متن: دستور ویدیوی موردنظر را ارسال کنید. مدل‌های فعلی ویرایش ویدیو را ندارند.")+
  "\nهزینه هر نتیجه موفق: "+str(cost)+" امتیاز، یا یک سهمیه از اشتراک داخل ربات."+
  "\nسهمیه/امتیاز فقط پس از ارسال نتیجه موفق کسر می‌شود.",
  [[("↩️ هوش مصنوعی","fun:ai")]])

def _telegram_media(message,kind):
 if kind=="image":
  photos=message.get("photo")
  if photos:return photos[-1].get("file_id",""),0
 if kind=="video":
  film=message.get("video") or message.get("document")
  if film:
   seconds=film.get("duration",0)
   size=film.get("file_size",0)
   if type(seconds) is not int or not 1<=seconds<=10:
    raise ValueError("ویدیوی ورودی باید حداکثر ۱۰ ثانیه باشد")
   if type(size) is not int or not 0<size<=MAX_MEDIA:
    raise ValueError("حجم ویدیوی ورودی باید کمتر از ۱۸ مگابایت باشد")
   return film.get("file_id",""),seconds
 return "",0

def input_message(c,uid,state,message,update_id):
 from sales import enqueue,now
 kind=state.split(":")[1]
 if kind not in ("image","video"):raise ValueError("مرحله مدیا نامعتبر است")
 _cfg(c)
 if not _balance(c,uid,kind):raise ValueError("سهمیه یا امتیاز کافی نیست")
 if state.endswith(":input"):
  fid,seconds=_telegram_media(message,kind)
  prompt=(message.get("caption") or message.get("text") or "").strip()
  if fid:
   import ai_models
   chosen=ai_models.resolved(c,uid,kind)
   if kind=="video":
    raise ValueError("مدل فعلی فقط تولید ویدیو از متن را پشتیبانی می‌کند؛ ویرایش ویدیو هنوز فعال نیست")
   if chosen["provider"]=="cloudflare":
    raise ValueError("مدل FLUX فقط ساخت تصویر را انجام می‌دهد؛ برای ویرایش مدل OpenAI را انتخاب کنید")
  if fid and not prompt:
   c.execute("""INSERT INTO ai_media_drafts(uid,kind,file_id,seconds,created)
    VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET kind=excluded.kind,
    file_id=excluded.file_id,seconds=excluded.seconds,created=excluded.created""",
    (uid,kind,fid,seconds,now()))
   c.execute("UPDATE users SET state=? WHERE id=?",("ai:"+kind+":caption",uid))
   return enqueue(c,uid,"📝 لطفاً دستور ویرایش تصویر یا ویدیو را با یک پیام متنی ارسال کنید.")
 else:
  record=c.execute("SELECT * FROM ai_media_drafts WHERE uid=? AND kind=?",(uid,kind)).fetchone()
  if not record or now()-record["created"]>900:raise ValueError("فایل قبلی منقضی شد؛ دوباره ارسال کنید")
  fid,seconds=record["file_id"],record["seconds"]
  prompt=(message.get("text") or "").strip()
 if not 4<=len(prompt)<=1200:raise ValueError("توضیح تولید یا ویرایش باید بین ۴ تا ۱۲۰۰ حرف باشد")
 c.execute("""INSERT OR IGNORE INTO ai_media_jobs
   (uid,update_id,kind,prompt,file_id,input_seconds,created,updated)
   VALUES(?,?,?,?,?,?,?,?)""",(uid,update_id,kind,prompt,fid,seconds,now(),now()))
 c.execute("DELETE FROM ai_media_drafts WHERE uid=?",(uid,))
 c.execute("UPDATE users SET state='' WHERE id=?",(uid,))
 return enqueue(c,uid,"✅ درخواست "+("تصویر" if kind=="image" else "ویدیو")+
   " دریافت شد و در صف پردازش قرار گرفت. نتیجه پس از آماده‌شدن به همین گفتگو ارسال می‌شود.",
   [[("🤖 منوی هوش مصنوعی","fun:ai")]],key="ai-media-queued:"+str(update_id))

def _opener():
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):raise ValueError("تغییر مسیر API ممنوع است")
 return urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

def _api(key,path,data=None,multipart=None,limit=3000000,timeout=75):
 url=API+path
 headers={"Authorization":"Bearer "+key}
 if multipart is not None:
  boundary="Masiha"+secrets.token_hex(12);chunks=[]
  for name,value in multipart.items():
   chunks.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+name+"\""+
     ("; filename=\"source.bin\"" if isinstance(value,tuple) else "")+"\r\n"+
     ("Content-Type: "+value[0]+"\r\n" if isinstance(value,tuple) else "")+
     "\r\n").encode())
   chunks.append(value[1] if isinstance(value,tuple) else str(value).encode())
   chunks.append(b"\r\n")
  chunks.append(("--"+boundary+"--\r\n").encode())
  body=b"".join(chunks);headers["Content-Type"]="multipart/form-data; boundary="+boundary
 elif data is not None:
  body=json.dumps(data).encode();headers["Content-Type"]="application/json"
 else:body=None
 req=urllib.request.Request(url,data=body,headers=headers)
 try:
  with _opener().open(req,timeout=timeout) as response:
   blob=response.read(limit+1)
   if len(blob)>limit:raise ValueError("پاسخ رسانه از سقف مجاز بزرگ‌تر است")
   return blob
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as exc:
  raise RuntimeError("ارتباط با سرویس رسانه برقرار نشد") from None

def _grab(c,file_id,kind):
 import audio_fingerprint
 from pathlib import Path
 with tempfile.TemporaryDirectory(prefix="masiha-ai-input-") as directory:
  path=Path(directory)/("reference.mp4" if kind=="video" else "reference.jpg")
  audio_fingerprint.download_telegram(c,file_id,path,MAX_MEDIA)
  return path.read_bytes()

def _provider_create(c,row,key,cfg):
 import ai_models
 model=ai_models.resolved(c,row["uid"],row["kind"])
 vendor=model["provider"]
 prompt=row["prompt"]
 if row["kind"]=="image":
  if vendor=="cloudflare":
   if row["file_id"]:raise ValueError("این مدل فقط ساخت تصویر را انجام می‌دهد؛ ویرایش تصویر به مدل OpenAI نیاز دارد")
   return "image",ai_models.cloudflare_image(c,model,prompt)
  if vendor!="openai":raise ValueError("این مدل توانایی تولید تصویر ندارد")
  key=ai_models.provider_key(c,"openai")
  if row["file_id"]:
   data=_grab(c,row["file_id"],"image")
   result=_api(key,"/images/edits",multipart={"model":model["model_id"],
    "prompt":prompt,"image":("image/jpeg",data)},limit=24_000_000,timeout=115)
  else:
   result=_api(key,"/images/generations",data={"model":model["model_id"],
    "prompt":prompt,"size":"1024x1024"},limit=24_000_000,timeout=115)
  payload=json.loads(result)
  encoded=payload["data"][0].get("b64_json")
  if not isinstance(encoded,str):raise ValueError("پاسخ تصویر فاقد فایل است")
  raw=base64.b64decode(encoded,validate=True)
  if len(raw)>MAX_MEDIA:raise ValueError("تصویر خروجی بیش از حد مجاز است")
  return "image",raw
 if vendor!="fal":raise ValueError("برای ویدیو یک ارائه‌دهنده فعال انتخاب کنید")
 if row["file_id"]:raise ValueError("ویدیوی ورودی در مدل انتخابی پشتیبانی نمی‌شود")
 import ai_fal_video
 request=ai_fal_video.submit(model["model_id"],ai_models.provider_key(c,"fal"),prompt)
 return "video",json.dumps({"model_key":model["model_key"],"request_id":request})

def _send_file(c,uid,kind,raw):
 import sales
 cfg=sales.settings(c)
 token=cfg.get("token")
 if not token:raise ValueError("ربات توکن ندارد")
 boundary="Masiha"+secrets.token_hex(12)
 method="sendPhoto" if kind=="image" else "sendVideo"
 name="photo" if kind=="image" else "video"
 filename=("generated.jpg" if raw[:2]==b"\xff\xd8" else "generated.png") if kind=="image" else "generated.mp4"
 content_type=("image/jpeg" if raw[:2]==b"\xff\xd8" else "image/png") if kind=="image" else "video/mp4"
 data=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n"+str(uid)+
 "\r\n--"+boundary+"\r\nContent-Disposition: form-data; name=\""+name+
 "\"; filename=\""+filename+"\"\r\nContent-Type: "+content_type+"\r\n\r\n").encode() + raw + (
 "\r\n--"+boundary+"--\r\n").encode()
 req=urllib.request.Request(sales.telegram_endpoint({"token":token},method),data=data,
 headers={"Content-Type":"multipart/form-data; boundary="+boundary},method="POST")
 with sales.telegram_open(req,timeout=45) as response:
  payload=json.loads(response.read(6000))
  if not payload.get("ok"):raise ValueError("ارسال فایل نتیجه به تلگرام ناموفق بود")

def _after_delivery(c,row):
 import sales
 cfg=sales.settings(c)
 ref="ai:media:"+str(row["update_id"])
 kind=row["kind"]
 if row["uid"] not in cfg["admins"]:
  if commerce.available(c,row["uid"],kind):
   if not commerce.consume(c,row["uid"],kind,ref):
    raise ValueError("سهمیه مصرف کافی نیست")
   cost=0
  else:
   cost=int(cfg["ai_cost_"+kind])
   if cost:loyalty.redeem(c,row["uid"],cost,ref,sales.now())
 else:cost=0
 c.execute("INSERT OR IGNORE INTO ai_usage(user_id,reference,model,points,created) VALUES(?,?,?,?,?)",
 (row["uid"],ref,__import__("ai_models").resolved(c,row["uid"],kind)["model_id"],cost,sales.now()))
 c.execute("UPDATE ai_media_jobs SET status='done',updated=? WHERE id=?",(sales.now(),row["id"]))
 sales.enqueue(c,row["uid"],"✅ "+("تصویر" if kind=="image" else "ویدیو")+
  " ارسال شد. "+("سهمیه اشتراک استفاده شد." if not cost else "امتیاز کسرشده: "+str(cost)),
  key="ai-media-completed:"+str(row["update_id"]))

def run_once():
 import sales
 with sales.conn() as c:
  item=c.execute("""SELECT * FROM ai_media_jobs
    WHERE status IN ('queued','polling') ORDER BY id LIMIT 1""").fetchone()
  if not item:return "idle"
  cfg=sales.settings(c)
  if not cfg.get("ai_media_enabled"):return "media_disabled"
  try:
   if item["status"]=="queued":
    if not _balance(c,item["uid"],item["kind"]):
     raise ValueError("امتیاز یا سهمیه کافی نیست")
    type_,value=_provider_create(c,item,cfg.get("ai_api_key",""),cfg)
    if type_=="video":
     c.execute("UPDATE ai_media_jobs SET status='polling',provider_id=?,updated=? WHERE id=?",
       (value,sales.now(),item["id"]))
     return "video_submitted"
    _send_file(c,item["uid"],"image",value)
    _after_delivery(c,item)
    return "image_done"
   import ai_models,ai_fal_video
   info=json.loads(item["provider_id"])
   model=ai_models.select(c,info["model_key"],"video")
   if model["provider"]!="fal":raise ValueError("مدل ویدیوی صف نامعتبر است")
   status,video_url=ai_fal_video.status(model["model_id"],
     ai_models.provider_key(c,"fal"),info["request_id"])
   if status=="pending":return "video_processing"
   raw=ai_fal_video.download_video(video_url,MAX_MEDIA)
   _send_file(c,item["uid"],"video",raw)
   _after_delivery(c,item)
   return "video_done"
  except Exception:
   c.execute("UPDATE ai_media_jobs SET status='failed',updated=? WHERE id=?",(sales.now(),item["id"]))
   sales.enqueue(c,item["uid"],"⚠️ پردازش درخواست رسانه موفق نبود؛ سهمیه اشتراک یا امتیازی از شما کسر نشد.",
      key="ai-media-error:"+str(item["update_id"]))
   return "media_failed"

if __name__=="__main__":
 print(run_once(),flush=True)