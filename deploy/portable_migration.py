#!/usr/bin/env python3
"""Destination-aware encrypted Masiha backup restoration.

The archive has already been authenticated and extracted by full_backup.py.
This module deliberately does not replay source machine identity, nginx/ACME,
panel binaries, service policy, or active remote node configuration.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote

DATABASES = {
    "panel": "/etc/x-ui/x-ui.db",
    "users": "/etc/masiha-vpn-extension/users.db",
    "sales": "/etc/masiha-vpn-extension/sales/sales.db",
}
PRESERVED_SETTINGS = frozenset((
    "secret", "panelGuid", "webDomain", "webListen", "webPort", "webBasePath",
    "webCertFile", "webKeyFile", "subDomain", "subListen", "subPort", "subPath",
    "subURI", "subProfileUrl", "subCertFile", "subKeyFile", "subEnable",
    "subEnableRouting", "subEncrypt", "subJsonURI", "subJsonPath",
    "tgBotEnable", "tgBotToken",
))
CUTOVER_SERVICES = (
    "masiha-vpn-extension.service", "masiha-channel-manager.timer",
    "masiha-channel-manager.service", "masiha-film-import.timer",
    "masiha-film-import.service", "masiha-film-discover.timer",
    "masiha-film-discover.service", "masiha-film2media-discovery.timer",
    "masiha-film2media-discovery.service", "masiha-rozmusic-import.timer",
    "masiha-rozmusic-import.service", "masiha-ai-media.timer",
    "masiha-ai-media.service", "masiha-audio-fingerprint.timer",
    "masiha-audio-fingerprint.service", "masiha-media-cache-maintenance.timer",
    "masiha-media-cache-maintenance.service",
)
RESTART_SERVICES = (
    "x-ui.service", "masiha-vpn-sidecar.service",
    "openvpn-server@masiha.service", "ocserv.service",
)
SPECIAL_FILES = frozenset((
    "/etc/ocserv/masiha.passwd",
    "/etc/default/telegram-bot-api",
    "/etc/systemd/system/telegram-bot-api.service",
    "/usr/local/bin/telegram-bot-api",
    "/etc/openvpn/server/masiha.conf",
    "/etc/openvpn/server/masiha-ros6.conf",
))
PORTABLE_PREFIXES = (
    "/etc/masiha-vpn-extension/sales/",
    "/etc/masiha-vpn-extension/ipsec/",
    "/etc/openvpn/masiha/",
    "/etc/openvpn/masiha-ros6/",
    "/var/lib/masiha-audio-fp/",
    "/var/lib/telegram-bot-api/",
)
SKIPPED_NAMES = frozenset((
    "/etc/masiha-vpn-extension/sales/telegram_local.enabled",
))


def portable_file(path):
    if path in SKIPPED_NAMES or path.endswith((".lock", ".db", "-wal", "-shm")):
        return False
    return path in SPECIAL_FILES or any(path.startswith(prefix) for prefix in PORTABLE_PREFIXES)


def _sqlcolumns(conn, name):
    return {r["name"]: r for r in conn.execute('PRAGMA table_info("'+name+'")')}


def _table_names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _rows(conn, table):
    return list(conn.execute("SELECT * FROM " + _quote(table)))


def _replace_rows(dst, table, rows):
    if not rows:
        return
    cols=list(rows[0].keys())
    names=",".join(_quote(x) for x in cols)
    values=",".join("?" for x in cols)
    dst.executemany("INSERT INTO "+_quote(table)+"("+names+") VALUES ("+values+")",
                    [[row[x] for x in cols] for row in rows])


def _domain_string(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new) if old and old != new else value
    if isinstance(value, list):
        return [_domain_string(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k:_domain_string(v, old, new) for k,v in value.items()}
    return value


def _backup_db(src, dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect("file:"+str(src)+"?mode=ro",uri=True,timeout=60) as inp:
        with sqlite3.connect(str(dest),timeout=60) as out:
            inp.backup(out,pages=256,sleep=0.05)
            assert out.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
            out.execute("PRAGMA journal_mode=DELETE")
    dest.chmod(0o600)


def _adapt_schema(candidate, target):
    """Carry target-only SQLite schema forward, preserving every legacy table."""
    tables = _table_names(candidate)
    for name in sorted(_table_names(target)-tables):
        if name.startswith("sqlite_"):
            continue
        definition=target.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
        if definition and definition[0]:
            candidate.execute(definition[0])
    for name in sorted(tables & _table_names(target)):
        old_cols=_sqlcolumns(candidate,name)
        for col,meta in _sqlcolumns(target,name).items():
            if col in old_cols:
                continue
            column=_quote(col)+" "+(meta["type"] or "TEXT")
            if meta["dflt_value"] is not None:
                column+=" DEFAULT "+meta["dflt_value"]
            # An absent new NOT NULL field without a default needs a safe
            # default value for all pre-existing rows.
            elif meta["notnull"]:
                column+=" DEFAULT "+("0" if "INT" in (meta["type"] or "").upper() else "''")
            candidate.execute("ALTER TABLE "+_quote(name)+" ADD COLUMN "+column)
    for name,sql in target.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        if sql and not candidate.execute("SELECT 1 FROM sqlite_master WHERE name=?",(name,)).fetchone():
            try:
                candidate.execute(sql)
            except sqlite3.IntegrityError:
                # Never silently drop a uniqueness invariant during migration.
                raise RuntimeError("Existing source data violates target index: "+name)


def _read_settings(db):
    return {key:value for key,value in db.execute('SELECT key,value FROM settings')}


def _load_json(text):
    return json.loads(text) if text else {}


def _free_port(used, start=8460):
    for port in range(start, 65536):
        if port not in used and port not in {80,443,4443,1194,62053,2096,8081}:
            used.add(port)
            return port
    raise RuntimeError("No free port for portable inbound")


def build_panel_candidate(source, target, output, source_domain, target_domain):
    """Preserve all legacy accounts/usage, add destination native inbounds, rebind TLS.

    Never connect/enable a remote node from the source: it may still be
    controlled by its original panel, causing concurrent configuration writes.
    """
    source, target, output=map(Path,(source,target,output))
    if output.exists():
        raise RuntimeError("Destination candidate already exists")
    if not target.is_file():
        raise RuntimeError("Install the destination Masiha panel before importing")
    _backup_db(source, output)
    src=sqlite3.connect(str(output));src.row_factory=sqlite3.Row
    dst=sqlite3.connect("file:"+str(target)+"?mode=ro",uri=True);dst.row_factory=sqlite3.Row
    try:
        src.execute("BEGIN IMMEDIATE")
        _adapt_schema(src,dst)
        if "client_host_rule_ids" in _sqlcolumns(src,"clients"):
            src.execute("UPDATE clients SET client_host_rule_ids='[]' "
                        "WHERE client_host_rule_ids IS NULL OR client_host_rule_ids=''")
        if "wg_keep_alive_range" in _sqlcolumns(src,"clients"):
            src.execute("UPDATE clients SET wg_keep_alive_range='' "
                        "WHERE wg_keep_alive_range IS NULL")
        # Destination panel login, session secret and web/subscription identity
        # must remain valid after importing the source client database.
        if "users" in _table_names(src) and "users" in _table_names(dst):
            columns=list(_sqlcolumns(src,"users"))
            current=_rows(dst,"users")
            src.execute('DELETE FROM "users"')
            for row in current:
                names=[n for n in columns if n in row.keys()]
                src.execute("INSERT INTO users("+",".join(_quote(n) for n in names)+") VALUES ("+
                            ",".join("?" for _ in names)+")",[row[n] for n in names])
        src_settings=_read_settings(src)
        target_settings=_read_settings(dst)
        for key,value in target_settings.items():
            if key in PRESERVED_SETTINGS:
                # 3x-ui's settings.key is not UNIQUE on some releases: INSERT
                # OR REPLACE creates a duplicate, and reads may return the
                # original S2 value. UPDATE all matching rows, or INSERT once.
                if key in src_settings:
                    src.execute("UPDATE settings SET value=? WHERE key=?",(value,key))
                else:
                    src.execute("INSERT INTO settings(key,value) VALUES(?,?)",(key,value))
        for key in ("subSupportUrl","subHappNewUrl","subHappFallbackUrl","subHappSubInfoButtonLink"):
            if key in src_settings and key not in PRESERVED_SETTINGS:
                value=_domain_string(src_settings[key],source_domain,target_domain)
                src.execute("UPDATE settings SET value=? WHERE key=?",(value,key))
        if "nodes" in _table_names(src):
            # Old external node is not allowed to start syncing from both hosts.
            src.execute("UPDATE nodes SET enable=0,status='disabled'")
        old_inbounds=_rows(src,"inbounds")
        native=_rows(dst,"inbounds")
        used={80,443,4443,1194,62053,2096,8081}
        for row in native:
            if row["enable"] and row["node_id"] is None:
                used.add(row["port"])
        old_to_new={}
        rebound_ports={}
        tls_certificate=None
        for native_row in native:
            ss=_load_json(native_row["stream_settings"])
            if ss.get("security")=="tls" and ss.get("tlsSettings",{}).get("certificates"):
                tls_certificate=ss["tlsSettings"]["certificates"]
                break
        cert_file=Path("/etc/letsencrypt/live")/target_domain/"fullchain.pem"
        key_file=Path("/etc/letsencrypt/live")/target_domain/"privkey.pem"
        if not tls_certificate and cert_file.exists() and key_file.exists():
            tls_certificate=[{"certificateFile":str(cert_file),"keyFile":str(key_file)}]
        for row in old_inbounds:
            external=row["node_id"] is not None
            port=row["port"]
            if not external:
                if port in used:
                    port=_free_port(used)
                else:
                    used.add(port)
            setting=_load_json(row["stream_settings"])
            if not external:
                setting=_domain_string(setting,source_domain,target_domain)
                if setting.get("security")=="tls":
                    if not tls_certificate:
                        raise RuntimeError("Destination TLS certificate is missing")
                    setting.setdefault("tlsSettings",{})["certificates"]=tls_certificate
                    setting["tlsSettings"]["serverName"]=target_domain
            update={"port":port,"stream_settings":json.dumps(setting,ensure_ascii=False,
                       separators=(",",":")),"enable":0 if external else row["enable"]}
            if "listen" in row.keys() and row["listen"] not in ("","0.0.0.0","::","127.0.0.1","localhost"):
                update["listen"]="0.0.0.0"
            if "share_addr" in row.keys() and row["share_addr"]==source_domain:
                update["share_addr"]=target_domain
            update_sql=",".join(_quote(k)+"=?" for k in update)
            src.execute("UPDATE inbounds SET "+update_sql+" WHERE id=?",
                        list(update.values())+[row["id"]])
            if row["port"]!=port and not external:
                rebound_ports[str(row["id"])]=[row["port"],port]
        max_id=max([r["id"] for r in old_inbounds]+[0])
        current_tags={str(r["tag"]) for r in old_inbounds}
        native_map={}
        for nr in native:
            max_id+=1
            row=dict(nr)
            row["id"]=max_id
            client_settings=_load_json(row.get("settings"))
            # Demo users on the destination must not become orphaned credentials.
            client_settings["clients"]=[]
            row["settings"]=json.dumps(client_settings,ensure_ascii=False,separators=(",",":"))
            tag=str(row.get("tag") or "")
            if tag in current_tags:
                tag=tag+"-destination-"+str(max_id)
                row["tag"]=tag
            current_tags.add(tag)
            colnames=[k for k in row if k in _sqlcolumns(src,"inbounds")]
            src.execute("INSERT INTO inbounds("+",".join(_quote(k) for k in colnames)+") VALUES ("+
                        ",".join("?" for _ in colnames)+")",[row[k] for k in colnames])
            native_map[nr["id"]]=max_id
        # Make legacy local credentials work through the destination's native
        # TLS endpoints, including the existing S3 -> luki Trojan port.
        associations={(r["client_id"],r["inbound_id"]) for r in _rows(src,"client_inbounds")}
        imported_credentials=0
        preferred={}
        for row in old_inbounds:
            if row["node_id"] is None:
                stream=_load_json(row["stream_settings"])
                key=(row["protocol"],stream.get("network"),stream.get("security"))
                preferred.setdefault(key,row)
        client_emails={r["email"].lower():r["id"] for r in _rows(src,"clients")}
        for target_inbound in native:
            stream=_load_json(target_inbound["stream_settings"])
            key=(target_inbound["protocol"],stream.get("network"),stream.get("security"))
            candidate_src=preferred.get(key)
            if not candidate_src:
                continue
            source_settings=_load_json(candidate_src["settings"])
            dest_id=native_map[target_inbound["id"]]
            target_settings=_load_json(src.execute(
                "SELECT settings FROM inbounds WHERE id=?",(dest_id,)).fetchone()[0])
            clients=source_settings.get("clients",[])
            if target_inbound["protocol"]=="shadowsocks":
                old_method=str(source_settings.get("method","")).replace("chacha20-poly1305","chacha20-ietf-poly1305")
                target_method=str(target_settings.get("method",""))
                if old_method!=target_method:
                    continue
            linked=[]
            for item in clients:
                cid=client_emails.get(str(item.get("email","")).lower())
                if cid is None:
                    continue
                linked.append(item)
                if (cid,dest_id) not in associations:
                    src.execute("INSERT INTO client_inbounds(client_id,inbound_id,flow_override,created_at) VALUES(?,?,?,?)",
                                (cid,dest_id,"",int(time.time())))
                    associations.add((cid,dest_id))
            target_settings["clients"]=linked
            src.execute("UPDATE inbounds SET settings=? WHERE id=?",
                        (json.dumps(target_settings,ensure_ascii=False,separators=(",",":")),dest_id))
            imported_credentials+=len(linked)
        if "hosts" in _table_names(src):
            for name in ("address","sni","host_header"):
                if name in _sqlcolumns(src,"hosts"):
                    src.execute("UPDATE hosts SET "+_quote(name)+"=? WHERE "+_quote(name)+"=?",
                                (target_domain,source_domain))
        bad=src.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError("Cross-version database foreign key mismatch")
        src.commit()
        assert src.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        report={
            "source_domain":source_domain,"destination_domain":target_domain,
            "source_clients":len(client_emails),"source_inbounds":len(old_inbounds),
            "destination_native_inbounds":len(native),
            "inbound_port_changes":rebound_ports,
            "legacy_remote_nodes_disabled":True,
            "source_credentials_added_to_native_inbounds":imported_credentials,
        }
        return report
    except Exception:
        src.rollback()
        raise
    finally:
        src.close();dst.close()


def build_sales_candidate(source, output, destination_subscription_uri):
    """Discard cached old-host links, not financial/order/media records.

    Every customer subscription is rebuilt from the preserved destination
    /sub/ base and original subId. Live bot delivery queries the installed
    panel and regenerates protocol profiles, so stale old-server Xray URLs
    should never be advertised as active after migration.
    """
    if not destination_subscription_uri.startswith("https://"):
        raise RuntimeError("Destination HTTPS subscription URL is not configured")
    _backup_db(Path(source),Path(output))
    count=0
    with sqlite3.connect(str(output)) as db:
        if "services" not in _table_names(db):
            return count
        for sid,protocol,data in db.execute("SELECT id,protocol,data FROM services").fetchall():
            if protocol!="xray":
                continue
            try:
                parsed=json.loads(data or "{}")
            except (ValueError,TypeError):
                continue
            subid=parsed.get("subId")
            if not isinstance(subid,str) or not (1<=len(subid)<=128):
                continue
            parsed["subscription"]=[destination_subscription_uri.rstrip("/")+"/"+
                                    quote(subid,safe="")]
            parsed["links"]=[]
            db.execute("UPDATE services SET data=? WHERE id=?",
                       (json.dumps(parsed,ensure_ascii=False,separators=(",",":")),sid))
            count+=1
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise RuntimeError("Rebound sales database integrity failed")
    return count


def migrate_destination(manifest,contents,destination_domain,backup_root=None,apply=True):
    """Apply staged, authenticated snapshot to installed target with rollback."""
    source_domain=manifest["source_domain"]
    if not isinstance(source_domain,str) or not source_domain:
        raise RuntimeError("Invalid archive domain")
    if not isinstance(destination_domain,str) or not destination_domain:
        raise RuntimeError("Invalid destination domain")
    for name,path in DATABASES.items():
        if not Path(path).is_file():
            raise RuntimeError("Destination component is not installed: "+name)
        if not (contents/"db"/(name+".db")).is_file():
            raise RuntimeError("Source archive has no "+name+" database")
    if not Path("/etc/letsencrypt/live/"+destination_domain+"/fullchain.pem").is_file():
        raise RuntimeError("Destination TLS certificate not found; configure HTTPS before migrating")
    data_items=[]
    for name,entry in manifest["files"].items():
        if portable_file(name) and entry.get("type")=="file":
            data_items.append((name,contents/"files"/name.lstrip("/")))
    base=Path(backup_root or "/root/masiha-backups")
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="masiha-portable-stage-",dir=str(base)) as tmp:
        temp=Path(tmp)
        candidate=temp/"panel.db"
        report=build_panel_candidate(contents/"db"/"panel.db",
                                     DATABASES["panel"],candidate,
                                     source_domain,destination_domain)
        with sqlite3.connect("file:"+str(candidate)+"?mode=ro",uri=True) as conn:
            uri=conn.execute("SELECT value FROM settings WHERE key='subURI' LIMIT 1").fetchone()
        if not uri:
            raise RuntimeError("Destination subscription URI not configured")
        sale_candidate=temp/"sales.db"
        report["cached_subscriptions_rebuilt"]=build_sales_candidate(
            contents/"db"/"sales.db",sale_candidate,str(uri[0]))
        for name in ("users","sales"):
            with sqlite3.connect("file:"+str(contents/"db"/(name+".db"))+"?mode=ro",uri=True) as db:
                if db.execute("pragma integrity_check").fetchone()[0]!="ok":
                    raise RuntimeError("Backup SQLite integrity failed: "+name)
        report["portable_files"]=len(data_items)
        report["telegram_remains_paused"]=True
        if not apply:
            return report
        stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
        rollback=base/("portable-rollback-"+stamp)
        rollback.mkdir(mode=0o700)
        report["rollback_directory"]=str(rollback)
        metadata=[]
        for name,src in data_items:
            dest=Path(name)
            if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise RuntimeError("Unsafe portable file destination: "+name)
            for ancestor in dest.parents:
                if ancestor.is_symlink():
                    raise RuntimeError("Symlink ancestor in portable destination: "+str(ancestor))
            if dest.exists():
                prior=rollback/"files"/name.lstrip("/")
                prior.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                shutil.copy2(dest,prior)
            metadata.append({"path":name,"existed":dest.exists()})
        for name,path in DATABASES.items():
            _backup_db(Path(path),rollback/(name+".db"))
        (rollback/"files.json").write_text(json.dumps(metadata,ensure_ascii=False))
        (rollback/"files.json").chmod(0o600)
        stopped=[]
        try:
            # Stop only destination components, never the source.
            for service in CUTOVER_SERVICES+("telegram-bot-api.service",)+RESTART_SERVICES:
                if subprocess.run(["systemctl","is-active","--quiet",service]).returncode==0:
                    subprocess.run(["systemctl","stop",service],check=True,timeout=60)
                    stopped.append(service)
            for name,source in data_items:
                dest=Path(name)
                dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                tmpfile=dest.with_name(dest.name+".portable-migrate-tmp")
                shutil.copyfile(source,tmpfile)
                tmpfile.chmod(0o600 if name not in ("/usr/local/bin/telegram-bot-api",) else 0o755)
                os.replace(tmpfile,dest)
            # Fresh Ubuntu installations may not yet have the dedicated
            # Telegram Local API account. Its old UID must never be replayed.
            if Path("/var/lib/telegram-bot-api").is_dir():
                if subprocess.run(["getent","passwd","tgbotapi"],stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode:
                    if subprocess.run(["getent","group","tgbotapi"],stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL).returncode:
                        subprocess.run(["groupadd","--system","tgbotapi"],check=True,timeout=20)
                    subprocess.run(["useradd","--system","--gid","tgbotapi",
                                    "--home-dir","/var/lib/telegram-bot-api",
                                    "--shell","/usr/sbin/nologin","tgbotapi"],
                                   check=True,timeout=20)
                subprocess.run(["chown","-R","tgbotapi:tgbotapi",
                                "/var/lib/telegram-bot-api"],check=True,timeout=120)
                Path("/var/lib/telegram-bot-api").chmod(0o700)
            subprocess.run(["systemctl","daemon-reload"],check=True,timeout=45)
            for name,path in DATABASES.items():
                src=(candidate if name=="panel" else sale_candidate if name=="sales"
                     else contents/"db"/(name+".db"))
                dest=Path(path);tmpfile=dest.with_name(dest.name+".portable-migrate-tmp")
                shutil.copyfile(src,tmpfile);tmpfile.chmod(0o600)
                for suffix in ("-wal","-shm"):
                    Path(str(dest)+suffix).unlink(missing_ok=True)
                os.replace(tmpfile,dest)
            for service in ("x-ui.service","masiha-vpn-sidecar.service","openvpn-server@masiha.service","ocserv.service"):
                if service in stopped:
                    subprocess.run(["systemctl","start",service],check=True,timeout=90)
                    if subprocess.run(["systemctl","is-active","--quiet",service]).returncode:
                        raise RuntimeError("Restored component did not start: "+service)
            for service in CUTOVER_SERVICES+("telegram-bot-api.service",):
                subprocess.run(["systemctl","disable","--now",service],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,check=False,timeout=25)
            # Check the generated Xray configuration, not only systemd's
            # outer x-ui service (which can be active with an invalid child).
            xray=Path("/usr/local/x-ui/bin/xray-linux-amd64")
            config=Path("/usr/local/x-ui/bin/config.json")
            if xray.is_file() and config.is_file():
                check=subprocess.run([str(xray),"run","-test","-c",str(config)],
                                     capture_output=True,timeout=40,check=False)
                if check.returncode:
                    raise RuntimeError("New Xray configuration failed validation")
            # Telegram bot and publication workers require a separate cutover.
            return report
        except Exception:
            # Stop restarted consumers before reverting SQLite and remove stale
            # WAL/SHM that could otherwise replay post-restore transactions.
            for service in reversed(RESTART_SERVICES):
                if service in stopped:
                    subprocess.run(["systemctl","stop",service],check=False,
                                   timeout=60,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for name,path in DATABASES.items():
                dest=Path(path)
                for suffix in ("-wal","-shm"):
                    Path(str(dest)+suffix).unlink(missing_ok=True)
            for name,path in DATABASES.items():
                original=rollback/(name+".db")
                if original.exists():
                    shutil.copyfile(original,path)
            for item in reversed(metadata):
                dest=Path(item["path"])
                if item["existed"]:
                    previous=rollback/"files"/item["path"].lstrip("/")
                    shutil.copyfile(previous,dest)
                else:
                    dest.unlink(missing_ok=True)
            for service in RESTART_SERVICES:
                if service in stopped:
                    subprocess.run(["systemctl","restart",service],check=False,
                                   timeout=60,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            raise
