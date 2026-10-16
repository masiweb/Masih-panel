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
  self.source_patch.start();self.db_patch.start();self.domain_patch.start()
 def tearDown(self):
  self.domain_patch.stop();self.db_patch.stop();self.source_patch.stop()
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
