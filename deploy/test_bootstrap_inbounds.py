#!/usr/bin/env python3
"""Regression tests for fresh-server Xray bootstrap. No services or users modified."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().with_name("bootstrap-inbounds.py")
spec=importlib.util.spec_from_file_location("masiha_bootstrap_inbounds",SCRIPT)
bootstrap=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=str(Path(self.tmp.name)/"panel.db")
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
                INSERT INTO users VALUES (1,'synthetic');
                CREATE TABLE inbounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    remark TEXT, tag TEXT, protocol TEXT, port INTEGER,
                    listen TEXT, enable INTEGER, up INTEGER, down INTEGER,
                    total INTEGER, expiry_time INTEGER, traffic_reset TEXT,
                    traffic_reset_day INTEGER, settings TEXT,
                    stream_settings TEXT, sniffing TEXT
                );
            """)
    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        with patch.object(bootstrap,"reality_keypair",return_value=("test-private-key","test-public-key")), \
             patch.object(bootstrap.socket.socket,"connect_ex",return_value=111):
            return bootstrap.seed(self.db,"vpn.example.com","/unused/xray",require_tls=False)

    def test_six_safe_inbounds_and_no_initial_customers(self):
        self.assertEqual(self.seed(),6)
        with sqlite3.connect(self.db) as db:
            rows=db.execute("SELECT protocol,port,tag,settings,stream_settings FROM inbounds ORDER BY port").fetchall()
        self.assertEqual([r[1] for r in rows],[8443,8444,8445,8446,8447,8448])
        self.assertEqual([r[0] for r in rows],["vless","vless","vmess","trojan","vless","vless"])
        self.assertEqual(len({r[2] for r in rows}),6)
        for protocol,port,tag,settings,stream in rows:
            self.assertEqual(json.loads(settings)["clients"],[])
            config=json.loads(stream)
            if port==8443:
                self.assertEqual(config["security"],"reality")
                self.assertEqual(config["realitySettings"]["privateKey"],"test-private-key")
                self.assertEqual(len(config["realitySettings"]["shortIds"][0]),16)
            else:
                self.assertEqual(config["security"],"tls")
                self.assertIn("vpn.example.com",config["tlsSettings"]["certificates"][0]["certificateFile"])
            if port==8448:
                self.assertEqual(config["network"],"xhttp")
                self.assertTrue(config["xhttpSettings"]["path"].startswith("/xhttp-"))
    def test_idempotent_existing_presets_are_never_rewritten(self):
        self.assertEqual(self.seed(),6)
        with sqlite3.connect(self.db) as db:
            snapshot=list(db.execute("SELECT remark,settings,stream_settings FROM inbounds ORDER BY port"))
        self.assertEqual(self.seed(),0)
        with sqlite3.connect(self.db) as db:
            after=list(db.execute("SELECT remark,settings,stream_settings FROM inbounds ORDER BY port"))
        self.assertEqual(after,snapshot)
    def test_existing_unrelated_inbound_is_preserved(self):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO inbounds(user_id,port,protocol,remark) VALUES(1,443,'vless','existing')")
        self.assertEqual(self.seed(),0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0],1)
    def test_rejects_bad_domain_without_changes(self):
        with self.assertRaises(ValueError):
            bootstrap.seed(self.db,"invalid/domain","/unused",require_tls=False)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
