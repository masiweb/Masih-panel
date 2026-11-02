#!/usr/bin/env python3
"""Isolated regression tests for complete encrypted migration archives."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("masiha_full_backup",
    Path(__file__).with_name("full-backup.py"))
backup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

class FullBackupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix="masiha-migration-test-")
  self.root=Path(self.tmp.name)
  self.source=self.root/"source"
  self.original_sources=backup.SOURCES
  self.original_services=backup.SERVICES
  self.original_cutover_units=backup.CUTOVER_GATED_UNITS
  self.config=self.source/"etc"/"test-app"
  self.config.mkdir(parents=True)
  self.backup_dir=self.root/"encrypted"
  self.key=self.root/"secret.key"
  self.state={"sourceA":self.config/"a.db",
              "sourceB":self.config/"b.db",
              "sourceC":self.config/"c.db"}
  self.active=[]
  for index,p in enumerate(self.state.values()):
   with sqlite3.connect(p) as db:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE accounts(id INTEGER PRIMARY KEY,name TEXT,bytes INTEGER)")
    db.execute("INSERT INTO accounts VALUES(?,?,?)",(index+1,"customer",256*(index+1)))
  (self.config/"nodes.conf").write_text("node-identity=qa\n",encoding="utf-8")
  (self.config/"server-private-key.txt").write_text("confidential",encoding="utf-8")
  self.source_patch=patch.object(backup,"SOURCES",(str(self.config),))
  self.db_patch=patch.object(backup,"DATABASES",{str(v):k for k,v in self.state.items()})
  self.domain_patch=patch.object(backup,"get_domain",return_value="vpn.example.com")
  self.services_patch=patch.object(backup,"service_states",return_value={})
  self.accounts_patch=patch.object(backup,"system_accounts",return_value={})
  self.telegram_patch=patch.object(backup,"telegram_local_api_status",return_value={
      "enabled":False,"config_present":False,"binary_present":False,
      "binary_sha256":None,"media_cache_included":False,"data_files":0,"data_bytes":0})
  self.network_patch=patch.object(backup,"network_diagnostics",return_value={})
  self.packages_patch=patch.object(backup,"package_inventory",return_value={"python3":"3.12"})
  for item in (self.source_patch,self.db_patch,self.domain_patch,self.services_patch,
               self.accounts_patch,self.telegram_patch,self.network_patch,self.packages_patch):
   item.start()
 def tearDown(self):
  for item in (self.packages_patch,self.network_patch,self.telegram_patch,self.accounts_patch,
               self.services_patch,self.domain_patch,self.db_patch,self.source_patch):
   item.stop()
  self.tmp.cleanup()
 def make(self):
  return backup.create_backup(self.backup_dir,self.key)
 def test_round_trip_all_databases_and_secrets(self):
  archive=self.make()
  self.assertTrue(archive.exists())
  self.assertTrue(str(archive).endswith(".gpg"))
  self.assertEqual(self.key.stat().st_mode&0o777,0o600)
  self.assertEqual(archive.stat().st_mode&0o777,0o600)
  self.assertNotIn(b"confidential",archive.read_bytes())
  with tempfile.TemporaryDirectory() as directory:
   mf,data=backup.unpack_verified(archive,self.key,Path(directory))
   self.assertEqual(len(mf["databases"]),3)
   self.assertIsInstance(mf["network_audit_only"],dict)
   self.assertEqual(mf["package_inventory"],{"python3":"3.12"})
   self.assertIn(str(self.config/"server-private-key.txt"),mf["files"])
   self.assertEqual({x["tables"]["accounts"] for x in mf["databases"].values()},{1})
  dest=self.root/"restored"
  backup.restore(archive,self.key,True,staging_root=dest)
  self.assertEqual((dest/str(self.config/"nodes.conf").lstrip("/")).read_text(),"node-identity=qa\n")
  self.assertEqual((dest/str(self.config/"server-private-key.txt").lstrip("/")).read_text(),"confidential")
  for source in self.state.values():
   db=dest/str(source).lstrip("/")
   with sqlite3.connect(db) as c:
    self.assertEqual(c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],1)
    self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok")
 def test_corrupt_archive_refused(self):
  archive=self.make()
  with open(archive,"r+b") as stream:
   stream.seek(32);stream.write(b"tamper")
  with tempfile.TemporaryDirectory() as directory:
   with self.assertRaisesRegex(RuntimeError,"SHA256 mismatch"):
    backup.unpack_verified(archive,self.key,Path(directory))
 def test_missing_or_insecure_key_refused(self):
  with self.assertRaisesRegex(RuntimeError,"missing"):
   backup.key_file(self.root/"missing",generate=False)
  self.key.write_text("weakpassword")
  self.key.chmod(0o644)
  with self.assertRaisesRegex(RuntimeError,"chmod 600"):
   backup.key_file(self.key)
 def test_rejects_path_traversal_and_symlink_escape(self):
  with self.assertRaisesRegex(RuntimeError,"Unsafe"):
   backup.safe_path(str(self.config/"../../outside"))
  with self.assertRaisesRegex(RuntimeError,"outside"):
   backup.safe_path("/etc/shadow")
  with self.assertRaisesRegex(RuntimeError,"Unexpected symlink"):
   backup.valid_link("/tmp/evil","/etc/shadow")
  with self.assertRaisesRegex(RuntimeError,"escapes"):
   backup.valid_link("/etc/letsencrypt/live/example/privkey.pem",
                     "../../../../etc/shadow")
 def test_restore_refuses_without_explicit_confirmation(self):
  archive=self.make()
  with self.assertRaisesRegex(RuntimeError,"--confirm-restore"):
   backup.restore(archive,self.key,False,staging_root=self.root/"restore")
 def test_staging_root_must_be_empty(self):
  archive=self.make()
  target=self.root/"occupied"
  target.mkdir()
  (target/"keep.txt").write_text("do not overwrite")
  with self.assertRaisesRegex(RuntimeError,"must be empty"):
   backup.restore(archive,self.key,True,staging_root=target)
  self.assertEqual((target/"keep.txt").read_text(),"do not overwrite")

 def test_skip_media_strips_media_rows_and_keeps_wallet_orders_and_points(self):
  from pathlib import Path
  salesdb=self.state["sourceC"]
  with sqlite3.connect(salesdb) as db:
   db.execute("CREATE TABLE media_items(id INTEGER PRIMARY KEY,title TEXT)")
   db.execute("CREATE TABLE playlists(id INTEGER PRIMARY KEY,name TEXT)")
   db.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY,price INTEGER)")
   db.execute("CREATE TABLE loyalty_points(id INTEGER PRIMARY KEY,points INTEGER)")
   db.execute("INSERT INTO media_items VALUES(1,'sample')")
   db.execute("INSERT INTO playlists VALUES(1,'playlist')")
   db.execute("INSERT INTO orders VALUES(1,200000)")
   db.execute("INSERT INTO loyalty_points VALUES(1,70)")
  with patch.object(backup,"DATABASES",{
      str(self.state["sourceA"]):"panel",
      str(self.state["sourceB"]):"users",
      str(salesdb):"sales"}):
   archive=backup.create_backup(self.backup_dir,self.key,skip_media=True)
   with tempfile.TemporaryDirectory() as directory:
    manifest,contents=backup.unpack_verified(archive,self.key,Path(directory))
    self.assertFalse(manifest["telegram_media_included"])
    with sqlite3.connect(contents/"db"/"sales.db") as db:
     self.assertEqual(db.execute("SELECT count(*) FROM media_items").fetchone()[0],0)
     self.assertEqual(db.execute("SELECT count(*) FROM playlists").fetchone()[0],0)
     self.assertEqual(db.execute("SELECT price FROM orders").fetchone()[0],200000)
     self.assertEqual(db.execute("SELECT points FROM loyalty_points").fetchone()[0],70)

 def test_local_telegram_api_files_and_media_cache_are_in_full_backup(self):
  root=self.source
  required={
   "config":root/"etc/default/telegram-bot-api",
   "service":root/"etc/systemd/system/telegram-bot-api.service",
   "autostart":root/"etc/systemd/system/masiha-telegram-api-autostart.path",
   "binary":root/"usr/local/bin/telegram-bot-api",
  }
  for name,path in required.items():
   path.parent.mkdir(parents=True,exist_ok=True)
   path.write_bytes(("fixture-"+name).encode())
  flag=root/"etc/masiha-vpn-extension/sales/telegram_local.enabled"
  flag.parent.mkdir(parents=True,exist_ok=True);flag.write_text("enabled\\n")
  data_root=root/"var/lib/telegram-bot-api"
  media=data_root/"bot123"/"file.bin"
  media.parent.mkdir(parents=True,exist_ok=True);media.write_bytes(b"telegram-media")
  required_paths=tuple(str(p) for p in required.values())
  api_status={"enabled":True,"config_present":True,"binary_present":True,
              "binary_sha256":"a"*64,"media_cache_included":True,
              "data_files":1,"data_bytes":len(b"telegram-media")}
  with (
       patch.object(backup,"SOURCES",(str(root),)),
       patch.object(backup,"TELEGRAM_REQUIRED_FILES",required_paths),
       patch.object(backup,"TELEGRAM_LOCAL_FLAG",str(flag)),
       patch.object(backup,"TELEGRAM_DATA_ROOT",str(data_root)),
       patch.object(backup,"telegram_local_api_status",return_value=api_status),
       patch.object(backup,"system_accounts",return_value={"tgbotapi":{
           "uid":998,"gid":998,"home":str(data_root),"shell":"/usr/sbin/nologin"}}),
  ):
   archive=self.make()
   with tempfile.TemporaryDirectory() as directory:
    manifest,contents=backup.unpack_verified(archive,self.key,Path(directory))
    self.assertTrue(manifest["telegram_local_api"]["enabled"])
    self.assertIn(str(required["config"]),manifest["files"])
    self.assertIn(str(required["service"]),manifest["files"])
    self.assertIn(str(required["autostart"]),manifest["files"])
    self.assertIn(str(required["binary"]),manifest["files"])
    self.assertIn(str(media),manifest["files"])
    self.assertEqual((contents/"files"/str(media).lstrip("/")).read_bytes(),b"telegram-media")
    self.assertIn("tgbotapi",manifest["system_accounts"])

 def test_skip_media_excludes_local_api_cache_but_keeps_required_runtime_files(self):
  root=self.source
  required=[]
  for rel in ("etc/default/telegram-bot-api",
              "etc/systemd/system/telegram-bot-api.service",
              "etc/systemd/system/masiha-telegram-api-autostart.path",
              "usr/local/bin/telegram-bot-api"):
   path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text("required")
   required.append(str(path))
  flag=root/"etc/masiha-vpn-extension/sales/telegram_local.enabled"
  flag.parent.mkdir(parents=True,exist_ok=True);flag.write_text("enabled")
  data_root=root/"var/lib/telegram-bot-api"
  media=data_root/"bot123"/"file.bin";media.parent.mkdir(parents=True,exist_ok=True)
  media.write_bytes(b"large media cache")
  status={"enabled":True,"config_present":True,"binary_present":True,
          "binary_sha256":"b"*64,"media_cache_included":False,
          "data_files":1,"data_bytes":len(b"large media cache")}
  with (
       patch.object(backup,"SOURCES",(str(root),)),
       patch.object(backup,"TELEGRAM_REQUIRED_FILES",tuple(required)),
       patch.object(backup,"TELEGRAM_LOCAL_FLAG",str(flag)),
       patch.object(backup,"TELEGRAM_DATA_ROOT",str(data_root)),
       patch.object(backup,"telegram_local_api_status",return_value=status),
       patch.object(backup,"system_accounts",return_value={"tgbotapi":{
           "uid":998,"gid":998,"home":str(data_root),"shell":"/usr/sbin/nologin"}}),
  ):
   archive=backup.create_backup(self.backup_dir,self.key,skip_media=True)
   with tempfile.TemporaryDirectory() as directory:
    manifest,contents=backup.unpack_verified(archive,self.key,Path(directory))
    self.assertFalse(manifest["telegram_media_included"])
    self.assertNotIn(str(media),manifest["files"])
    self.assertFalse((contents/"files"/str(media).lstrip("/")).exists())
    self.assertTrue(all(path in manifest["files"] for path in required))

 def test_inventory_covers_all_active_vpn_and_media_components(self):
  for path in ("/etc/openvpn/masiha-ros6",
               "/etc/openvpn/server/masiha-ros6.conf",
               "/etc/masiha-sstp","/opt/masiha-sstp",
               "/var/lib/masiha-audio-fp","/var/lib/telegram-bot-api",
               "/usr/local/sbin/masiha-ipsec-firewall",
               "/usr/local/sbin/masiha-sstp-network",
               "/etc/ppp/options.masiha-l2tp",
               "/etc/ppp/ip-up.d/90-masiha-ipsec",
               "/etc/ppp/ip-down.d/90-masiha-ipsec",
               "/etc/sysctl.d/93-masiha-sstp-ipv4-forward.conf"):
   self.assertIn(path,self.original_sources,path)
  for unit in ("strongswan-starter.service","xl2tpd.service",
               "openvpn-server@masiha-ros6.service","masiha-sstp.service",
               "masiha-ipsec-network.service","masiha-film-import.service",
               "masiha-channel-manager.timer","nginx.service","certbot.timer"):
   self.assertIn(unit,self.original_services,unit)
  for unit in ("masiha-vpn-extension.service","masiha-film-import.service",
               "masiha-rozmusic-import.timer","masiha-channel-manager.timer"):
   self.assertIn(unit,self.original_cutover_units,unit)
  self.assertLess(self.original_services.index("x-ui.service"),
                  self.original_services.index("masiha-vpn-extension.service"))
  self.assertLess(self.original_services.index("masiha-vpn-sidecar.service"),
                  self.original_services.index("nginx.service"))
  self.assertIn("certbot.timer",self.original_services)

 def test_services_stop_consumers_first_and_start_dependencies_first(self):
  calls=[]
  outcomes=[
   backup.subprocess.CompletedProcess([],3),
   backup.subprocess.CompletedProcess([],3),
   backup.subprocess.CompletedProcess([],0),
   backup.subprocess.CompletedProcess([],0),
  ]
  with (
   patch.object(backup,"run",side_effect=lambda *args: calls.append(args)),
   patch.object(backup.subprocess,"run",side_effect=outcomes),
  ):
   backup.stop_services(["dependency.service","worker.service"])
   backup.start_services(["dependency.service","worker.service"])
  self.assertEqual(calls,[
   ("systemctl","stop","worker.service"),
   ("systemctl","stop","dependency.service"),
   ("systemctl","start","dependency.service"),
   ("systemctl","start","worker.service"),
  ])

 def test_service_restart_attempts_every_unit_then_reports_failures(self):
  calls=[]
  outcomes=[
   backup.subprocess.CompletedProcess([],3),
   backup.subprocess.CompletedProcess([],0),
  ]
  with (
   patch.object(backup,"run",side_effect=lambda *args: calls.append(args)),
   patch.object(backup.subprocess,"run",side_effect=outcomes),
  ):
   with self.assertRaisesRegex(RuntimeError,"dependency.service did not become active"):
    backup.start_services(["dependency.service","worker.service"])
  self.assertEqual(calls,[
   ("systemctl","start","dependency.service"),
   ("systemctl","start","worker.service"),
  ])

 def test_required_packages_are_checked_for_active_services(self):
  states={"strongswan-starter.service":{"active":True,"enabled":"enabled"}}
  with self.assertRaisesRegex(RuntimeError,"strongswan"):
   backup.validate_required_packages(states,installed={"strongswan":"5.9"})
  required=backup.validate_required_packages(states,installed={name:"version" for name in backup.REQUIRED_PACKAGES_BY_UNIT["strongswan-starter.service"]})
  self.assertIn("libcharon-extra-plugins",required)

 def test_active_service_inventory_rejects_missing_required_files(self):
  missing=self.root/"missing-ipsec.conf"
  with patch.object(backup,"REQUIRED_PATHS_BY_UNIT",{"demo.service":(str(missing),)}):
   with self.assertRaisesRegex(RuntimeError,"demo.service"):
    backup.validate_source_inventory({"demo.service":{"active":True}})
   self.assertTrue(backup.validate_source_inventory({"demo.service":{"active":False}}))
 def test_restore_keeps_telegram_workers_paused_until_cutover(self):
  states={
   "x-ui.service":{"active":True,"enabled":"enabled"},
   "telegram-bot-api.service":{"active":True,"enabled":"enabled"},
   "masiha-vpn-extension.service":{"active":True,"enabled":"enabled"},
   "masiha-channel-manager.timer":{"active":True,"enabled":"enabled"},
  }
  calls=[]
  with (
       patch.object(backup,"run",side_effect=lambda *args: calls.append(args)),
       patch.object(backup,"current_enabled_state",return_value="disabled"),
       patch.object(backup.subprocess,"run",return_value=backup.subprocess.CompletedProcess([],0)),
  ):
   pending=backup.restore_service_states(states)
  self.assertIn("masiha-vpn-extension.service",pending)
  self.assertIn("masiha-channel-manager.timer",pending)
  self.assertIn(("systemctl","start","x-ui.service"),calls)
  self.assertIn(("systemctl","start","telegram-bot-api.service"),calls)
  self.assertNotIn(("systemctl","start","masiha-vpn-extension.service"),calls)
  self.assertNotIn(("systemctl","start","masiha-channel-manager.timer"),calls)

 def test_restore_can_activate_workers_only_when_explicitly_requested(self):
  states={
   "masiha-vpn-extension.service":{"active":True,"enabled":"enabled"},
   "masiha-channel-manager.timer":{"active":True,"enabled":"enabled"},
  }
  calls=[]
  with (
       patch.object(backup,"run",side_effect=lambda *args: calls.append(args)),
       patch.object(backup,"current_enabled_state",return_value="disabled"),
       patch.object(backup.subprocess,"run",return_value=backup.subprocess.CompletedProcess([],0)),
  ):
   pending=backup.restore_service_states(states,activate_telegram=True)
  self.assertEqual(pending,[])
  self.assertIn(("systemctl","start","masiha-vpn-extension.service"),calls)
  self.assertIn(("systemctl","start","masiha-channel-manager.timer"),calls)

 def test_domain_mismatch_fails_before_live_restore_changes(self):
  archive=self.make()
  with patch.object(backup,"get_domain",return_value="other.example.com"):
   with self.assertRaisesRegex(RuntimeError,"Domain mismatch"):
    backup.restore(archive,self.key,True)

 def test_quiesce_stops_and_restarts_even_on_failure(self):
  with patch.object(backup,"active_services",return_value=["x-ui.service"]) as active:
   with patch.object(backup,"stop_services") as stop:
    with patch.object(backup,"start_services") as start:
     with patch.object(backup,"copy_snapshot",side_effect=RuntimeError("simulated failure")):
      with self.assertRaisesRegex(RuntimeError,"simulated failure"):
       self.make_backup_quiesced()
     self.assertEqual(stop.call_count,1)
     self.assertEqual(start.call_count,1)
     self.assertEqual(active.call_count,1)
 def make_backup_quiesced(self):
  return backup.create_backup(self.backup_dir,self.key,quiesce=True)

if __name__=="__main__":
 unittest.main()
