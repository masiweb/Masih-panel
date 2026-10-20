#!/usr/bin/python3
"""Entertainment, loyalty display, and opt-in point-funded AI conversations."""
import json
import secrets
from sales import enqueue, settings, integer, now, audit
import gamification as loyalty
import media_library

def menu(c,uid):
 enqueue(c,uid,"🎮 سرگرمی و باشگاه امتیازهای مسیحا\nبا خرید اشتراک و دعوت دوستان امتیاز بگیرید.",
 [
  [("🤖 هوش مصنوعی","fun:ai"),("🎵 موزیک","fun:music")],
  [("🎬 فیلم","fun:film"),("🎲 بازی","fun:game")],
  [("⭐ امتیازهای من","fun:points")],
  [("🏠 منوی اصلی","home")]
 ])

def points(c,uid):
 cfg=settings(c)
 earned=loyalty.balance(c,uid)
 count=c.execute("SELECT COUNT(*) FROM loyalty_points WHERE user_id=? AND kind='referral'",(uid,)).fetchone()[0]
 earned_buy=c.execute("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE user_id=? AND kind='purchase'",(uid,)).fetchone()[0]
 enqueue(c,uid,"⭐ موجودی امتیاز شما: "+str(earned)+
   "\nامتیاز خرید: "+str(earned_buy)+
   "\nدعوت‌های موفق: "+str(count)+
   "\nبه‌ازای هر "+str(cfg["points_per_toman"])+" تومان خرید: "+str(cfg["points_per_purchase_unit"])+" امتیاز"+
   "\nهر دعوت موفق: "+str(cfg["points_per_referral"])+" امتیاز"+
   "\nهر درخواست هوش مصنوعی: "+str(cfg["ai_cost"])+" امتیاز",
   [[("دعوت دوستان","referral"),("هوش مصنوعی","fun:ai")],
    [("↩️ سرگرمی","fun"),("🏠 منوی اصلی","home")]])

def ai_menu(c,uid):
 cfg=settings(c)
 if not cfg["ai_enabled"] and uid not in cfg["admins"]:
  return enqueue(c,uid,"🤖 سرویس هوش مصنوعی هنوز فعال نشده است.",
     [[("🛒 خرید اشتراک داخل ربات","ais:list:bot")]])
 import ai_commerce
 ent=c.execute("""SELECT COALESCE(SUM(remaining_text),0),COALESCE(SUM(remaining_image),0),
  COALESCE(SUM(remaining_video),0) FROM ai_sale_subscriptions
  WHERE user_id=? AND expires>?""",(uid,now())).fetchone()
 text=("🤖 هوش مصنوعی داخل ربات\n"+
   "سهمیه فعال: متن "+str(ent[0] or 0)+" | تصویر "+str(ent[1] or 0)+
   " | ویدیو "+str(ent[2] or 0)+"\n"+
   "تعرفه هر درخواست موفق (بدون اشتراک): متن "+str(cfg.get("ai_cost_text",cfg["ai_cost"]))+
   "، تصویر "+str(cfg["ai_cost_image"])+"، ویدیو "+str(cfg["ai_cost_video"])+" امتیاز")
 buttons=[[("💬 گفتگو متنی","fun:ai:text")],
   [("🖼 ساخت / ویرایش تصویر","fun:ai:image")],
   [("🎬 ساخت / ویرایش ویدیو ≤۱۰ثانیه","fun:ai:video")],
   [("🛒 خرید اشتراک داخل ربات","ais:list:bot")],
   [("↩️ سرگرمی","fun")]]
 return enqueue(c,uid,text,buttons)

def ai_start(c,uid):
 cfg=settings(c)
 if not cfg["ai_enabled"] and uid not in cfg["admins"]:
  return enqueue(c,uid,"🤖 هوش مصنوعی در حال آماده‌سازی است. پس از ثبت تنظیمات API توسط مدیر فعال می‌شود.",
       [[("⭐ امتیازات من","fun:points"),("↩️ سرگرمی","fun")]])
 import ai_commerce
 cost=cfg.get("ai_cost_text",cfg["ai_cost"])
 has_sub=bool(ai_commerce.available(c,uid,"text"))
 if uid not in cfg["admins"] and not has_sub and loyalty.balance(c,uid)<cost:
  return enqueue(c,uid,"برای استفاده از هوش مصنوعی "+str(cost)+" امتیاز لازم دارید. از خرید یا دعوت دوستان امتیاز بگیرید.",
       [[("⭐ امتیازات من","fun:points"),("دعوت دوستان","referral"),("↩️ سرگرمی","fun")]])
 c.execute("UPDATE users SET state='ai:chat' WHERE id=?",(uid,))
 import ai_conversation
 count=c.execute("SELECT COUNT(*) FROM ai_chat_history WHERE user_id=?",(uid,)).fetchone()[0]
 enqueue(c,uid,"🤖 پرسش خود را در پیام بعدی ارسال کنید.\n"
     + ("🧠 گفتگوهای قبلی شما در ادامه پاسخ‌ها لحاظ می‌شود. پیام‌های ذخیره‌شده: "+str(count)+"\n" if count else
        "🧠 از این سؤال به بعد سابقه مکالمه شما حفظ می‌شود.\n") +

     ("✅ مدیر: بدون کسر امتیاز." if uid in cfg["admins"] else
      "✅ از سهمیه اشتراک داخل ربات استفاده می‌شود." if has_sub else
      "هزینه هر پاسخ موفق: "+str(cost)+" امتیاز.")+
     " برای خروج «/start» را بفرستید.",[[("↩️ انصراف و بازگشت","fun")]])

def ai_answer(c,uid,question,update_id,long_choice=None):
 cfg=settings(c)
 if not cfg["ai_enabled"] and uid not in cfg["admins"]:raise ValueError("هوش مصنوعی موقتاً غیرفعال است")
 import ai_commerce
 cost=integer(cfg.get("ai_cost_text",cfg["ai_cost"]),0,1000000000)
 sub=bool(ai_commerce.available(c,uid,"text"))
 if uid not in cfg["admins"] and not sub and loyalty.balance(c,uid)<cost:
  raise ValueError("امتیاز کافی برای استفاده از هوش مصنوعی ندارید")
 import ai_conversation
 question=(question or "").strip()
 if not question or len(question)>1600:
  raise ValueError("سؤال باید بین ۱ تا ۱۶۰۰ کاراکتر باشد")
 # Requests use an update-specific idempotency reference. Duplicated
 # callback/update delivery cannot charge again or create another offer.
 ref="ai:"+str(update_id)
 if c.execute("SELECT 1 FROM ai_usage WHERE reference=?",(ref,)).fetchone():
  return enqueue(c,uid,"✅ پاسخ این پیام قبلاً ثبت شده است.",
     [[("💬 ادامه گفتگو","fun:ai:text")]])
 # Don't call the provider (or debit credit) before the customer consents
 # to the longer, file-based format.
 if long_choice is None and ai_conversation.is_long(question):
  return ai_conversation.offer(c,uid,question,update_id)
 history=ai_conversation.previous(c,uid)
 import ai_models
 expanded=bool(long_choice)
 if sub or cfg.get("ai_default_text_model"):
  chosen=ai_models.resolved(c,uid,"text")
  answer=ai_models.text_response(c,chosen,question,history=history,expanded=expanded)
  label=chosen["model_id"]
 else:
  answer=loyalty.query_ai(cfg,question,history=history,expanded=expanded)
  label=cfg["ai_model"]
 # Build both files before charging points/quota. Generation errors must
 # not charge the customer or remove the pending confirmation.
 if expanded:
  ai_conversation.queue_files(c,uid,answer,ref)
 charge=0 if uid in cfg["admins"] else (0 if sub else cost)
 if uid not in cfg["admins"] and sub:
  if not ai_commerce.consume(c,uid,"text",ref):
   raise ValueError("سهمیه اشتراک کافی نیست")
 elif charge:loyalty.redeem(c,uid,charge,ref,now())
 c.execute("INSERT INTO ai_usage(user_id,reference,model,points,created) VALUES(?,?,?,?,?)",
          (uid,ref,label,charge,now()))
 audit(c,"ai_usage",str(uid)+":points:"+str(cost))
 ai_conversation.record(c,uid,question,answer,label,ref)
 if long_choice is not None:
  c.execute("DELETE FROM ai_long_requests WHERE user_id=? AND update_id=?",(uid,update_id))
 if expanded:
  enqueue(c,uid,"✅ پاسخ کامل آماده شد و در دو فایل PDF و TXT ارسال می‌شود.\n"
   "⭐ امتیاز باقی‌مانده: "+str(loyalty.balance(c,uid)),
   [[("💬 ادامه گفتگو","fun:ai:text"),("🗑 پاک‌کردن سابقه","fun:ai:clear")]],
   key="ai-response:"+str(update_id),quick_nav=False)
 else:
  enqueue(c,uid,"🤖 "+answer+"\n\n⭐ امتیاز باقی‌مانده: "+str(loyalty.balance(c,uid)),
   [[("💬 ادامه گفتگو","fun:ai:text"),("🗑 پاک‌کردن سابقه","fun:ai:clear"),
     ("↩️ سرگرمی","fun")]],key="ai-response:"+str(update_id))

def callback(c,uid,cmd):
 if cmd=="fun":return menu(c,uid)
 if cmd=="fun:points":return points(c,uid)
 if cmd=="fun:ai":return ai_menu(c,uid)
 if cmd=="fun:ai:text":return ai_start(c,uid)
 if cmd=="fun:ai:clear":
  import ai_conversation
  return ai_conversation.clear(c,uid)
 if cmd in ("fun:ai:image","fun:ai:video"):
  import ai_media
  return ai_media.menu(c,uid,cmd.rsplit(":",1)[1])
 if cmd=="fun:music":return media_library.kind_menu(c,uid,"music")
 if cmd=="fun:film":return media_library.kind_menu(c,uid,"film")
 if cmd=="fun:game":
  return enqueue(c,uid,"🎲 بخش بازی\nچالش روزانه، سؤال چهارگزینه‌ای و جدول رتبه‌بندی در برنامه توسعه‌اند.",
    [[("⭐ امتیازها","fun:points"),("↩️ سرگرمی","fun")]])
 raise ValueError("گزینه سرگرمی ناشناخته است")
