#!/usr/bin/env python3
"""Capability-scoped, curated model registry for Masiha AI plans.

Free-tier means conditional provider allowance, NOT unlimited/guaranteed.
No provider/model URL is accepted from customers. Credentials never returned.
"""
import base64,json,re,urllib.error,urllib.request
from urllib.parse import quote
from sales import settings,put,audit,now

# key, provider, vendor model ID, modality, tier, display name
CATALOG=(
 ("oa-luna","openai","gpt-6-luna","text","paid","GPT-6 Luna"),
 ("oa-sol","openai","gpt-6.1-sol","text","paid","GPT-6.1 Sol"),
 ("oa-astra","openai","gpt-6-astra","text","paid","GPT-6 Astra"),
 ("oa-mini","openai","gpt-4.1-mini","text","paid","GPT-4.1 Mini"),
 ("oa-img2","openai","gpt-image-2","image","paid","GPT Image 2"),
 ("oa-flare","openai","gpt-image-2.5-flare","image","paid","GPT Image 2.5 Flare"),
 ("oa-sunburst","openai","gpt-image-2.5-sunburst","image","paid","GPT Image 2.5 Sunburst"),
 ("oa-imgmini","openai","gpt-image-1-mini","image","paid","GPT Image 1 Mini (legacy)"),
 ("gg-flash","google","gemini-3.8-flash","text","free_tier","Gemini 3.8 Flash"),
 ("gg-lite","google","gemini-3.5-flash-lite","text","free_tier","Gemini 3.5 Flash Lite"),
 ("gq-oss","groq","openai/gpt-oss-20b","text","free_tier","GPT-OSS 20B on Groq"),
 ("gq-llama","groq","llama-3.3-70b-versatile","text","free_tier","Llama 3.3 70B on Groq"),
 ("cf-glm","cloudflare","@cf/zai-org/glm-4.7-flash","text","free_tier","GLM 4.7 Flash / Workers AI"),
 ("cf-gemma","cloudflare","@cf/google/gemma-4-26b-a4b-it","text","free_tier","Gemma 4 / Workers AI"),
 ("cf-flux","cloudflare","@cf/black-forest-labs/flux-1-schnell","image","free_tier","FLUX.1 Schnell / Workers AI"),
 # Neither OpenAI Sora API nor an indefinitely free hosted video API is live.
 ("fal-wan","fal","fal-ai/wan/v2.2-a14b/text-to-video","video","paid","Wan 2.2 video / fal"),
 ("fal-kling","fal","fal-ai/kling-video/v3/standard/text-to-video","video","paid","Kling 3 standard / fal"),
)
KINDS=("text","image","video")
PROVIDERS=("openai","google","groq","cloudflare","fal")
KEY_FIELDS={x:"ai_provider_"+x+"_key" for x in PROVIDERS}
NO_REDIRECT=type("NoRedirect",(urllib.request.HTTPRedirectHandler,),{"redirect_request":lambda self,*a,**k:None})
def setup(c):
 c.execute("""CREATE TABLE IF NOT EXISTS ai_provider_models(
 model_key TEXT PRIMARY KEY, provider TEXT NOT NULL, model_id TEXT NOT NULL,
 modality TEXT NOT NULL,tier TEXT NOT NULL,title TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1)""")
 for record in CATALOG:
  c.execute("""INSERT OR IGNORE INTO ai_provider_models(
   model_key,provider,model_id,modality,tier,title) VALUES(?,?,?,?,?,?)""",record)
 cols={r[1] for r in c.execute("PRAGMA table_info(ai_sale_plans)")}
 for op in KINDS:
  if "model_"+op not in cols:
   c.execute("ALTER TABLE ai_sale_plans ADD COLUMN model_"+op+" TEXT NOT NULL DEFAULT ''")
def credentials(c):
 s=settings(c)
 return {p:bool(s.get(KEY_FIELDS[p]) or (p=="openai" and s.get("ai_api_key"))) for p in PROVIDERS}
def catalog(c):
 cred=credentials(c)
 return [dict(r,configured=cred.get(r["provider"],False))
         for r in c.execute("SELECT * FROM ai_provider_models ORDER BY modality,provider,title")]
def configure(c,values):
 if not isinstance(values,dict) or not values:raise ValueError("تنظیمات ارائه‌دهندگان معتبر نیست")
 allowed={*KEY_FIELDS.values(),"ai_provider_cloudflare_account",*[f"ai_default_{op}_model" for op in KINDS]}
 if set(values)-allowed:raise ValueError("فیلد ناشناخته ارائه‌دهنده")
 for key,value in values.items():
  if not isinstance(value,str) or len(value)>500:raise ValueError("مقدار نامعتبر است")
  value=value.strip()
  if key.endswith("_key"):
   if value and not re.fullmatch(r"[A-Za-z0-9_\-.:]{12,500}",value):
    raise ValueError("کلید API معتبر نیست")
   if value:put(c,key,value)
   # Empty form field means KEEP existing key. No retrieval of secrets.
  elif key.startswith("ai_default_"):
   op=key[len("ai_default_"):-len("_model")]
   if value:select(c,value,op)
   put(c,key,value)
  else:
   if not re.fullmatch(r"[a-fA-F0-9]{32}",value):
    raise ValueError("شناسه حساب Cloudflare باید ۳۲ کاراکتر هگز باشد")
   put(c,key,value.lower())
 audit(c,"ai_provider_settings",",".join(sorted(values.keys())))
 return {"configured":credentials(c)}
def select(c,key,kind):
 if kind not in KINDS:raise ValueError("نوع مدل نامعتبر")
 row=c.execute("SELECT * FROM ai_provider_models WHERE model_key=? AND modality=? AND active=1",
  (key,kind)).fetchone()
 if not row:raise ValueError("مدل انتخابی برای این نوع خروجی فعال یا معتبر نیست")
 return dict(row)
def resolved(c,uid,kind):
 """Old plan snapshots keep working using legacy defaults."""
 import ai_commerce
 sub=ai_commerce.available(c,uid,kind)
 if sub:
  row=c.execute("SELECT o.plan_snapshot FROM ai_sale_orders o WHERE o.id=?",
    (sub["order_id"],)).fetchone()
  if row:
   key=json.loads(row[0]).get("model_"+kind,"")
   if key:return select(c,key,kind)
 cfg=settings(c)
 defaults={"text":"oa-mini","image":"oa-imgmini","video":""}
 key=cfg.get("ai_default_"+kind+"_model") or defaults[kind]
 if kind=="text":
  custom=cfg.get("ai_model","")
  found=c.execute("SELECT model_key FROM ai_provider_models WHERE model_id=? AND modality='text' AND provider='openai'",
    (custom,)).fetchone()
  if found:key=found[0]
 if kind=="image":
  found=c.execute("SELECT model_key FROM ai_provider_models WHERE model_id=? AND modality='image' AND provider='openai'",
    (cfg.get("ai_image_model",""),)).fetchone()
  if found:key=found[0]
 if not key:raise ValueError("برای تولید ویدیو باید یک مدل فعال و مجاز انتخاب شود؛ API قدیمی Sora غیرفعال است")
 return select(c,key,kind)
def _api_json(url,key,body,timeout=45,limit=450000):
 headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"}
 req=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method="POST")
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NO_REDIRECT())
 try:
  with opener.open(req,timeout=timeout) as r:
   raw=r.read(limit+1)
   if len(raw)>limit:raise ValueError("پاسخ API بیش از سقف است")
   return json.loads(raw)
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
  raise ValueError("درخواست API ارائه‌دهنده ناموفق بود؛ سهمیه کم نشد") from None

def provider_key(c,provider):
 cfg=settings(c)
 key=cfg.get(KEY_FIELDS[provider]) or (cfg.get("ai_api_key") if provider=="openai" else "")
 if not key:raise ValueError("کلید API ارائه‌دهنده انتخاب‌شده در مدیریت تنظیم نشده است")
 return key
def _cloudflare_base(c):
 account=settings(c).get("ai_provider_cloudflare_account","")
 if not re.fullmatch(r"[a-fA-F0-9]{32}",account):
  raise ValueError("شناسه حساب Cloudflare تنظیم نشده است")
 return "https://api.cloudflare.com/client/v4/accounts/"+account+"/ai"
def text_response(c,model,prompt):
 provider=model["provider"]
 if provider not in ("openai","google","groq","cloudflare"):
  raise ValueError("ارائه‌دهنده برای پاسخ متنی پشتیبانی نمی‌شود")
 endpoints={
  "openai":"https://api.openai.com/v1/chat/completions",
  "google":"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
  "groq":"https://api.groq.com/openai/v1/chat/completions",
 }
 url=(_cloudflare_base(c)+"/v1/chat/completions" if provider=="cloudflare" else endpoints[provider])
 payload={"model":model["model_id"],"messages":[
  {"role":"system","content":"You are a helpful Persian assistant. Never solicit passwords or secrets."},
  {"role":"user","content":prompt[:1600]}]}
 if provider=="openai":payload["max_completion_tokens"]=420
 else:payload["max_tokens"]=420
 answer=_api_json(url,provider_key(c,provider),payload)
 try:response=answer["choices"][0]["message"]["content"]
 except (KeyError,IndexError,TypeError):raise ValueError("پاسخ متنی ارائه‌دهنده نامعتبر است") from None
 if not isinstance(response,str) or not response.strip():raise ValueError("پاسخ متنی نامعتبر است")
 return response[:3500]
def cloudflare_image(c,model,prompt):
 if model["provider"]!="cloudflare":raise ValueError("این مدل Cloudflare نیست")
 endpoint=_cloudflare_base(c)+"/run/"+quote(model["model_id"],safe="/@-_.")
 out=_api_json(endpoint,provider_key(c,"cloudflare"),{"prompt":prompt[:1200],"steps":4},timeout=95,limit=28000000)
 try:payload=out["result"]["image"]
 except (TypeError,KeyError):raise ValueError("تصویر خروجی Cloudflare یافت نشد") from None
 raw=base64.b64decode(payload,validate=True)
 if not raw or len(raw)>18_000_000:raise ValueError("فایل تصویر نامعتبر یا بزرگ است")
 return raw
