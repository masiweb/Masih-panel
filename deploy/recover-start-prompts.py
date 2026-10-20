#!/usr/bin/env python3
"""One-time safe recovery for legacy channel onboarding prompts.
Only retries JOIN instructions for private unverified, unblocked users.
Does not mark arbitrary media, payments, or other messages as deliverable.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"runtime"))
from sales import conn,audit

def recover(c):
 rows=c.execute("""SELECT o.id,o.chat,o.payload FROM outbox o
 JOIN users u ON u.id=o.chat
 WHERE o.done=0 AND COALESCE(u.blocked,0)=0
 AND o.dedupe NOT LIKE 'channel-gate:%'
 AND TRIM(COALESCE(u.phone,''))=''
 ORDER BY o.id""").fetchall()
 latest={}
 for r in rows:
  try:
   p=json.loads(r["payload"])
   buttons=p["reply_markup"]["inline_keyboard"]
   callbacks=[b.get("callback_data") for group in buttons for b in group]
   valid=(r["chat"]==p.get("chat_id") and
     p.get("text","").startswith("برای شروع استفاده از ربات، ابتدا در کانال ") and
     "home" in callbacks and p.get("text","").endswith("عضو شوید. سپس دکمه بررسی مجدد را بزنید."))
  except (TypeError,KeyError,ValueError,IndexError):continue
  if valid:latest.setdefault(r["chat"],[]).append(r["id"])
 recovered=duplicates=0
 for ids in latest.values():
  for stale in ids[:-1]:
   c.execute("UPDATE outbox SET done=2,payload='{}' WHERE id=? AND done=0",(stale,))
   duplicates+=1
  current=ids[-1]
  c.execute("""UPDATE outbox SET dedupe=?,next_try=0
   WHERE id=? AND done=0""",("channel-gate:legacy:"+str(current),current))
  recovered+=1
 if recovered:audit(c,"bot_channel_onboarding_recovered",
                    "delivered_queued="+str(recovered)+";duplicate_skipped="+str(duplicates))
 return recovered,duplicates

if __name__=="__main__":
 with conn() as c:print("CHANNEL_GATE_REPLAY_QUEUED",recover(c))
