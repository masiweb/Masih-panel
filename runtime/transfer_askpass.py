#!/usr/bin/env python3
"""Minimal root-only SSH_ASKPASS helper for ephemeral panel transfers.

Password is never part of argv or an exported environment variable. SSH
receives only the root-readable path to the password stored in /run tmpfs.
"""
import os
from pathlib import Path
import stat
import sys

path = os.environ.get("MASIHA_SSH_PASSWORD_FILE", "")
if not path.startswith("/run/masiha-transfer-") or not path.endswith("/ssh_password"):
    sys.exit(2)
try:
    st=os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
        sys.exit(3)
    with open(path,"r",encoding="utf-8") as secret:
        pw=secret.read()
    if not pw or "\x00" in pw or len(pw)>1024:
        sys.exit(4)
    sys.stdout.write(pw)
except OSError:
    sys.exit(5)
