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

def ai_start(c,uid):
 cfg=settings(c)
 if not cfg["ai_enabled"] and uid not in cfg["admins"]:
  return enqueue(c,uid,"🤖 هوش مصنوعی در حال آماده‌سازی است. پس از ثبت تنظیمات API توسط مدیر فعال می‌شود.",
       [[("⭐ امتیازات من","fun:points"),("↩️ سرگرمی","fun")]])
 cost=cfg["ai_cost"]
 if uid not in cfg["admins"] and loyalty.balance(c,uid)<cost:
  return enqueue(c,uid,"برای استفاده از هوش مصنوعی "+str(cost)+" امتیاز لازم دارید. از خرید یا دعوت دوستان امتیاز بگیرید.",
       [[("⭐ امتیازات من","fun:points"),("دعوت دوستان","referral"),("↩️ سرگرمی","fun")]])
 c.execute("UPDATE users SET state='ai:chat' WHERE id=?",(uid,))
 enqueue(c,uid,"🤖 پرسش خود را در پیام بعدی ارسال کنید.\n"+
     ("✅ مدیر: بدون کسر امتیاز." if uid in cfg["admins"] else
      "هزینه هر پاسخ موفق: "+str(cost)+" امتیاز.")+
     " برای خروج «/start» را بفرستید.",[[("↩️ انصراف و بازگشت","fun")]])

def ai_answer(c,uid,question,update_id):
 cfg=settings(c)
 if not cfg["ai_enabled"] and uid not in cfg["admins"]:raise ValueError("هوش مصنوعی موقتاً غیرفعال است")
 cost=integer(cfg["ai_cost"],1,10000)
 if uid not in cfg["admins"] and loyalty.balance(c,uid)<cost:
  raise ValueError("امتیاز کافی برای استفاده از هوش مصنوعی ندارید")
 # Requests use an update-specific idempotency reference. The AI answer is
 # queued in the same SQLite transaction as the point debit.
 ref="ai:"+str(update_id)
 if c.execute("SELECT 1 FROM ai_usage WHERE reference=?",(ref,)).fetchone():
  return enqueue(c,uid,"پاسخ این پیام قبلاً ثبت شده است.",[[("↩️ سرگرمی","fun")]])
 answer=loyalty.query_ai(cfg,question)
 charge=0 if uid in cfg["admins"] else cost
 if charge:loyalty.redeem(c,uid,charge,ref,now())
 c.execute("INSERT INTO ai_usage(user_id,reference,model,points,created) VALUES(?,?,?,?,?)",
          (uid,ref,cfg["ai_model"],charge,now()))
 audit(c,"ai_usage",str(uid)+":points:"+str(cost))
 enqueue(c,uid,"🤖 "+answer+"\n\n⭐ امتیاز باقی‌مانده: "+str(loyalty.balance(c,uid)),
         [[("سؤال بعدی","fun:ai"),("↩️ سرگرمی","fun")]],key="ai-response:"+str(update_id))

def callback(c,uid,cmd):
 if cmd=="fun":return menu(c,uid)
 if cmd=="fun:points":return points(c,uid)
 if cmd=="fun:ai":return ai_start(c,uid)
 if cmd=="fun:music":return media_library.kind_menu(c,uid,"music")
 if cmd=="fun:film":return media_library.kind_menu(c,uid,"film")
 if cmd=="fun:game":
  return enqueue(c,uid,"🎲 بخش بازی\nچالش روزانه، سؤال چهارگزینه‌ای و جدول رتبه‌بندی در برنامه توسعه‌اند.",
    [[("⭐ امتیازها","fun:points"),("↩️ سرگرمی","fun")]])
 raise ValueError("گزینه سرگرمی ناشناخته است")
