#!/usr/bin/env python3
"""Isolated admin backup deletion regression: no production files touched."""
import fcntl
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("masiha_sidecar_test",ROOT/"sidecar/server.py")
side=importlib.util.module_from_spec(spec)
spec.loader.exec_module(side)

class BackupDeletionTests(unittest.TestCase):
    def create(self,path,contents=b"encrypted test backup"):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o600)
        return path

    def test_delete_one_local_archive_and_hash_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            name="masiha-full-vpn.example.com-20261107T010101Z.tar.gz.gpg"
            archive=self.create(root/name)
            digest=self.create(root/(name+".sha256"))
            key=self.create(root/"backup-secret.key",b"must-stay")
            other=self.create(root/"masiha-full-vpn.example.com-20261107T020202Z.tar.gz.gpg")
            side.delete_backup_file(root,"local",name=name)
            self.assertFalse(archive.exists())
            self.assertFalse(digest.exists())
            self.assertEqual(key.read_bytes(),b"must-stay")
            self.assertTrue(other.exists())

    def test_delete_received_archive_and_its_specific_key(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            job="a"*32
            archive=self.create(root/"web-imports"/(job+".gpg"))
            key=self.create(root/"web-imports"/(job+".key"))
            state=self.create(root/"web-jobs"/(job+".json"),b'{"state":"failed"}')
            globalkey=self.create(root/"backup-secret.key")
            side.delete_backup_file(root,"received",job=job)
            self.assertFalse(archive.exists())
            self.assertFalse(key.exists())
            self.assertFalse(state.exists())
            self.assertTrue(globalkey.exists())

    def test_reject_path_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaises(ValueError):
                side.delete_backup_file(root,"local",name="../etc/passwd")
            with self.assertRaises(ValueError):
                side.delete_backup_file(root,"received",job="../x")
            name="masiha-full-vpn.example.com-20261107T010101Z.tar.gz.gpg"
            source=self.create(root/"outside-test-file")
            (root/name).symlink_to(source)
            with self.assertRaises(FileNotFoundError):
                side.delete_backup_file(root,"local",name=name)
            self.assertTrue(source.exists())

    def test_reject_delete_during_active_operation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            name="masiha-full-vpn.example.com-20261107T010101Z.tar.gz.gpg"
            archive=self.create(root/name)
            with (root/"web-work.lock").open("a+b") as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    side.delete_backup_file(root,"local",name=name)
            self.assertTrue(archive.exists())

if __name__=="__main__":
    unittest.main()
