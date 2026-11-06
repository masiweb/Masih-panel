#!/usr/bin/env python3
"""Portable restore: real SQLite schema regression tests, no changes to live services."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"runtime"))
from portable_migration import build_panel_candidate, build_sales_candidate, portable_file

SOURCE=Path("/opt/masiha-migration-export/db-snapshot-work/panel.db")
CURRENT=Path("/etc/x-ui/x-ui.db")


class PortableMigrationTests(unittest.TestCase):
    def test_whitelist_excludes_server_identity_and_executable_runtime(self):
        unsafe=[
            "/etc/letsencrypt/live/s2.chanelchat.ir/fullchain.pem",
            "/etc/letsencrypt/archive/s2.chanelchat.ir/privkey1.pem",
            "/etc/nginx/sites-available/masiha-vpn",
            "/usr/local/x-ui/x-ui",
            "/etc/masiha-vpn-extension/config.json",
            "/etc/x-ui/x-ui.db",
            "/etc/masiha-vpn-extension/sales/sales.db",
            "/etc/masiha-vpn-extension/sales/telegram_local.enabled",
            "/etc/systemd/system/x-ui.service",
            "/root/.ssh/authorized_keys",
            "/etc/masiha-vpn-extension/sales/worker.lock",
        ]
        self.assertTrue(all(not portable_file(p) for p in unsafe))
        self.assertTrue(portable_file("/etc/ocserv/masiha.passwd"))
        self.assertTrue(portable_file("/etc/openvpn/masiha/pki/ca.crt"))
        self.assertTrue(portable_file("/var/lib/masiha-audio-fp/fingerprints.pklz"))

    def test_sales_urls_rebuilt_without_erasing_orders(self):
        with tempfile.TemporaryDirectory(prefix="masiha-portable-sales-test-") as tmp:
            source=Path(tmp)/"source.db"
            output=Path(tmp)/"rebound.db"
            with sqlite3.connect(source) as c:
                c.execute("CREATE TABLE services(id TEXT PRIMARY KEY,protocol TEXT,data TEXT)")
                c.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY,amount INTEGER)")
                c.execute("INSERT INTO orders VALUES(1,123456)")
                d={"subId":"portable-test-token","links":["vless://old@s2.chanelchat.ir:8444"],
                   "subscription":["https://s2.chanelchat.ir/old/portable-test-token"]}
                c.execute("INSERT INTO services VALUES(?,?,?)",("one","xray",json.dumps(d)))
            count=build_sales_candidate(source,output,"https://s3.chanelchat.ir/sub/")
            self.assertEqual(count,1)
            with sqlite3.connect(output) as c:
                self.assertEqual(c.execute("select amount from orders where id=1").fetchone()[0],123456)
                data=json.loads(c.execute("select data from services where id='one'").fetchone()[0])
                self.assertEqual(data["links"],[])
                self.assertEqual(data["subscription"],["https://s3.chanelchat.ir/sub/portable-test-token"])

    @unittest.skipUnless(SOURCE.is_file() and CURRENT.is_file(),
                         "Live fixture snapshots available only on S2")
    def test_real_old_to_new_domain_with_destination_native_inbounds(self):
        with tempfile.TemporaryDirectory(prefix="masiha-portable-qa-") as tmp:
            old=Path(tmp)/"old.db"
            new=Path(tmp)/"new.db"
            candidate=Path(tmp)/"merged.db"
            with sqlite3.connect(f"file:{SOURCE}?mode=ro",uri=True) as src:
                with sqlite3.connect(str(old)) as out:src.backup(out)
            with sqlite3.connect(f"file:{CURRENT}?mode=ro",uri=True) as src:
                with sqlite3.connect(str(new)) as out:src.backup(out)
            with sqlite3.connect(new) as db:
                db.execute("UPDATE settings SET value='s3.chanelchat.ir' WHERE key='subDomain'")
                db.execute("UPDATE settings SET value='https://s3.chanelchat.ir/sub/' WHERE key='subURI'")
                columns={r[1] for r in db.execute("pragma table_info(clients)")}
                if "client_host_rule_ids" not in columns:
                    db.execute("ALTER TABLE clients ADD COLUMN client_host_rule_ids TEXT DEFAULT '[]'")
                if "wg_keep_alive_range" not in columns:
                    db.execute("ALTER TABLE clients ADD COLUMN wg_keep_alive_range TEXT DEFAULT ''")
                # The target native Trojan route is 8446, not 18443 on S2.
                db.execute("UPDATE inbounds SET port=8446 WHERE id=4")
                for row in db.execute("SELECT id,stream_settings FROM inbounds WHERE node_id IS NULL").fetchall():
                    js=json.loads(row[1])
                    if js.get("security")=="tls":
                        tls=js.get("tlsSettings",{})
                        tls["serverName"]="s3.chanelchat.ir"
                        tls["certificates"]=[{"certificateFile":"/etc/letsencrypt/live/s3.chanelchat.ir/fullchain.pem",
                                               "keyFile":"/etc/letsencrypt/live/s3.chanelchat.ir/privkey.pem"}]
                        db.execute("UPDATE inbounds SET stream_settings=? WHERE id=?",(json.dumps(js),row[0]))
                db.commit()
            r=build_panel_candidate(old,new,candidate,"s2.chanelchat.ir","s3.chanelchat.ir")
            self.assertEqual(r["source_clients"],87)
            self.assertEqual(r["source_inbounds"],6)
            self.assertTrue(r["legacy_remote_nodes_disabled"])
            self.assertIn("2",r["inbound_port_changes"])
            with sqlite3.connect(candidate) as db:
                db.row_factory=sqlite3.Row
                self.assertEqual(db.execute("pragma integrity_check").fetchone()[0],"ok")
                self.assertEqual(db.execute("select count(*) from clients").fetchone()[0],87)
                self.assertEqual(db.execute("select count(*) from inbounds").fetchone()[0],12)
                self.assertEqual(db.execute("select count(*) from nodes where enable=1").fetchone()[0],0)
                self.assertEqual(db.execute("select count(*) from client_traffics").fetchone()[0],87)
                self.assertGreater(db.execute("select count(*) from client_inbounds").fetchone()[0],507)
                self.assertEqual(db.execute("select value from settings where key='subDomain'").fetchone()[0],
                                 "s3.chanelchat.ir")
                self.assertEqual(db.execute("select value from settings where key='subURI'").fetchone()[0],
                                 "https://s3.chanelchat.ir/sub/")
                self.assertEqual(db.execute("select count(*) from users").fetchone()[0],1)
                for row in db.execute("select stream_settings from inbounds where node_id is null"):
                    settings=json.loads(row[0])
                    if settings.get("security")=="tls" and settings.get("tlsSettings"):
                        self.assertNotEqual(settings["tlsSettings"].get("serverName"),"s2.chanelchat.ir")
                self.assertTrue(db.execute("select count(*) from clients where client_host_rule_ids is null").fetchone()[0]==0)


if __name__=="__main__":
    unittest.main()
