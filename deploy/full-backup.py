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

VERSION = 1
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
    "/etc/openvpn/server/masiha.conf",
    "/etc/ocserv",
    "/etc/letsencrypt",
    "/etc/nginx/sites-available/masiha-vpn",
    "/etc/nginx/sites-enabled/masiha-vpn",
    "/etc/systemd/system/x-ui.service",
    "/etc/systemd/system/masiha-vpn-extension.service",
    "/etc/sysctl.d/98-masiha-vpn-forward.conf",
    "/usr/local/x-ui",
    "/opt/masiha-vpn-extension",
)
EXCLUDED_DIRS = frozenset((
    "backups", "release-staging", "toolchain", ".git", "__pycache__",
    ".venv", ".cache", "node_modules",
))
SENSITIVE_TRANSIENT_SUFFIXES = (".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")
SERVICES = (
    "masiha-vpn-extension.service", "x-ui.service",
    "openvpn-server@masiha.service", "ocserv.service",
)
DEFAULT_KEY = Path("/root/.masiha-backup.key")
DEFAULT_OUTPUT = Path("/root/masiha-backups")


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


def snapshot_databases(stage):
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


def copy_snapshot(stage):
    entries = {}
    for src in source_files():
        path = safe_path(str(src))
        meta = src.lstat()
        if stat.S_ISLNK(meta.st_mode):
            link = os.readlink(src)
            valid_link(path, link)
            entries[path] = {"type": "symlink", "target": link}
        elif stat.S_ISREG(meta.st_mode):
            dest = stage / "files" / path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest, follow_symlinks=False)
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


def stop_services(services):
    for service in services:
        run("systemctl", "stop", service)


def start_services(services):
    for service in reversed(services):
        run("systemctl", "start", service)


def pack(stage, tar_path, manifest):
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8")
    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as out:
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


def create_backup(directory=DEFAULT_OUTPUT, key=DEFAULT_KEY, quiesce=False):
    ensure_root()
    key = key_file(key, generate=True)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    domain = get_domain()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / ("masiha-full-" + domain + "-" + stamp + ".tar.gz.gpg")
    if final.exists():
        die("Destination backup already exists: " + str(final))
    with tempfile.TemporaryDirectory(prefix=".masiha-stage-", dir=str(directory)) as tmp:
        stage = Path(tmp)
        os.chmod(stage, 0o700)
        running = active_services() if quiesce else []
        try:
            if running:
                print("QUIESCING_VPN_SERVICES", len(running), flush=True)
                stop_services(running)
            databases = snapshot_databases(stage)
            files = copy_snapshot(stage)
        finally:
            if running:
                start_services(running)
                print("VPN_SERVICES_RESTARTED", flush=True)
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
            "databases": databases, "files": files, "source_roots": list(SOURCES),
        }
        plain = stage / "payload.tar.gz"
        pack(stage, plain, manifest)
        pending = directory / ("." + final.name + ".part")
        try:
            crypt(plain, pending, key)
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
        if manifest.get("format") != "masiha-full" or manifest.get("version") != VERSION:
            die("Unsupported backup format")
        if set(manifest.get("databases",{})) != set(DATABASES.values()):
            die("Database list is incomplete")
        entries = manifest.get("files")
        if not isinstance(entries,dict) or not entries:
            die("Missing file manifest")
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


def validate_restored(root=None):
    for original in DATABASES:
        db = map_target(original,root)
        with sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True) as c:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                die("Restored SQLite integrity failed: " + original)
    if root is None:
        subprocess.run(["systemctl","daemon-reload"],check=True,timeout=30)
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


def restore(archive,key,confirm=False,staging_root=None,allow_domain_mismatch=False):
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
            validate_restored(root=staging_root)
            print("ISOLATED_RESTORE_OK",staging_root,"entries",len(staged_files))
            return
        destination_domain=get_domain()
        if destination_domain != manifest["source_domain"] and not allow_domain_mismatch:
            die("Domain mismatch ("+manifest["source_domain"]+" -> "+destination_domain+
                "); restore only after matching domain, or use --allow-domain-mismatch with manual cert/DNS migration")
        if destination_domain != manifest["source_domain"]:
            print("WARNING: certificates, DNS, subscriptions and node URLs must be manually migrated for new domain",flush=True)
        running=active_services()
        # Take encrypted rollback snapshot before mutating any live file.
        print("CREATING_DESTINATION_SAFETY_BACKUP",flush=True)
        rollback_backup=create_backup(quiesce=False)
        print("ROLLBACK_ARCHIVE",rollback_backup,flush=True)
        undo=work/"rollback";undo.mkdir(mode=0o700)
        changed=[]
        try:
            stop_services(running)
            changed=restore_files(manifest,contents,rollback=undo)
            validate_restored()
            start_services(running)
            # Run a second check after service start.
            for unit in running:
                if subprocess.run(["systemctl","is-active","--quiet",unit]).returncode:
                    die("Restored service did not start: "+unit)
        except Exception:
            print("RESTORE_FAILED_ATTEMPTING_ROLLBACK",file=sys.stderr,flush=True)
            for unit in running:
                subprocess.run(["systemctl","stop",unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if changed:
                revert(changed)
            subprocess.run(["systemctl","daemon-reload"],check=False)
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
    b.add_argument("--quiesce",action="store_true",
                   help="briefly stop VPN services while taking snapshot (recommended for final migration)")
    v=sub.add_parser("verify")
    v.add_argument("archive")
    v.add_argument("--key-file",default=str(DEFAULT_KEY))
    r=sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--key-file",default=str(DEFAULT_KEY))
    r.add_argument("--confirm-restore",action="store_true")
    r.add_argument("--staging-root",help="isolated restore-only test directory under /tmp or /var/tmp")
    r.add_argument("--allow-domain-mismatch",action="store_true")
    args=parser.parse_args()
    if args.action=="backup":
        create_backup(Path(args.output_dir),Path(args.key_file),args.quiesce)
    elif args.action=="verify":
        ensure_root()
        with tempfile.TemporaryDirectory(prefix="masiha-verify-") as tmp:
            unpack_verified(args.archive,key_file(args.key_file),Path(tmp))
        print("BACKUP_INTEGRITY_VERIFIED=PASS")
    elif args.action=="restore":
        restore(args.archive,args.key_file,args.confirm_restore,args.staging_root,
                args.allow_domain_mismatch)


if __name__=="__main__":
    try:
        main()
    except (RuntimeError,ValueError,OSError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print("ERROR:",str(exc),file=sys.stderr)
        sys.exit(1)
