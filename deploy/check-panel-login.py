#!/usr/bin/env python3
"""Validate the actual saved first-run admin session without printing secrets.

Run only on a provisioned host. Fails closed if the panel does not accept the
root-only initial-login credentials. No attempts to fetch other users' secrets.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time
import urllib.error
import urllib.request

CREDS = Path("/root/masiha-vpn-initial-login.env")
DB = "/etc/x-ui/x-ui.db"


def read_config():
    st = CREDS.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise RuntimeError("Saved login credentials must be root-owned mode 0600")
    cfg = {}
    for line in CREDS.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        cfg[k] = shlex.split(v)[0] if shlex.split(v) else ""
    for k in ("XUI_USERNAME", "XUI_PASSWORD", "XUI_PANEL_PORT", "XUI_WEB_BASE_PATH"):
        if not cfg.get(k):
            raise RuntimeError("Required saved login field is missing: " + k)
    return cfg


def verify():
    cfg = read_config()
    with sqlite3.connect("file:" + DB + "?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT username FROM users ORDER BY id LIMIT 1").fetchone()
    if not row or row[0] != cfg["XUI_USERNAME"]:
        raise RuntimeError("Username saved in installation file differs from x-ui database")

    # Do not pass a password on the command line or send credentials to the
    # public internet: use loopback and validate the actual login endpoint.
    base = "http://127.0.0.1:" + cfg["XUI_PANEL_PORT"] + "/" + cfg["XUI_WEB_BASE_PATH"].strip("/")
    # Bypass HTTP_PROXY/HTTPS_PROXY for the loopback-only login probe.
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    host = json.loads(Path("/etc/masiha-vpn-extension/config.json").read_text())["domain"]
    def request(path, payload=None, token=None):
        headers = {"X-Requested-With": "XMLHttpRequest", "Host": host}
        if token:
            headers["X-CSRF-Token"] = token
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers)
        with client.open(req, timeout=12) as r:
            return json.load(r)
    # systemctl start returns when the systemd process has been launched,
    # NOT when x-ui has finished loading its SQLite database and HTTP server.
    # Probe only the unauthenticated CSRF endpoint until ready. A login failure
    # must never be retried (failed logins trigger a localhost lockout).
    wait_seconds = int(os.environ.get("MASIHA_PANEL_READY_TIMEOUT", "90"))
    if not 1 <= wait_seconds <= 300:
        raise RuntimeError("Panel readiness timeout must be 1-300 seconds")
    deadline = time.monotonic() + wait_seconds
    print(f"Waiting for local x-ui login endpoint (up to {wait_seconds}s)", flush=True)
    last_progress = time.monotonic()
    while True:
        try:
            challenge = request("/csrf-token")
            if challenge.get("success") and challenge.get("obj"):
                break
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            pass  # service is starting, restarting or not yet accepting HTTP
        if time.monotonic() >= deadline:
            raise RuntimeError("Local x-ui CSRF endpoint did not become ready in time")
        if time.monotonic() - last_progress >= 10:
            print("Still waiting for local x-ui HTTP readiness...", flush=True)
            last_progress = time.monotonic()
        time.sleep(min(2, max(0.1, deadline - time.monotonic())))
    print("Native x-ui login endpoint: READY", flush=True)
    # Exactly ONE login attempt; a wrong password should produce a hard error.
    result = request("/login", {
        "username": cfg["XUI_USERNAME"],
        "password": cfg["XUI_PASSWORD"],
    }, challenge["obj"])
    if not result.get("success"):
        raise RuntimeError("Saved administrator credentials rejected by live panel")
    csrf = request("/panel/csrf-token")
    if not csrf.get("success") or not csrf.get("obj"):
        raise RuntimeError("Authenticated panel session did not provide CSRF token")
    print("PASS: saved first-run administrator credentials authenticate to native x-ui")


if __name__ == "__main__":
    try:
        verify()
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError, sqlite3.Error) as exc:
        # Controlled exceptions above contain NO passwords, cookies or tokens.
        # Never print raw HTTP responses or settings in diagnostic messages.
        message = str(exc) if isinstance(exc, RuntimeError) else "local HTTP or database check failed"
        print("ERROR: initial admin login verification failed: " + message, file=sys.stderr)
        sys.exit(1)
