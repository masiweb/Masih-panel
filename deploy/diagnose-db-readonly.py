#!/usr/bin/env python3
"""Read-only diagnosis for the 'attempt to write a readonly database' error.

Never creates, edits, re-owns, chmods or vacuums database files. No row data,
passwords, tokens or application configuration values are read or displayed.
"""
from __future__ import annotations
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import subprocess

DATABASES = [
    Path("/etc/x-ui/x-ui.db"),
    Path("/etc/masiha-vpn-extension/users.db"),
    Path("/etc/masiha-vpn-extension/sales/sales.db"),
]

def report_path(path: Path, label: str):
    try:
        st=path.lstat()
    except FileNotFoundError:
        print(f"{label}=MISSING")
        return False
    kind="symlink" if stat.S_ISLNK(st.st_mode) else "regular" if stat.S_ISREG(st.st_mode) else "directory" if stat.S_ISDIR(st.st_mode) else "other"
    try:
        owner=pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner=str(st.st_uid)
    print(f"{label} type={kind} owner={owner} uid={st.st_uid} gid={st.st_gid} perms={stat.S_IMODE(st.st_mode):04o}")
    return True

def service_uid(unit):
    p=subprocess.run(["systemctl","show",unit,"-p","User","--value"],
                     capture_output=True,text=True,timeout=8)
    user=p.stdout.strip()
    if not user: user="root"
    try:
        uid=pwd.getpwnam(user).pw_uid
    except KeyError:
        print(f"SERVICE_{unit}=UNKNOWN_USER")
        return None
    print(f"SERVICE_{unit}=user:{user} uid:{uid}")
    return uid

def accessible_for_uid(path,uid,write=False):
    try:
        st=path.stat()
    except OSError:
        return False
    mode=stat.S_IMODE(st.st_mode)
    bit_owner=stat.S_IWUSR if write else stat.S_IRUSR
    bit_group=stat.S_IWGRP if write else stat.S_IRGRP
    bit_other=stat.S_IWOTH if write else stat.S_IROTH
    if uid==0:
        return True
    if uid==st.st_uid:
        return bool(mode&bit_owner)
    try:
        user=pwd.getpwuid(uid)
        groups=os.getgrouplist(user.pw_name,user.pw_gid)
    except (KeyError,OSError):
        groups=[]
    return bool(mode & (bit_group if st.st_gid in groups else bit_other))

def main():
    print("DIAGNOSTIC_ONLY=YES")
    print("CHANGES_MADE=NONE")
    uids=[u for u in (
        service_uid("x-ui.service"),
        service_uid("masiha-vpn-sidecar.service"),
        service_uid("masiha-vpn-extension.service"),
    ) if u is not None]
    for db in DATABASES:
        print("DATABASE_PATH="+str(db))
        if not report_path(db,"FILE"):
            continue
        report_path(db.parent,"DIRECTORY")
        for ext in ("-wal","-shm","-journal"):
            path=Path(str(db)+ext)
            if path.exists() or path.is_symlink():
                report_path(path,"SQLITE_SIDECAR_"+ext)
        try:
            st=os.statvfs(db.parent)
            print("FILESYSTEM_READONLY="+("YES" if st.f_flag & getattr(os,"ST_RDONLY",1) else "NO"))
        except OSError as exc:
            print("FILESYSTEM_READONLY=UNKNOWN_"+type(exc).__name__)
        for uid in sorted(set(uids)):
            print(f"PROCESS_UID_{uid}_FILE_WRITE_MODE="+str(accessible_for_uid(db,uid,write=True)))
            print(f"PROCESS_UID_{uid}_DIR_WRITE_MODE="+str(accessible_for_uid(db.parent,uid,write=True)))
        try:
            conn=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=4)
            try:
                schema=conn.execute("PRAGMA schema_version").fetchone()
                print("SQLITE_READONLY_OPEN="+("OK" if schema else "NO_RESULT"))
            finally:
                conn.close()
        except (OSError,sqlite3.Error) as exc:
            print("SQLITE_READONLY_OPEN=ERROR_"+type(exc).__name__)
        print("---")

if __name__=="__main__":
    main()
