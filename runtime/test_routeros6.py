#!/usr/bin/env python3
"""Offline RouterOS6 compatibility tests; never touch live keys, listeners, x-ui."""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routeros6

USER = "mc-" + ("a" * 24)
PASSWORD = "safePassword_withURL-characters234"


class RouterOS6(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.dbpath = self.base / "xui.db"
        with sqlite3.connect(self.dbpath) as c:
            c.executescript("""
              CREATE TABLE masiha_accounts(
                client_id INTEGER, generation INTEGER, username TEXT,
                password TEXT, ready INTEGER);
              CREATE TABLE clients(
                id INTEGER, created_at INTEGER, email TEXT, total_gb INTEGER,
                enable INTEGER, expiry_time INTEGER);
              CREATE TABLE client_traffics(
                email TEXT, up INTEGER, down INTEGER, total INTEGER,
                expiry_time INTEGER, enable INTEGER);
            """)
            c.execute("INSERT INTO masiha_accounts VALUES(1,10,?,?,1)", (USER, PASSWORD))
            c.execute("INSERT INTO clients VALUES(1,10,'myuser',10000,1,0)")
            c.execute("INSERT INTO client_traffics VALUES('myuser',0,0,10000,0,1)")
        def connection():
            c = sqlite3.connect(self.dbpath)
            c.row_factory = sqlite3.Row
            return c
        self.credit = Mock()
        self.unified = types.SimpleNamespace(
            connection=connection,
            account_for_username=lambda c, u: c.execute(
                "SELECT * FROM masiha_accounts WHERE username=?", (u,)).fetchone(),
            permitted=lambda x: bool(x["enable"] and x["traffic_enable"]
                                    and x["traffic_up"] + x["traffic_down"] < x["total_gb"]),
            ovpn_session=lambda u, ip, start: u + ":" + ip + ":" + start,
            credit=self.credit,
        )
        self.patch = patch.dict(sys.modules, {"unified": self.unified})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for attr, val in (
            ("BASE", self.base), ("CONFIG", self.base / "server.conf"),
            ("SOCKET", self.base / "mgmt.sock"),
        ):
            pi = patch.object(routeros6, attr, val)
            pi.start()
            self.addCleanup(pi.stop)

    def test_password_and_quota(self):
        password_file = self.base / "login"
        password_file.write_text(USER + "\n" + PASSWORD + "\n")
        self.assertEqual(routeros6.auth(password_file), 0)
        password_file.write_text(USER + "\n" + "incorrectpassword" + "\n")
        self.assertEqual(routeros6.auth(password_file), 1)
        password_file.write_text("bad;injection\n" + PASSWORD)
        self.assertEqual(routeros6.auth(password_file), 1)
        with sqlite3.connect(self.dbpath) as c:
            c.execute("UPDATE client_traffics SET up=10000")
        password_file.write_text(USER + "\n" + PASSWORD)
        self.assertEqual(routeros6.auth(password_file), 1)

    def test_profiles_use_same_password_and_existing_ca(self):
        self.base.joinpath("ca.crt").write_text("PEM_CA_TEST")
        self.base.joinpath("server.conf").write_text("separate listener")
        items = routeros6.profiles({"username": USER, "password": PASSWORD}, "s2.chanelchat.ir")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["filename"], "masiha-ros6-ca.crt")
        self.assertIn("port=1195", items[1]["content"])
        self.assertIn("cipher=aes256", items[1]["content"])
        self.assertIn("auth=sha1", items[1]["content"])
        self.assertIn("verify-server-certificate=yes", items[1]["content"])
        self.assertIn(PASSWORD, items[1]["content"])
        self.assertNotIn("tls-crypt", items[1]["content"])
        self.assertNotIn("protocol=udp", items[1]["content"])
        self.assertEqual(routeros6.profiles({"username": "bad", "password": PASSWORD},
                                            "s2.chanelchat.ir"), [])

    def test_live_accounting_from_status_without_double_creation(self):
        self.base.joinpath("ca.crt").write_text("CA")
        self.base.joinpath("server.conf").write_text("server")
        status = ("\t".join(["CLIENT_LIST", USER, "1.2.3.4:54321",
                              "10.85.0.2", "", "512", "1024", "now",
                              "1700000000", USER, "1", "0"]) + "\n")
        with patch.object(routeros6, "management", return_value=status) as read:
            routeros6.collect()
        self.credit.assert_called_once()
        args = self.credit.call_args.args
        self.assertEqual(args[0], USER)
        self.assertEqual(args[1], "openvpn")
        self.assertEqual(args[-2:], (512, 1024))
        read.assert_called_once_with("status 3")

    def test_connect_rejects_expired_and_disconnect_counts(self):
        env = {"username": USER, "script_type": "client-connect",
               "trusted_ip": "1.2.3.4", "trusted_port": "4543",
               "time_unix": "1700000000"}
        with patch.dict(os.environ, env):
            self.assertEqual(routeros6.hook(), 0)
        self.credit.assert_called_once()
        with sqlite3.connect(self.dbpath) as c:
            c.execute("UPDATE client_traffics SET up=10000")
        self.credit.reset_mock()
        with patch.dict(os.environ, env):
            self.assertEqual(routeros6.hook(), 1)
        self.credit.assert_not_called()
        with patch.dict(os.environ, {**env, "script_type": "client-disconnect",
                                     "bytes_received": "111", "bytes_sent": "222"}):
            self.assertEqual(routeros6.hook(), 0)
        self.credit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
