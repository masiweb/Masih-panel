#!/usr/bin/env python3
"""Isolated tests for live-admin-readiness checking; never contact a real host."""
import importlib.util
import json
import pathlib
import unittest
import urllib.error
from unittest import mock

target = pathlib.Path(__file__).resolve().with_name("check-panel-login.py")
spec = importlib.util.spec_from_file_location("masiha_check_panel_login", target)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class FakeDB:
    def __init__(self, username="admin"):
        self.username = username
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def execute(self, query):
        assert "SELECT username" in query
        return self
    def fetchone(self):
        return (self.username,)


class Response:
    def __init__(self, obj):
        self.obj = obj
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def read(self, *args):
        return json.dumps(self.obj).encode()


class FakeOpener:
    def __init__(self, unready=0, accept=True):
        self.unready = unready
        self.accept = accept
        self.challenge_count = 0
        self.login_count = 0
    def open(self, request, timeout):
        url = request.full_url
        if url.endswith("/panel/csrf-token"):
            return Response({"success": True, "obj": "session-token"})
        if url.endswith("/csrf-token"):
            self.challenge_count += 1
            if self.challenge_count <= self.unready:
                raise urllib.error.URLError("connection refused")
            return Response({"success": True, "obj": "challenge"})
        if url.endswith("/login"):
            self.login_count += 1
            data = json.loads(request.data)
            assert data["username"] == "admin"
            assert data["password"] == "dummy-test-only"
            return Response({"success": self.accept, "obj": ""})
        raise AssertionError("Unknown request: " + url)


class LoginReadinessTests(unittest.TestCase):
    def run_with(self, opener, db_user="admin"):
        config = {"XUI_USERNAME": "admin", "XUI_PASSWORD": "dummy-test-only",
                  "XUI_PANEL_PORT": "62053", "XUI_WEB_BASE_PATH": "/xui/"}
        fake_config_file = mock.Mock()
        fake_config_file.read_text.return_value = '{"domain":"vpn.example.com"}'
        with mock.patch.object(checker, "read_config", return_value=config), \
             mock.patch.object(checker.sqlite3, "connect", return_value=FakeDB(db_user)), \
             mock.patch.object(checker, "Path", return_value=fake_config_file), \
             mock.patch.object(checker.urllib.request, "build_opener", return_value=opener), \
             mock.patch.object(checker.time, "sleep", return_value=None), \
             mock.patch.dict(checker.os.environ, {"MASIHA_PANEL_READY_TIMEOUT": "5"}):
            return checker.verify()

    def test_waits_for_startup_and_authenticates_exactly_once(self):
        opener = FakeOpener(unready=3)
        self.run_with(opener)
        self.assertEqual(opener.challenge_count, 4)
        self.assertEqual(opener.login_count, 1)

    def test_refused_credentials_never_retried_or_locked_out(self):
        opener = FakeOpener(accept=False)
        with self.assertRaisesRegex(RuntimeError, "credentials rejected"):
            self.run_with(opener)
        self.assertEqual(opener.login_count, 1)

    def test_wrong_database_username_fails_before_http(self):
        opener = FakeOpener()
        with self.assertRaisesRegex(RuntimeError, "differs from x-ui database"):
            self.run_with(opener, "upstream-random")
        self.assertEqual(opener.challenge_count, 0)
        self.assertEqual(opener.login_count, 0)


if __name__ == "__main__":
    unittest.main()
