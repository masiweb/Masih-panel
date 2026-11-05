#!/usr/bin/env python3
"""Safe housekeeping of Telegram Local Bot API media caches for the Masiha bot.

The authoritative content references are Telegram file_ids persisted in
SQLite. Only local copies named file_N in known media folders are pruned.
Never touch Telegram's binlog, db, temp files, or source backups.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
import stat
import subprocess
import time

ROOT = Path("/var/lib/telegram-bot-api")
DB = Path("/etc/masiha-vpn-extension/sales/sales.db")
LOCK = Path("/run/lock/masiha-media-cache-maintenance.lock")
FILM_TEMP = Path("/var/tmp/masiha-films")
AGE = {"music": 6 * 3600, "videos": 1 * 3600}
MATCH = re.compile(r"file_[0-9]+\Z")


def opened_inodes():
    opened = set()
    for pid in os.listdir("/proc"):
        if not pid.isdecimal():
            continue
        d = "/proc/" + pid + "/fd"
        try:
            entries = os.listdir(d)
        except (OSError, PermissionError):
            continue
        for fd in entries:
            try:
                st = os.stat(d + "/" + fd)
                if stat.S_ISREG(st.st_mode):
                    opened.add((st.st_dev, st.st_ino))
            except OSError:
                continue
    return opened


def run(apply):
    if not DB.is_file() or not ROOT.is_dir():
        raise RuntimeError("Bot database or Local Bot API data directory missing")
    con = sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT v FROM settings WHERE k='token'").fetchone()
        if not row:
            raise RuntimeError("Bot settings missing")
        token = json.loads(row[0])
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]{6,12}:[A-Za-z0-9_-]{20,}", token):
            raise RuntimeError("Invalid bot identity; refusing maintenance")
        totals = {
            "music": con.execute("""SELECT count(*) FROM media_items
                WHERE kind='music' AND status='published'
                AND length(file_id)>=10""").fetchone()[0],
            "videos": con.execute("""SELECT count(*) FROM film_variants
                WHERE uploaded>0 AND stream_ready=1
                AND length(file_id)>=10""").fetchone()[0]
        }
    finally:
        con.close()
    bot_path = ROOT / token
    if bot_path.is_symlink() or not bot_path.is_dir():
        raise RuntimeError("Expected bot cache folder missing")
    owner = pwd.getpwnam("tgbotapi").pw_uid
    opened = opened_inodes()
    now = time.time()
    import shutil
    low_disk = shutil.disk_usage(ROOT).free < 4 * 1024**3
    stats = {}
    for kind, age in AGE.items():
        # Emergency reclaim: old cached copies cannot consume the VPN reserve.
        if low_disk:
            age = min(age, 15 * 60)
        if not totals[kind]:
            stats[kind] = {"files": 0, "bytes": 0, "skipped_no_receipts": True}
            continue
        folder = bot_path / kind
        if folder.is_symlink() or not folder.is_dir():
            continue
        count, nbytes = 0, 0
        for entry in folder.iterdir():
            if not MATCH.fullmatch(entry.name):
                continue
            try:
                st = entry.lstat()
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != owner or
                    st.st_nlink != 1 or st.st_mtime > now - age or
                    (st.st_dev, st.st_ino) in opened):
                    continue
                if apply:
                    # Recheck immediately before unlink.
                    st2 = entry.lstat()
                    if (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
                        continue
                    entry.unlink()
                count += 1
                nbytes += st.st_size
            except (FileNotFoundError, PermissionError):
                continue
        stats[kind] = {"files": count, "bytes": nbytes}
    # Scratch directory of killed film-import processes. Live process uses
    # tempfile.TemporaryDirectory and already deletes both copies on exit.
    removed_scratch, scratch_bytes = 0, 0
    if FILM_TEMP.is_dir():
        result = subprocess.run(["systemctl", "is-active", "--quiet", "masiha-film-import.service"])
        if result.returncode != 0:
            for directory in FILM_TEMP.iterdir():
                if (not directory.name.startswith("job-") or directory.is_symlink() or
                    not directory.is_dir()):
                    continue
                try:
                    children = list(directory.iterdir())
                    if not children or any(p.is_symlink() or not p.is_file() or
                                         p.name not in ("source.bin", "stream.mp4") for p in children):
                        continue
                    times = [directory.stat().st_mtime] + [p.stat().st_mtime for p in children]
                    if max(times) > now - 24 * 3600:
                        continue
                    childstats = [p.stat() for p in children]
                    if any((s.st_dev, s.st_ino) in opened for s in childstats):
                        continue
                    if apply:
                        for p in children:
                            p.unlink()
                        directory.rmdir()
                    removed_scratch += 1
                    scratch_bytes += sum(s.st_size for s in childstats)
                except (OSError, PermissionError):
                    continue
    stats["orphaned_film_scratch"] = {"directories": removed_scratch, "bytes": scratch_bytes}
    stats["mode"] = "applied" if apply else "dry_run"
    stats["total_bytes"] = sum(x.get("bytes", 0) for x in stats.values() if isinstance(x, dict))
    print(json.dumps(stats, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with LOCK.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"another_cleanup_running"}')
            return
        run(args.apply)


if __name__ == "__main__":
    main()
