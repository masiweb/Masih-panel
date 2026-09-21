#!/usr/bin/env python3
import asyncio, os
import httpx
TOKEN=os.environ["TELEGRAM_BOT_TOKEN"]
BASE=f"https://api.telegram.org/bot{TOKEN}"
async def main():
    offset=0
    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                data=(await client.get(f"{BASE}/getUpdates",params={"timeout":30,"offset":offset})).json()
                for update in data.get("result",[]):
                    offset=update["update_id"]+1
                    msg=update.get("message",{})
                    if msg.get("text") in ("/start","شروع"):
                        await client.post(f"{BASE}/sendMessage",json={"chat_id":msg["chat"]["id"],"text":"به Masiha VPN خوش آمدید. بخش خرید، تمدید و مدیریت سرویس در حال توسعه است."})
            except Exception:
                await asyncio.sleep(5)
if __name__=="__main__": asyncio.run(main())
