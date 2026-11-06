#!/usr/bin/env python3
"""Regression for encrypted direct server-to-server backup transfer."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"runtime"))
import backup_transfer as transfer

class ServerTransferRegression(unittest.TestCase):
    def test_ip_ssh_inputs(self):
        self.assertEqual(transfer.validate_target("212.74.39.225","root",22),
                         ("212.74.39.225","root",22))
        for host in ("localhost","127.0.0.1","0.0.0.0","::1","invalid-ip"):
            with self.subTest(host=host),self.assertRaises(ValueError):
                transfer.validate_target(host,"root",22)
        with self.assertRaises(ValueError):
            transfer.validate_target("212.74.39.225","root","22")

    def test_pinned_ssh_and_resumable_copy(self):
        raw=(ROOT/"runtime/backup_transfer.py").read_text()
        for marker in ("StrictHostKeyChecking=yes","UserKnownHostsFile=",
                       "ssh-keyscan","ssh-keygen","--append-verify",
                       "--partial","sha256sum","systemd-run",
                       "web_backup_worker.py","inspect"):
            self.assertIn(marker,raw,marker)
        self.assertNotIn("StrictHostKeyChecking=no",raw)
        self.assertNotIn("UserKnownHostsFile=/dev/null",raw)

    def test_api_ui_nginx_endpoints(self):
        paths=("sidecar/server.py","sidecar/nginx-locations.conf",
               "deploy/native-web/BackupRestorePanel.tsx","deploy/update-existing.sh")
        for path in paths:
            raw=(ROOT/path).read_text()
            with self.subTest(path=path):
                self.assertIn("transfer-probe",raw)
                self.assertIn("transfer-start",raw)
        panel=(ROOT/"deploy/native-web/BackupRestorePanel.tsx").read_text()
        self.assertIn("sshTrusted",panel)
        self.assertIn("Input.Password",panel)
        self.assertIn("destination_ready_for_restore",panel)

    def test_root_only_one_time_handoff_and_no_auto_restore(self):
        raw=(ROOT/"runtime/backup_transfer.py").read_text()
        self.assertIn('Path("/run/masiha-backup-transfer")',raw)
        self.assertIn("path.unlink()",raw)
        self.assertIn("0o600",raw)
        self.assertNotIn("--confirm-restore",raw)
        ask=(ROOT/"runtime/transfer_askpass.py").read_text()
        self.assertIn("MASIHA_SSH_PASSWORD_FILE",ask)
        self.assertIn("/run/masiha-transfer-",ask)
        self.assertIn("0o077",ask)

    def test_install_and_updater_deploy_all_parts(self):
        install=(ROOT/"deploy/install-ubuntu-24.04.sh").read_text()
        update=(ROOT/"deploy/update-existing.sh").read_text()
        self.assertIn("backup_transfer.py",install)
        self.assertIn("transfer_askpass.py",install)
        self.assertIn("runtime/*.py",update)
        self.assertIn("transfer_askpass.py",update)
        self.assertIn("0755",install)
        self.assertIn("0755",update)
        self.assertIn("transfer", (ROOT/"sidecar/server.py").read_text())

if __name__=="__main__":
    unittest.main()
