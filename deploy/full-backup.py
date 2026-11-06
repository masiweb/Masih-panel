#!/usr/bin/env python3
"""Encrypted full backup and guarded restore for Masiha VPN on Ubuntu.

Commands:
  backup  [--output-dir /root/masiha-backups] [--key-file /root/.masiha-backup.key] [--quiesce]
  verify  ARCHIVE [--key-file KEY]
  restore ARCHIVE --confirm-restore [--key-file KEY] [--staging-root DIR] [--allow-domain-mismatch]

Never publish backup files or the encryption key. A live restore replaces secrets.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile

VERSION = 2
DATABASES = {
    "/etc/x-ui/x-ui.db": "panel",
    "/etc/masiha-vpn-extension/users.db": "users",
    "/etc/masiha-vpn-extension/sales/sales.db": "sales",
}
# Deliberately exclude private SSH keys, machine identity and SentinelX agent.
SOURCES = (
    "/etc/x-ui",
    "/etc/masiha-vpn-extension",
    "/etc/openvpn/masiha",
    "/etc/openvpn/masiha-ros6",
    "/etc/openvpn/server/masiha.conf",
    "/etc/openvpn/server/masiha-ros6.conf",
    "/etc/ocserv",
    "/etc/letsencrypt",
    "/etc/nginx/sites-available/masiha-vpn",
    "/etc/nginx/sites-enabled/masiha-vpn",
    "/etc/nginx/nginx.conf",
    "/etc/nginx/conf.d",
    "/etc/ufw",
    "/etc/iptables",
    "/etc/nftables.conf",
    "/etc/systemd/system/x-ui.service",
    "/etc/systemd/system/x-ui.service.d",
    "/etc/systemd/system/masiha-vpn-extension.service",
    "/etc/systemd/system/masiha-vpn-extension.service.d",
    "/etc/sysctl.d/98-masiha-vpn-forward.conf",
    "/etc/sysctl.d/93-masiha-sstp-ipv4-forward.conf",
    "/usr/local/x-ui",
    "/opt/masiha-vpn-extension",
    "/etc/masiha-sstp",
    "/opt/masiha-sstp",
    "/var/lib/masiha-audio-fp",
    "/var/lib/masiha-telegram-build",
    "/var/www/masiha-ui",
    "/etc/systemd/system/masiha-vpn-sidecar.service",
    "/etc/default/telegram-bot-api",
    "/etc/systemd/system/telegram-bot-api.service",
    "/etc/systemd/system/masiha-telegram-api-autostart.path",
    "/etc/systemd/system/masiha-telegram-botapi-build.service",
    "/etc/systemd/system/masiha-telegram-restore-paused.service",
    "/etc/systemd/system/masiha-channel-manager.service",
    "/etc/systemd/system/masiha-channel-manager.timer",
    "/etc/systemd/system/masiha-film-discover.service",
    "/etc/systemd/system/masiha-film-discover.timer",
    "/etc/systemd/system/masiha-rozmusic-import.timer",
    "/usr/lib/systemd/system/masiha-rozmusic-import.timer",
    "/usr/local/bin/telegram-bot-api",
    "/usr/local/sbin/masiha-complete-telegram-bot-api-build",
    "/usr/local/sbin/masiha-telegram-restore-paused",
    "/usr/local/sbin/masiha-ipsec-firewall",
    "/usr/local/sbin/masiha-sstp-network",
    "/var/lib/telegram-bot-api",
    "/etc/ipsec.conf",
    "/etc/ipsec.secrets",
    "/etc/strongswan.conf",
    "/etc/strongswan.d",
    "/etc/xl2tpd",
    "/etc/ppp/chap-secrets",
    "/etc/ppp/options.xl2tpd",
    "/etc/ppp/options.l2tpd",
    "/etc/ppp/options.masiha-l2tp",
    "/etc/ppp/ip-up.d/90-masiha-ipsec",
    "/etc/ppp/ip-down.d/90-masiha-ipsec",
    "/etc/systemd/system/masiha-ipsec-accounting.service",
    "/etc/systemd/system/masiha-ipsec-accounting.timer",
    "/etc/systemd/system/masiha-ipsec-network.service",
    "/etc/systemd/system/masiha-sstp-network.service",
    "/etc/systemd/system/masiha-sstp.service",
    "/etc/systemd/system/masiha-film-import.service",
    "/etc/systemd/system/masiha-film-import.timer",
    "/etc/systemd/system/masiha-film2media-discovery.service",
    "/etc/systemd/system/masiha-film2media-discovery.timer",
    "/etc/systemd/system/masiha-film2media-initial.service",
    "/etc/systemd/system/masiha-rozmusic-import.service",
    "/etc/systemd/system/masiha-rozmusic-import.timer",
    "/etc/systemd/system/masiha-ai-media.service",
    "/etc/systemd/system/masiha-ai-media.timer",
    "/etc/systemd/system/masiha-audio-fingerprint.service",
    "/etc/systemd/system/masiha-audio-fingerprint.timer",
    "/etc/systemd/system/masiha-full-backup.service",
    "/etc/systemd/system/masiha-full-backup.timer",
    "/etc/systemd/system/strongswan-starter.service.d",
    "/etc/systemd/system/xl2tpd.service.d",
    "/etc/systemd/system/ocserv.service.d",
    "/etc/systemd/system/telegram-bot-api.service.d",
    "/etc/systemd/system/openvpn-server@masiha.service.d",
    "/etc/systemd/system/openvpn-server@masiha-ros6.service.d",
    "/etc/systemd/system.control/masiha-telegram-botapi-build.service.d",
    "/etc/accel-ppp",
    "/etc/accel-ppp.conf",
)
EXCLUDED_DIRS = frozenset((
    "backups", "release-staging", "toolchain", ".git", "__pycache__",
    ".venv", ".cache", "node_modules",
))
SENSITIVE_TRANSIENT_SUFFIXES = (".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")
TELEGRAM_DATA_ROOT = "/var/lib/telegram-bot-api"
TELEGRAM_LOCAL_FLAG = "/etc/masiha-vpn-extension/sales/telegram_local.enabled"
TELEGRAM_REQUIRED_FILES = (
    "/etc/default/telegram-bot-api",
    "/etc/systemd/system/telegram-bot-api.service",
    "/etc/systemd/system/masiha-telegram-api-autostart.path",
    "/usr/local/bin/telegram-bot-api",
)
# Ordered dependency-first. start_services/restore_service_states start in this
# order; stop_services stops in reverse so consumers stop before their providers.
SERVICES = (
    "masiha-ipsec-network.service", "masiha-sstp-network.service",
    "strongswan-starter.service", "xl2tpd.service",
    "openvpn-server@masiha.service", "openvpn-server@masiha-ros6.service",
    "ocserv.service", "masiha-sstp.service",
    "x-ui.service", "masiha-vpn-sidecar.service",
    "nginx.service", "certbot.timer",
    "telegram-bot-api.service", "masiha-telegram-api-autostart.path",
    "masiha-ipsec-accounting.service", "masiha-ipsec-accounting.timer",
    "masiha-channel-manager.service", "masiha-channel-manager.timer",
    "masiha-rozmusic-import.service", "masiha-rozmusic-import.timer",
    "masiha-film-import.service", "masiha-film-import.timer",
    "masiha-film2media-discovery.service", "masiha-film2media-discovery.timer",
    "masiha-film2media-initial.service", "masiha-film-discover.service",
    "masiha-film-discover.timer", "masiha-ai-media.service", "masiha-ai-media.timer",
    "masiha-audio-fingerprint.service", "masiha-audio-fingerprint.timer",
    "masiha-vpn-extension.service",
)
# These workers can poll, publish, import, or mutate Telegram media. A target
# restore must not run them while the source server is still live.
CUTOVER_GATED_UNITS = frozenset((
    "masiha-vpn-extension.service",
    "masiha-channel-manager.service", "masiha-channel-manager.timer",
    "masiha-rozmusic-import.service", "masiha-rozmusic-import.timer",
    "masiha-film-import.service", "masiha-film-import.timer",
    "masiha-film2media-discovery.service", "masiha-film2media-discovery.timer",
    "masiha-film2media-initial.service",
    "masiha-film-discover.service", "masiha-film-discover.timer",
    "masiha-ai-media.service", "masiha-ai-media.timer",
    "masiha-audio-fingerprint.service", "masiha-audio-fingerprint.timer",
))
DEFAULT_KEY = Path("/root/.masiha-backup.key")
DEFAULT_OUTPUT = Path("/root/masiha-backups")

# Runtime packages needed by active services on a fresh Ubuntu 24.04 target.
# The installer provisions these before a full archive is restored.
REQUIRED_PACKAGES_BY_UNIT = {
    "nginx.service": ("nginx",),
    "certbot.timer": ("certbot",),
    "openvpn-server@masiha.service": ("openvpn",),
    "openvpn-server@masiha-ros6.service": ("openvpn",),
    "ocserv.service": ("ocserv",),
    "strongswan-starter.service": (
        "strongswan", "strongswan-swanctl", "libcharon-extra-plugins",
        "libcharon-extauth-plugins", "libstrongswan-extra-plugins",
    ),
    "xl2tpd.service": ("xl2tpd", "ppp"),
    "masiha-ipsec-network.service": ("iptables", "iproute2"),
    "masiha-sstp-network.service": ("iptables", "iproute2"),
    "masiha-sstp.service": ("libpcre2-8-0", "ppp"),
    "masiha-audio-fingerprint.timer": (
        "ffmpeg", "libchromaprint-tools", "python3-numpy", "python3-scipy",
        "python3-docopt", "python3-joblib", "python3-psutil",
    ),
}

# A backup must not silently omit the configuration of an active VPN endpoint.
REQUIRED_PATHS_BY_UNIT = {
    "x-ui.service": (
        "/usr/local/x-ui/x-ui", "/usr/local/x-ui/bin/xray-linux-amd64",
        "/etc/x-ui/x-ui.db",
    ),
    "nginx.service": (
        "/etc/nginx/sites-available/masiha-vpn",
        "/etc/nginx/sites-enabled/masiha-vpn",
    ),
    "openvpn-server@masiha.service": (
        "/etc/openvpn/server/masiha.conf",
        "/etc/openvpn/masiha/pki/ca.crt",
        "/etc/openvpn/masiha/pki/issued/masiha-openvpn.crt",
        "/etc/openvpn/masiha/pki/private/masiha-openvpn.key",
        "/etc/openvpn/masiha/tls-crypt.key",
        "/etc/openvpn/masiha/pki/crl.pem",
        "/opt/masiha-vpn-extension/vpn-account-hook.sh",
    ),
    "openvpn-server@masiha-ros6.service": (
        "/etc/openvpn/server/masiha-ros6.conf",
        "/etc/openvpn/masiha-ros6/ca.crt",
        "/etc/openvpn/masiha-ros6/server.crt",
        "/etc/openvpn/masiha-ros6/server.key",
        "/opt/masiha-vpn-extension/ros6-auth-hook.sh",
        "/opt/masiha-vpn-extension/ros6-account-hook.sh",
        "/opt/masiha-vpn-extension/routeros6.py",
    ),
    "ocserv.service": (
        "/etc/ocserv/ocserv.conf", "/etc/ocserv/masiha.passwd",
    ),
    "strongswan-starter.service": (
        "/etc/ipsec.conf", "/etc/ipsec.secrets",
        "/etc/masiha-vpn-extension/ipsec/masiha.secrets",
        "/etc/masiha-vpn-extension/ipsec/chap-secrets",
    ),
    "xl2tpd.service": (
        "/etc/xl2tpd/xl2tpd.conf", "/etc/ppp/options.masiha-l2tp",
        "/etc/ppp/chap-secrets",
        "/etc/ppp/ip-up.d/90-masiha-ipsec",
        "/etc/ppp/ip-down.d/90-masiha-ipsec",
    ),
    "masiha-ipsec-network.service": (
        "/etc/systemd/system/masiha-ipsec-network.service",
        "/usr/local/sbin/masiha-ipsec-firewall",
    ),
    "masiha-sstp-network.service": (
        "/etc/systemd/system/masiha-sstp-network.service",
        "/usr/local/sbin/masiha-sstp-network",
        "/etc/sysctl.d/93-masiha-sstp-ipv4-forward.conf",
    ),
    "masiha-sstp.service": (
        "/etc/systemd/system/masiha-sstp.service",
        "/etc/masiha-sstp/accel-ppp.conf",
        "/etc/masiha-sstp/client-password",
        "/etc/masiha-sstp/chap-secrets",
        "/opt/masiha-sstp/sbin/accel-pppd",
        "/opt/masiha-sstp/lib64/accel-ppp/libtriton.so",
        "/opt/masiha-sstp/lib64/accel-ppp/libsstp.so",
    ),
    "masiha-vpn-extension.service": (
        "/etc/systemd/system/masiha-vpn-extension.service",
        "/etc/masiha-vpn-extension/config.json",
        "/opt/masiha-vpn-extension/worker.py",
    ),
    "masiha-vpn-sidecar.service": (
        "/etc/systemd/system/masiha-vpn-sidecar.service",
        "/var/www/masiha-ui",
    ),
}


def die(message):
    raise RuntimeError(message)


def ensure_root():
    if os.geteuid() != 0:
        die("Run with sudo/root: VPN secrets and database snapshots require root access")


def sha_file(p):
    digest = hashlib.sha256()
    with open(p, "rb") as fd:
        for block in iter(lambda: fd.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_path(path):
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        die("Unsafe destination path")
    pure = PurePosixPath(path)
    if ".." in pure.parts or str(pure) != path:
        die("Unsafe destination path: " + path)
    if not any(path == root or path.startswith(root + "/") for root in SOURCES):
        die("Unexpected path outside VPN backup allowlist: " + path)
    return path


def valid_link(path, target):
    if not target or "\x00" in target:
        die("Invalid symlink target")
    if path == "/etc/nginx/sites-enabled/masiha-vpn":
        if target != "/etc/nginx/sites-available/masiha-vpn":
            die("Unexpected Nginx symlink target")
        return
    if path == "/etc/ppp/chap-secrets" and target == "/etc/masiha-vpn-extension/ipsec/chap-secrets":
        return
    if not path.startswith("/etc/letsencrypt/"):
        die("Unexpected symlink in backup: " + path)
    resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
    if not resolved.startswith("/etc/letsencrypt/"):
        die("TLS symlink escapes Let's Encrypt directory: " + path)


def source_files():
    for source in SOURCES:
        base = Path(source)
        if not base.exists() and not base.is_symlink():
            continue
        if base.is_symlink():
            yield base
        elif base.is_file():
            yield base
        elif base.is_dir():
            for parent, directories, files in os.walk(base, followlinks=False):
                directories[:] = sorted(d for d in directories if d not in EXCLUDED_DIRS)
                for name in sorted(files):
                    path = Path(parent) / name
                    if name.endswith(SENSITIVE_TRANSIENT_SUFFIXES) or name.endswith(".lock"):
                        continue
                    if str(path) in DATABASES:
                        continue
                    if path.is_file() or path.is_symlink():
                        yield path
                for name in directories[:]:
                    path = Path(parent) / name
                    if path.is_symlink():
                        yield path
                        directories.remove(name)


def get_domain():
    p = Path("/etc/masiha-vpn-extension/config.json")
    if not p.is_file():
        die("Missing installed Masiha configuration: " + str(p))
    domain = json.loads(p.read_text(encoding="utf-8")).get("domain")
    if not isinstance(domain, str) or not domain or "/" in domain:
        die("Invalid configured source domain")
    return domain


def run(*args, input_data=None):
    result = subprocess.run(args, input=input_data, capture_output=True, timeout=180)
    if result.returncode:
        die("Command failed (" + args[0] + "): " +
            result.stderr.decode("utf-8", "replace")[-550:])
    return result


def key_file(path, generate=False):
    key = Path(path)
    if not key.exists():
        if not generate:
            die("Decryption key file missing: " + str(key))
        key.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(secrets.token_hex(32) + "\n")
        print("ENCRYPTION_KEY_CREATED", str(key))
    if not key.is_file() or key.is_symlink() or key.stat().st_mode & 0o077:
        die("Encryption key must be a private regular file (chmod 600): " + str(key))
    if not key.read_text(encoding="ascii").strip():
        die("Encryption key file empty")
    return key


def crypt(source, dest, key, decrypt=False):
    command = ["gpg", "--batch", "--yes", "--no-tty", "--pinentry-mode", "loopback",
               "--passphrase-file", str(key)]
    if decrypt:
        command += ["--decrypt", "--output", str(dest), str(source)]
    else:
        command += ["--symmetric", "--cipher-algo", "AES256", "--s2k-digest-algo",
                    "SHA512", "--s2k-count", "65011712", "--output", str(dest), str(source)]
    run(*command)
    dest.chmod(0o600)


MEDIA_TABLES = (
    "media_items", "media_metadata_reward", "media_likes", "media_comments",
    "playlists", "playlist_entries", "playlist_members", "playlist_likes",
    "playlist_comments", "media_reports",
)


def snapshot_databases(stage, skip_media=False):
    db_stats = {}
    for original, label in DATABASES.items():
        if not Path(original).exists():
            die("Required SQLite database missing: " + original)
        dst = stage / "db" / (label + ".db")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect("file:" + original + "?mode=ro", uri=True, timeout=30) as conn:
            with sqlite3.connect(dst, timeout=30) as backup:
                conn.backup(backup, pages=512, sleep=0.05)
                check = backup.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    die("SQLite integrity failure: " + label)
                # A portable SQLite snapshot must be a SINGLE database file,
                # not a WAL-mode database with stray -wal/-shm sidecars.
                backup.execute("PRAGMA journal_mode=DELETE")
                if label == "sales" and skip_media:
                    # Export WITHOUT bot media or playlists. Preserve purchase,
                    # wallet, account, AI and earned loyalty transactions.
                    existing = {r[0] for r in backup.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    backup.execute("PRAGMA foreign_keys=OFF")
                    for name in MEDIA_TABLES:
                        if name in existing:
                            backup.execute('DELETE FROM "' + name + '"')
                    backup.commit()
                    backup.execute("VACUUM")
                table_counts = {}
                for (name,) in backup.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ):
                    # Table names originate from local SQLite schema, not an external string.
                    escaped = name.replace('"', '""')
                    table_counts[name] = backup.execute(
                        'SELECT COUNT(*) FROM "' + escaped + '"').fetchone()[0]
                db_stats[label] = {
                    "path": original, "tables": table_counts,
                    "bytes": dst.stat().st_size, "sha256": sha_file(dst)
                }
        for suffix in ("-wal", "-shm"):
            Path(str(dst) + suffix).unlink(missing_ok=True)
        dst.chmod(0o600)
    return db_stats


def is_telegram_media_path(path):
    value = str(path)
    return value == TELEGRAM_DATA_ROOT or value.startswith(TELEGRAM_DATA_ROOT + "/")


def copy_snapshot(stage, skip_media=False):
    entries = {}
    for src in source_files():
        path = safe_path(str(src))
        if skip_media and is_telegram_media_path(path):
            continue
        meta = src.lstat()
        if stat.S_ISLNK(meta.st_mode):
            link = os.readlink(src)
            valid_link(path, link)
            entries[path] = {"type": "symlink", "target": link}
        elif stat.S_ISREG(meta.st_mode):
            dest = stage / "files" / path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            # A file being actively uploaded must not be captured half-written.
            # Retry a changing file twice, then fail rather than publish a bad archive.
            stable = False
            for attempt in range(3):
                before = src.stat()
                shutil.copyfile(src, dest, follow_symlinks=False)
                after = src.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) == (
                    after.st_size, after.st_mtime_ns, after.st_ino
                ) and dest.stat().st_size == after.st_size:
                    meta = src.lstat()
                    stable = True
                    break
            if not stable:
                die("Source file changed while being backed up: " + path)
            dest.chmod(0o600)
            entries[path] = {
                "type": "file", "size": dest.stat().st_size,
                "sha256": sha_file(dest), "mode": stat.S_IMODE(meta.st_mode),
                "uid": meta.st_uid, "gid": meta.st_gid,
            }
    return entries


def active_services():
    return [s for s in SERVICES if subprocess.run(
        ["systemctl", "is-active", "--quiet", s], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL).returncode == 0]


def service_states():
    states = {}
    for unit in SERVICES:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        enabled = subprocess.run(
            ["systemctl", "is-enabled", unit], capture_output=True, text=True, timeout=12)
        states[unit] = {
            "active": active,
            "enabled": enabled.stdout.strip() or "not-found",
        }
    return states


def package_inventory():
    fmt = "$" + "{Package}\t$" + "{db:Status-Status}\t$" + "{Version}\n"
    result = subprocess.run(["dpkg-query", "-W", "-f=" + fmt],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        die("Unable to read installed Debian package inventory")
    packages = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) == 3 and fields[1] == "installed":
            packages[fields[0]] = fields[2]
    return packages


def validate_required_packages(states, installed=None):
    installed = package_inventory() if installed is None else installed
    needed = set()
    enabled_values = ("enabled", "enabled-runtime", "generated", "indirect")
    for unit, packages in REQUIRED_PACKAGES_BY_UNIT.items():
        state = states.get(unit) or {}
        if state.get("active") or state.get("enabled") in enabled_values:
            needed.update(packages)
    missing = sorted(needed - set(installed))
    if missing:
        die("Required OS packages are missing for restored services: " +
            ", ".join(missing) +
            ". Install them before restore with: apt-get install -y " +
            " ".join(missing))
    return sorted(needed)


def validate_source_inventory(states):
    enabled_values = ("enabled", "enabled-runtime", "generated", "indirect")
    missing = []
    for unit, paths in REQUIRED_PATHS_BY_UNIT.items():
        state = states.get(unit) or {}
        if not (state.get("active") or state.get("enabled") in enabled_values):
            continue
        for path in paths:
            if not os.path.exists(path):
                missing.append((unit, path))
    if missing:
        details = "; ".join(unit + " -> " + path for unit, path in missing)
        die("Full backup inventory is incomplete for active services: " + details)
    return True


def system_accounts():
    accounts = {}
    result = subprocess.run(["getent", "passwd", "tgbotapi"],
                            capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        fields = result.stdout.strip().split(":")
        accounts["tgbotapi"] = {
            "uid": int(fields[2]), "gid": int(fields[3]),
            "home": fields[5], "shell": fields[6],
        }
    return accounts


def telegram_local_api_status(skip_media=False):
    binary = Path("/usr/local/bin/telegram-bot-api")
    config = Path("/etc/default/telegram-bot-api")
    data = Path(TELEGRAM_DATA_ROOT)
    data_files = [x for x in data.rglob("*") if x.is_file()] if data.is_dir() else []
    return {
        "enabled": Path(TELEGRAM_LOCAL_FLAG).is_file(),
        "config_present": config.is_file(),
        "binary_present": binary.is_file(),
        "binary_sha256": sha_file(binary) if binary.is_file() else None,
        "media_cache_included": bool(data.is_dir() and not skip_media),
        "data_files": len(data_files),
        "data_bytes": sum(x.stat().st_size for x in data_files),
    }


def validate_local_api_backup(api_status, accounts, skip_media):
    if not api_status.get("enabled"):
        return
    for name in TELEGRAM_REQUIRED_FILES:
        if not Path(name).is_file():
            die("Local Telegram API is enabled but a required migration file is missing: " + name)
    if "tgbotapi" not in accounts:
        die("Local Telegram API is enabled but the tgbotapi system account is missing")
    if not skip_media and not api_status.get("media_cache_included"):
        die("Local Telegram API media cache is missing from a full backup")


def ensure_system_accounts(accounts):
    for name in accounts:
        if name != "tgbotapi":
            die("Unexpected system account in backup: " + name)
        if subprocess.run(["getent", "passwd", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            continue
        if subprocess.run(["getent", "group", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0:
            run("groupadd", "--system", name)
        run("useradd", "--system", "--gid", name, "--home-dir",
            TELEGRAM_DATA_ROOT, "--shell", "/usr/sbin/nologin", name)


def fix_telegram_api_ownership():
    data = Path(TELEGRAM_DATA_ROOT)
    if not data.exists():
        return
    run("chown", "-R", "tgbotapi:tgbotapi", str(data))
    for path in [data, *data.rglob("*")]:
        if path.is_dir():
            path.chmod(0o700)


def current_enabled_state(unit):
    result = subprocess.run(["systemctl", "is-enabled", unit],
                            capture_output=True, text=True, timeout=12)
    return result.stdout.strip() or "not-found"


def restore_service_states(states, activate_telegram=False):
    if not isinstance(states, dict) or not states:
        return []
    run("systemctl", "daemon-reload")
    pending = []
    enabled_values = ("enabled", "enabled-runtime")
    for unit in SERVICES:
        state = states.get(unit)
        if not isinstance(state, dict):
            continue
        desired = state.get("enabled", "not-found")
        gated = unit in CUTOVER_GATED_UNITS and not activate_telegram
        if gated:
            if state.get("active") or desired in enabled_values:
                pending.append(unit)
            current = current_enabled_state(unit)
            if current in enabled_values:
                run("systemctl", "disable", unit)
            continue
        current = current_enabled_state(unit)
        if desired in enabled_values and current not in enabled_values:
            run("systemctl", "enable", unit)
        elif desired == "disabled" and current in enabled_values:
            run("systemctl", "disable", unit)
        elif desired == "masked" and current != "masked":
            run("systemctl", "mask", unit)
    # Start dependencies and network services first; the Telegram worker is last.
    for unit in SERVICES:
        state = states.get(unit)
        if not isinstance(state, dict) or not state.get("active"):
            continue
        if unit in CUTOVER_GATED_UNITS and not activate_telegram:
            continue
        run("systemctl", "start", unit)
        if subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode:
            die("Restored service did not start: " + unit)
    return pending


def stop_services(services):
    # The input order is dependency-first, so stop consumers first.
    failures = []
    for service in reversed(services):
        try:
            run("systemctl", "stop", service)
            active = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if active.returncode == 0:
                failures.append(service + " remained active")
        except Exception as exc:
            failures.append(service + ": " + str(exc))
    if failures:
        die("Could not quiesce all services: " + "; ".join(failures))


def start_services(services):
    # The input order is dependency-first, so start providers first.
    # Continue after one failure so every service gets a restart attempt.
    failures = []
    for service in services:
        try:
            run("systemctl", "start", service)
            active = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if active.returncode != 0:
                failures.append(service + " did not become active")
        except Exception as exc:
            failures.append(service + ": " + str(exc))
    if failures:
        die("Some services could not be restarted: " + "; ".join(failures))


def network_diagnostics():
    """Encrypted audit record for network rules; never replay firewall on restore."""
    checks = {
        "ufw": ["ufw","status","verbose"],
        "ipv4_routes": ["ip","-4","route","show"],
        "ipv6_routes": ["ip","-6","route","show"],
        "iptables": ["iptables-save"],
        "nftables": ["nft","list","ruleset"],
        "sysctl_forwarding": ["sysctl","net.ipv4.ip_forward","net.ipv6.conf.all.forwarding"],
    }
    diagnostics = {}
    for name,cmd in checks.items():
        try:
            outcome=subprocess.run(cmd,capture_output=True,text=True,timeout=8)
            if outcome.returncode == 0:
                diagnostics[name]=outcome.stdout[:200000]
        except (OSError,subprocess.TimeoutExpired):
            pass
    return diagnostics


def _tar_entries(out,stage):
    for folder in ("manifest.json", "db", "files"):
        path = stage / folder
        if not path.exists():
            continue
        if path.is_file():
            out.add(path, arcname=folder, recursive=False)
        else:
            for p in sorted(path.rglob("*")):
                if p.is_file() and not p.name.endswith(SENSITIVE_TRANSIENT_SUFFIXES):
                    out.add(p, arcname=str(p.relative_to(stage)), recursive=False)


def pack(stage, tar_path, manifest):
    """Retained for offline test fixtures and explicit unencrypted staging."""
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8")
    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as out:
        _tar_entries(out,stage)


def pack_encrypted_stream(stage, dest, manifest, key):
    """Stream tar->gzip->GPG directly; no duplicate 1.5GB plaintext tar.

    The encrypted .part file is made private and renamed by the caller only
    after GPG exits successfully.
    """
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8")
    argv=["gpg","--batch","--yes","--no-tty","--pinentry-mode","loopback",
          "--passphrase-file",str(key),"--symmetric","--cipher-algo","AES256",
          "--s2k-digest-algo","SHA512","--s2k-count","65011712",
          "--output",str(dest)]
    with subprocess.Popen(argv,stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE) as proc:
        try:
            with tarfile.open(fileobj=proc.stdin,mode="w|gz",
                              format=tarfile.PAX_FORMAT) as tar:
                _tar_entries(tar,stage)
            proc.stdin.close()
            errors=proc.stderr.read()
            code=proc.wait(timeout=1800)
            if code:
                die("Encrypted archive streaming failed: " +
                    errors.decode("utf-8","replace")[-450:])
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            raise
    dest.chmod(0o600)


def create_backup(directory=DEFAULT_OUTPUT, key=DEFAULT_KEY, quiesce=False, skip_media=False):
    ensure_root()
    key = key_file(key, generate=True)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    domain = get_domain()
    source_states = service_states()
    packages = package_inventory()
    validate_required_packages(source_states, packages)
    validate_source_inventory(source_states)
    accounts = system_accounts()
    api_status = telegram_local_api_status(skip_media=skip_media)
    validate_local_api_backup(api_status, accounts, skip_media)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / ("masiha-full-" + domain + "-" + stamp + ".tar.gz.gpg")
    if final.exists():
        die("Destination backup already exists: " + str(final))
    with tempfile.TemporaryDirectory(prefix=".masiha-stage-", dir=str(directory)) as tmp:
        stage = Path(tmp)
        os.chmod(stage, 0o700)
        running = active_services() if quiesce else []
        snapshot_error = None
        try:
            if running:
                print("QUIESCING_VPN_SERVICES", len(running), flush=True)
                stop_services(running)
            databases = snapshot_databases(stage, skip_media=skip_media)
            files = copy_snapshot(stage, skip_media=skip_media)
        except BaseException as exc:
            snapshot_error = exc
        restart_error = None
        if running:
            try:
                start_services(running)
                print("VPN_SERVICES_RESTARTED", flush=True)
            except Exception as exc:
                restart_error = exc
        if restart_error:
            die("Backup snapshot error=" + str(snapshot_error) +
                "; CRITICAL: service restart error=" + str(restart_error) +
                ". Inspect systemctl status for every listed service immediately.")
        if snapshot_error:
            raise snapshot_error
        if not files:
            die("Backup contains no configuration files")
        commit = subprocess.run(["git", "-C", "/root/Masih-panel",
                                  "rev-parse", "HEAD"], capture_output=True, text=True)
        manifest = {
            "format": "masiha-full", "version": VERSION,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_domain": domain, "source_git_commit": commit.stdout.strip()
            if commit.returncode == 0 else "unavailable",
            "snapshot_type": "quiesced" if quiesce else "online-sqlite-consistent",
            "telegram_media_included": not skip_media,
            "telegram_local_api": api_status,
            "system_accounts": accounts,
            "package_inventory": packages,
            "service_states": source_states,
            "databases": databases, "files": files, "source_roots": list(SOURCES),
            "network_audit_only": network_diagnostics(),
        }
        pending = directory / ("." + final.name + ".part")
        try:
            pack_encrypted_stream(stage, pending, manifest, key)
            os.replace(pending, final)
        finally:
            pending.unlink(missing_ok=True)
        digest = sha_file(final)
        sidecar = Path(str(final) + ".sha256")
        sidecar.write_text(digest + "  " + final.name + "\n", encoding="ascii")
        sidecar.chmod(0o600)
    print("BACKUP_READY", final)
    print("ARCHIVE_SHA256", digest)
    print("KEY_FILE_SEPARATE", key)
    print("DATABASE_TABLES", {k:len(v["tables"]) for k,v in databases.items()})
    print("BACKED_UP_FILES", len(files))
    print("ARCHIVE_BYTES", final.stat().st_size)
    return final


def unpack_verified(archive, key, work):
    archive = Path(archive)
    if not archive.is_file():
        die("Backup archive not found")
    sidecar = Path(str(archive) + ".sha256")
    if sidecar.is_file():
        expected = sidecar.read_text().split()[0]
        if sha_file(archive) != expected:
            die("Encrypted archive SHA256 mismatch")
    plain = work / "payload.tar.gz"
    crypt(archive, plain, key, decrypt=True)
    contents = work / "contents"
    contents.mkdir(mode=0o700)
    with tarfile.open(plain, "r:gz") as tf:
        members = tf.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)):
            die("Duplicate names in tar archive")
        if "manifest.json" not in names:
            die("Missing backup manifest")
        meta = tf.getmember("manifest.json")
        if not meta.isfile() or meta.size > 10 * 1024 * 1024:
            die("Invalid manifest")
        manifest = json.load(tf.extractfile(meta))
        if manifest.get("format") != "masiha-full" or manifest.get("version") not in (1, VERSION):
            die("Unsupported backup format")
        if set(manifest.get("databases",{})) != set(DATABASES.values()):
            die("Database list is incomplete")
        entries = manifest.get("files")
        if not isinstance(entries,dict) or not entries:
            die("Missing file manifest")
        if manifest.get("version", 1) >= 2:
            api = manifest.get("telegram_local_api") or {}
            if api.get("enabled"):
                for required in TELEGRAM_REQUIRED_FILES:
                    if required not in entries:
                        die("Local Telegram API backup is incomplete: " + required)
                if "tgbotapi" not in manifest.get("system_accounts", {}):
                    die("Local Telegram API backup lacks its system account")
                if api.get("media_cache_included") and not any(
                    name.startswith(TELEGRAM_DATA_ROOT + "/") for name in entries
                ):
                    die("Local Telegram API media cache is missing from archive")
            states = manifest.get("service_states", {})
            if not isinstance(states, dict) or any(k not in SERVICES for k in states):
                die("Invalid source service-state manifest")
        allowed = {"manifest.json"}
        allowed.update("db/" + k + ".db" for k in DATABASES.values())
        for name,entry in entries.items():
            safe_path(name)
            if entry["type"] == "file":
                allowed.add("files/" + name.lstrip("/"))
            elif entry["type"] == "symlink":
                valid_link(name, entry["target"])
            else:
                die("Invalid entry type for " + name)
        for tar_member in members:
            if tar_member.isdir():
                if tar_member.name not in ("db","files") and not tar_member.name.startswith("files/"):
                    die("Unexpected directory in archive")
                continue
            if not tar_member.isfile() or tar_member.name not in allowed:
                die("Unsafe or unexpected archive member: " + tar_member.name)
            if tar_member.name == "manifest.json":
                continue
            dest = contents / tar_member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(tar_member) as data, open(dest,"xb") as out:
                shutil.copyfileobj(data,out)
            dest.chmod(0o600)
    for label,entry in manifest["databases"].items():
        path = contents / "db" / (label + ".db")
        if not path.is_file() or path.stat().st_size != entry["bytes"] or sha_file(path) != entry["sha256"]:
            die("Database checksum failed: " + label)
        with sqlite3.connect("file:" + str(path) + "?mode=ro",uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                die("Corrupt SQLite database: " + label)
    for original,entry in entries.items():
        if entry["type"] != "file":
            continue
        path = contents / "files" / original.lstrip("/")
        if not path.is_file() or path.stat().st_size != entry["size"] or sha_file(path) != entry["sha256"]:
            die("File checksum mismatch: " + original)
    print("ARCHIVE_VERIFIED", manifest["source_domain"])
    print("ARCHIVE_DATABASES", {k:len(v["tables"]) for k,v in manifest["databases"].items()})
    print("ARCHIVE_FILES",len(entries))
    return manifest, contents


def map_target(path, prefix=None):
    safe_path(path)
    return Path(prefix) / path.lstrip("/") if prefix else Path(path)


def restore_files(manifest, contents, root=None, rollback=None):
    restored = []
    file_specs = [(path,entry,contents/"files"/path.lstrip("/"))
                  for path,entry in manifest["files"].items()]
    for original,label in DATABASES.items():
        file_specs.append((original, {"type":"file","mode":0o600,"uid":0,"gid":0},
                           contents/"db"/(label+".db")))
    # Regular files before symlinks so Let's Encrypt targets already exist.
    file_specs.sort(key=lambda x: (x[1]["type"]=="symlink",x[0]))
    try:
        for name,entry,source in file_specs:
            destination = map_target(name,root)
            # Preserve confidentiality when restoring PKI/private-key directories
            # that are absent on a freshly provisioned host.
            destination.parent.mkdir(mode=0o700, parents=True,exist_ok=True)
            if destination.is_dir() and not destination.is_symlink():
                die("Destination is directory, expected file: " + str(destination))
            prior = None
            if rollback is not None and (destination.exists() or destination.is_symlink()):
                prior = rollback / str(len(restored))
                if destination.is_symlink():
                    os.symlink(os.readlink(destination),prior)
                else:
                    shutil.copy2(destination,prior,follow_symlinks=False)
            restored.append((destination,prior))
            temp = destination.with_name(destination.name+".masiha-restore-tmp")
            temp.unlink(missing_ok=True)
            if entry["type"]=="symlink":
                valid_link(name,entry["target"])
                os.symlink(entry["target"],temp)
            else:
                shutil.copyfile(source,temp)
                temp.chmod(int(entry.get("mode",0o600)) & 0o777)
                if root is None:
                    os.chown(temp,int(entry.get("uid",0)),int(entry.get("gid",0)))
            os.replace(temp,destination)
        # Never replay a destination WAL against a restored SQLite snapshot.
        # Preserve WAL/SHM in the rollback log before clearing them.
        for path in DATABASES:
            db = map_target(path,root)
            for extra in ("-wal","-shm"):
                sidecar = Path(str(db)+extra)
                if rollback is not None:
                    previous = None
                    if sidecar.is_file():
                        previous = rollback / str(len(restored))
                        shutil.copy2(sidecar,previous)
                    restored.append((sidecar,previous))
                sidecar.unlink(missing_ok=True)
        return restored
    except Exception:
        if rollback is not None:
            revert(restored)
        raise


def revert(restored):
    for path,prior in reversed(restored):
        if prior is not None:
            os.replace(prior,path)
        else:
            path.unlink(missing_ok=True)


def validate_restored(root=None, manifest=None):
    for original in DATABASES:
        db = map_target(original,root)
        with sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True) as c:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                die("Restored SQLite integrity failed: " + original)
    if root is None:
        api = (manifest or {}).get("telegram_local_api") or {}
        if api.get("enabled"):
            for required in TELEGRAM_REQUIRED_FILES:
                if not Path(required).is_file():
                    die("Restored Local Telegram API file is missing: " + required)
            if not os.access("/usr/local/bin/telegram-bot-api", os.X_OK):
                die("Restored Telegram Bot API binary is not executable")
            if not Path(TELEGRAM_DATA_ROOT).is_dir():
                die("Restored Telegram Bot API data directory is missing")
            if subprocess.run(["getent", "passwd", "tgbotapi"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                die("Restored tgbotapi system account is missing")
        subprocess.run(["systemctl","daemon-reload"],check=True,timeout=30)
        source_states = (manifest or {}).get("service_states") or {}
        validate_required_packages(source_states)
        enabled_values = ("enabled", "enabled-runtime", "generated", "indirect")
        required_binaries = {
            "strongswan-starter.service": "/usr/sbin/ipsec",
            "xl2tpd.service": "/usr/sbin/xl2tpd",
            "openvpn-server@masiha.service": "/usr/sbin/openvpn",
            "openvpn-server@masiha-ros6.service": "/usr/sbin/openvpn",
            "masiha-sstp.service": "/opt/masiha-sstp/sbin/accel-pppd",
            "masiha-ipsec-network.service": "/usr/local/sbin/masiha-ipsec-firewall",
            "masiha-sstp-network.service": "/usr/local/sbin/masiha-sstp-network",
        }
        for unit, binary in required_binaries.items():
            state = source_states.get(unit) or {}
            if state.get("active") or state.get("enabled") in enabled_values:
                if not Path(binary).is_file() or not os.access(binary, os.X_OK):
                    die("Required executable for restored service is missing: " +
                        unit + " -> " + binary)
        for unit, state in source_states.items():
            if state.get("active") or state.get("enabled") in enabled_values:
                loaded = subprocess.run(
                    ["systemctl", "show", "-p", "LoadState", "--value", unit],
                    capture_output=True, text=True, timeout=12)
                if loaded.returncode or loaded.stdout.strip() == "not-found":
                    die("Required systemd unit is not installed after restore: " + unit)
        for binary,command in (
            ("/usr/local/x-ui/bin/xray-linux-amd64",
             ["/usr/local/x-ui/bin/xray-linux-amd64","run","-test","-c",
              "/usr/local/x-ui/bin/config.json"]),
            ("/usr/sbin/nginx",["nginx","-t"]),
            ("/usr/sbin/ocserv",["ocserv","-t","-c","/etc/ocserv/ocserv.conf"]),
        ):
            if Path(binary).is_file():
                p=subprocess.run(command,capture_output=True,timeout=35)
                if p.returncode:
                    die("Restore preflight failed for " + binary + ": " +
                        p.stderr.decode("utf-8","replace")[-300:])
    print("RESTORED_DATABASE_AND_SERVICE_CONFIG_VALIDATION=PASS")


def restore(archive,key,confirm=False,staging_root=None,allow_domain_mismatch=False,
            activate_telegram=False):
    ensure_root()
    if not confirm:
        die("Refusing restore without --confirm-restore")
    key=key_file(key)
    with tempfile.TemporaryDirectory(prefix="masiha-restore-") as temp:
        work=Path(temp)
        manifest,contents=unpack_verified(archive,key,work)
        if staging_root:
            original_root=Path(staging_root)
            if original_root.is_symlink():
                die("Isolated staging directory must not be a symlink")
            staging_root=original_root.resolve()
            if str(staging_root)=="/" or not str(staging_root).startswith(("/tmp/","/var/tmp/")):
                die("Isolated staging root must be under /tmp or /var/tmp")
            if staging_root.exists() and any(staging_root.iterdir()):
                die("Isolated staging root must be empty to avoid overwriting data")
            staged_files=restore_files(manifest,contents,root=staging_root)
            validate_restored(root=staging_root, manifest=manifest)
            print("ISOLATED_RESTORE_OK",staging_root,"entries",len(staged_files))
            return
        destination_domain=get_domain()
        if destination_domain != manifest["source_domain"] and not allow_domain_mismatch:
            die("Domain mismatch ("+manifest["source_domain"]+" -> "+destination_domain+
                "); restore only after matching domain, or use --allow-domain-mismatch with manual cert/DNS migration")
        if destination_domain != manifest["source_domain"]:
            print("WARNING: certificates, DNS, subscriptions and node URLs must be manually migrated for new domain",flush=True)
        source_states = manifest.get("service_states") or {}
        validate_required_packages(source_states)
        target_states=service_states()
        running=[unit for unit,state in target_states.items() if state.get("active")]
        # Take encrypted rollback snapshot before mutating any live file.
        print("CREATING_DESTINATION_SAFETY_BACKUP",flush=True)
        rollback_backup=create_backup(quiesce=False)
        print("ROLLBACK_ARCHIVE",rollback_backup,flush=True)
        undo=work/"rollback";undo.mkdir(mode=0o700)
        changed=[]
        try:
            stop_services(running)
            ensure_system_accounts(manifest.get("system_accounts", {}))
            changed=restore_files(manifest,contents,rollback=undo)
            fix_telegram_api_ownership()
            validate_restored(manifest=manifest)
            source_states=manifest.get("service_states")
            if source_states:
                pending=restore_service_states(source_states,activate_telegram=activate_telegram)
                if pending:
                    print("TELEGRAM_CUTOVER_PENDING_UNITS",pending,flush=True)
                    print("Keep Telegram polling/import workers stopped until the source server is shut down.",flush=True)
                    enable_units = [u for u in pending
                                    if (source_states.get(u) or {}).get("enabled")
                                    in ("enabled", "enabled-runtime")]
                    start_only = [u for u in pending if u not in enable_units
                                  and (source_states.get(u) or {}).get("active")]
                    if enable_units:
                        print("After source shutdown, run: systemctl enable --now " +
                              " ".join(enable_units),flush=True)
                    if start_only:
                        print("After source shutdown, run: systemctl start " +
                              " ".join(start_only),flush=True)
            else:
                # Backward compatibility for v1 archives without service-state metadata.
                to_start=[unit for unit in running if
                          activate_telegram or unit not in CUTOVER_GATED_UNITS]
                start_services(to_start)
                if not activate_telegram:
                    print("LEGACY_BACKUP: service states were not recorded; review and enable target services manually.",flush=True)
        except Exception:
            print("RESTORE_FAILED_ATTEMPTING_ROLLBACK",file=sys.stderr,flush=True)
            for unit in running:
                subprocess.run(["systemctl","stop",unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if changed:
                revert(changed)
            subprocess.run(["systemctl","daemon-reload"],check=False)
            if target_states:
                try:
                    restore_service_states(target_states,activate_telegram=True)
                except Exception as rollback_error:
                    print("SERVICE_ROLLBACK_ERROR",str(rollback_error),file=sys.stderr,flush=True)
            else:
                for unit in reversed(running):
                    subprocess.run(["systemctl","start",unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            print("ROLLBACK_ATTEMPTED",file=sys.stderr,flush=True)
            raise
        print("LIVE_RESTORE_COMPLETE", manifest["source_domain"])
        print("CHECK_DNS_NODES_CERTIFICATES_AND_REAL_CONNECTIVITY_BEFORE_CUTOVER")


def main():
    # All temporary plaintext snapshots and newly created directories are private.
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    b=sub.add_parser("backup")
    b.add_argument("--output-dir",default=str(DEFAULT_OUTPUT))
    b.add_argument("--key-file",default=str(DEFAULT_KEY))
    b.add_argument("--skip-media",action="store_true",
                   help="exclude media tables and Telegram Local API cache; preserve sales and points")
    b.add_argument("--quiesce",action="store_true",
                   help="briefly stop VPN services while taking snapshot (recommended for final migration)")
    i=sub.add_parser("inspect",help="validate archive and show portable identity summary")
    i.add_argument("archive")
    i.add_argument("--key-file",default=str(DEFAULT_KEY))
    v=sub.add_parser("verify")
    v.add_argument("archive")
    v.add_argument("--key-file",default=str(DEFAULT_KEY))
    r=sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--key-file",default=str(DEFAULT_KEY))
    r.add_argument("--confirm-restore",action="store_true")
    r.add_argument("--staging-root",help="isolated restore-only test directory under /tmp or /var/tmp")
    r.add_argument("--allow-domain-mismatch",action="store_true",
                   help="legacy alias for safe --portable restore; never replay source server identity")
    r.add_argument("--portable",action="store_true",
                   help="import source data while preserving destination TLS/domain/web panel")
    r.add_argument("--preview",action="store_true",
                   help="validate planned portable migration without changing the destination")
    r.add_argument("--activate-telegram",action="store_true",
                   help="start restored Telegram/extension worker immediately; ONLY after old server stops it")
    args=parser.parse_args()
    if args.action=="backup":
        create_backup(Path(args.output_dir),Path(args.key_file),args.quiesce,args.skip_media)
    elif args.action=="verify":
        ensure_root()
        with tempfile.TemporaryDirectory(prefix="masiha-verify-") as tmp:
            unpack_verified(args.archive,key_file(args.key_file),Path(tmp))
        print("BACKUP_INTEGRITY_VERIFIED=PASS")
    elif args.action=="inspect":
        ensure_root()
        with tempfile.TemporaryDirectory(prefix="masiha-inspect-") as tmp:
            manifest,contents=unpack_verified(args.archive,key_file(args.key_file),Path(tmp))
            summary={
                "source_domain":manifest["source_domain"],
                "destination_domain":get_domain(),
                "requires_rebind":get_domain()!=manifest["source_domain"],
                "created_utc":manifest.get("created_utc",""),
                "snapshot_type":manifest.get("snapshot_type",""),
                "media_included":manifest.get("telegram_media_included",True),
                "database_tables":{k:len(v.get("tables",{})) for k,v in manifest["databases"].items()},
                "files":len(manifest["files"]),
                "safe_portable_restore":True,
            }
            print("BACKUP_SUMMARY_JSON "+json.dumps(summary,ensure_ascii=False))
    elif args.action=="restore":
        if args.portable or args.allow_domain_mismatch or args.preview:
            if not args.confirm_restore and not args.preview:
                die("Refusing portable restore without --confirm-restore")
            if args.staging_root:
                die("Use normal staging restore without --portable")
            ensure_root()
            with tempfile.TemporaryDirectory(prefix="masiha-portable-unpack-") as tmp:
                from portable_migration import migrate_destination
                manifest,contents=unpack_verified(args.archive,key_file(args.key_file),Path(tmp))
                report=migrate_destination(manifest,contents,get_domain(),apply=not args.preview)
                print(("PORTABLE_RESTORE_PLAN " if args.preview else "PORTABLE_RESTORE_COMPLETE ")+
                      json.dumps(report,ensure_ascii=False))
                if not args.preview:
                    print("TELEGRAM_CUTOVER_REQUIRED; NEVER POLL THE SAME BOT ON TWO SERVERS")
        else:
            restore(args.archive,args.key_file,args.confirm_restore,args.staging_root,
                    args.allow_domain_mismatch,args.activate_telegram)


if __name__=="__main__":
    try:
        main()
    except (RuntimeError,ValueError,OSError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print("ERROR:",str(exc),file=sys.stderr)
        sys.exit(1)
