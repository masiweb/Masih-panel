#!/usr/bin/python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bot_texts_admin as a

class TextCatalogAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.base = self.directory/"defaults.json"
        self.custom = self.directory/"custom.json"
        self.base.write_text(json.dumps({
            "messages":{"سلام":"سلام","کاربر {name}":"کاربر {name}"},
            "buttons":{"🛒 خرید":"🛒 خرید"},
            "phrases":{"امتیاز":"امتیاز","https://a.ir بزن":"https://a.ir بزن"}
        }, ensure_ascii=False),encoding="utf-8")
        self.one=patch.object(a,"DEFAULT_FILE",self.base)
        self.two=patch.object(a,"OVERRIDE_FILE",self.custom)
        self.one.start();self.two.start()
    def tearDown(self):
        self.two.stop();self.one.stop();self.tmp.cleanup()
    def test_list_and_search(self):
        p=a.listing({"query":"امتیاز","page":1,"page_size":20})
        self.assertEqual(p["total"],1)
        self.assertEqual(p["rows"][0]["kind"],"phrases")
    def test_save_and_reset(self):
        saved=a.edit({"kind":"buttons","key":"🛒 خرید","expected":"🛒 خرید",
                      "value":"🛍 خرید جدید"})
        self.assertEqual(saved["current"],"🛍 خرید جدید")
        self.assertEqual(a.listing({"changed_only":True})["total"],1)
        saved=a.edit({"kind":"buttons","key":"🛒 خرید","expected":"🛍 خرید جدید",
                      "operation":"reset"})
        self.assertFalse(saved["changed"])
        self.assertEqual(a.listing({"changed_only":True})["total"],0)
    def test_conflict_rollback(self):
        a.edit({"kind":"messages","key":"سلام","expected":"سلام","value":"درود"})
        with self.assertRaisesRegex(ValueError,"شخص دیگری"):
            a.edit({"kind":"messages","key":"سلام","expected":"سلام","value":"خوش آمدید"})
        self.assertEqual(next(r["current"] for r in a.listing({"kind":"messages"})["rows"] if r["original"]=="سلام"),"درود")
    def test_invariant_guards(self):
        for kind,key,new in [("messages","کاربر {name}","کاربر شما"),
                             ("phrases","https://a.ir بزن","اینجا بزن"),
                             ("buttons","🛒 خرید","A"*65)]:
            with self.assertRaises(ValueError):
                a.edit({"kind":kind,"key":key,"expected":key,"value":new})
        self.assertFalse(self.custom.exists())
    def test_malformed_json_preserved(self):
        self.custom.write_text("{invalid json",encoding="utf-8")
        with self.assertRaises(ValueError):
            a.edit({"kind":"messages","key":"سلام","expected":"سلام","value":"درود"})
        self.assertEqual(self.custom.read_text(),"{invalid json")
    def test_filters_and_limits(self):
        with self.assertRaises(ValueError):a.listing({"page_size":100})
        with self.assertRaises(ValueError):a.listing({"query":"a"*121})
        with self.assertRaises(ValueError):a.listing({"kind":"secret"})
        self.assertEqual(a.listing({"kind":"buttons"})["total"],1)
    def test_no_callback_field_persisted(self):
        a.edit({"kind":"buttons","key":"🛒 خرید","expected":"🛒 خرید","value":"سفارش"})
        doc=json.loads(self.custom.read_text())
        self.assertEqual(doc["buttons"],{"🛒 خرید":"سفارش"})
        self.assertNotIn("callback_data",str(doc))

if __name__=="__main__":
 unittest.main()