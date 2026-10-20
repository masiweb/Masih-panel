import os, tempfile, json, io, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
os.environ["MASIHA_CONFIG"]="/tmp/masiha-nonexistent-config.json"
_testdir=tempfile.TemporaryDirectory()
os.environ["MASIHA_SALES_DIR"]=_testdir.name
import sales, sales_worker, bot_texts

class MenuTextTests(unittest.TestCase):
 def setUp(self):
  self.c=sales.conn()
  self.c.execute("DELETE FROM outbox")
  self.c.execute("DELETE FROM users")
  self.c.execute("INSERT INTO users(id,name,created) VALUES(123,'test',0)")
  self.c.commit()
 def tearDown(self):
  self.c.close()
 def callback(self,cmd="buynew",message=None):
  msg=message or {"chat":{"id":123,"type":"private"},"text":"old menu",
       "message_id":42,"reply_markup":{"inline_keyboard":[[{"text":"Old","callback_data":"buynew"}]]}}
  return {"update_id":501,"callback_query":{"data":cmd,"from":{"id":123,"first_name":"T"},"message":msg}}
 def test_callback_edits_menu_not_stack(self):
  sales_worker.handle(self.c,self.callback())
  rows=self.c.execute("SELECT method,payload FROM outbox ORDER BY id").fetchall()
  self.assertEqual([r["method"] for r in rows],["editMessageText"])
  p=json.loads(rows[0]["payload"])
  self.assertEqual(p["message_id"],42)
  self.assertTrue(p["reply_markup"]["inline_keyboard"])
 def test_regular_start_still_sends(self):
  sales_worker.handle(self.c,{"update_id":502,"message":{"from":{"id":123},"chat":{"id":123,"type":"private"},"text":"/start"}})
  self.assertEqual(self.c.execute("SELECT method FROM outbox").fetchone()[0],"sendMessage")
 def test_media_keyboard_removed_but_photo_preserved(self):
  msg={"chat":{"id":123,"type":"private"},"photo":[{"file_id":"abc"}],"message_id":43,
       "reply_markup":{"inline_keyboard":[[{"text":"Old","callback_data":"buynew"}]]}}
  sales_worker.handle(self.c,self.callback(message=msg))
  self.assertEqual([r[0] for r in self.c.execute("SELECT method FROM outbox ORDER BY id")],
                   ["editMessageReplyMarkup","sendMessage"])
 def test_failed_callback_does_not_leak_context(self):
  with self.assertRaises(ValueError):
   sales_worker.handle(self.c,self.callback(cmd="approve:123"))
  sales.enqueue(self.c,123,"normal")
  self.assertEqual(self.c.execute("SELECT method FROM outbox").fetchone()[0],"sendMessage")
 def test_templates_preserve_ids_and_hot_reload(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/"edit.json"
   with patch.object(bot_texts,"OVERRIDE_FILE",path):
    path.write_text(json.dumps({"buttons":{"🛒 خرید جدید":"🛍 خرید اشتراک"},
          "phrases":{"موجودی: ":"اعتبار: "}}),encoding="utf-8")
    one=bot_texts.rewrite_payload({"chat_id":123,"text":"موجودی: 100 تومان",
       "reply_markup":{"inline_keyboard":[[{"text":"🛒 خرید جدید","callback_data":"buynew"}]]}})
    self.assertEqual(one["text"],"اعتبار: 100 تومان")
    self.assertEqual(one["reply_markup"]["inline_keyboard"][0][0]["text"],"🛍 خرید اشتراک")
    self.assertEqual(one["reply_markup"]["inline_keyboard"][0][0]["callback_data"],"buynew")
    path.write_text('{"buttons":{"🛒 خرید جدید":"جدیدتر"}}',encoding="utf-8")
    self.assertEqual(bot_texts.rewrite_payload({"reply_markup":{"inline_keyboard":[[
       {"text":"🛒 خرید جدید","callback_data":"buynew"}]]}})["reply_markup"]["inline_keyboard"][0][0]["text"],"جدیدتر")
 def test_bad_json_does_not_break_delivery(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/"broken.json"
   path.write_text('not json',encoding="utf-8")
   with patch.object(bot_texts,"OVERRIDE_FILE",path):
    self.assertEqual(bot_texts.rewrite_payload({"text":"hello"})["text"],"hello")
 def test_telegram_edit_400_falls_back(self):
  row={"method":"editMessageText"}
  calls=[]
  def fake(settings,method,payload):
   calls.append((method,payload))
   if method=="editMessageText":
    raise urllib.error.HTTPError("https://api.telegram.org",400,"Bad Request",{},
           io.BytesIO(b'{"description":"Bad Request: message to edit not found"}'))
   return True
  with patch.object(sales_worker,"tg",side_effect=fake):
   self.assertTrue(sales_worker.deliver_outbox_message({},row,
        {"chat_id":123,"message_id":42,"text":"NEW","reply_markup":{"inline_keyboard":[]}}))
  self.assertEqual([m for m,_ in calls],["editMessageText","editMessageReplyMarkup","sendMessage"])
  self.assertNotIn("message_id",calls[-1][1])

if __name__=="__main__":
 unittest.main()