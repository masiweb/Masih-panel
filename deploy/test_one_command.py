#!/usr/bin/env python3
"""Safety regression for one-command Masiha provisioning and updates."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import os

BASE=Path(__file__).resolve().parents[1]

class OneCommandTests(unittest.TestCase):
    def read(self,relative):
        return (BASE/relative).read_text()

    def test_user_entrypoints_are_two_exact_actions(self):
        install=self.read('install.sh')
        update=self.read('update.sh')
        boot=self.read('deploy/bootstrap.sh')
        self.assertIn('bash "$TMP" install',install)
        self.assertIn('bash "$TMP" update',update)
        self.assertIn('exec bash "$REPO/deploy/install-ubuntu-24.04.sh"',boot)
        self.assertIn('exec bash "$REPO/deploy/update-existing.sh"',boot)
        self.assertIn('git -C "$REPO" merge --ff-only FETCH_HEAD',boot)
        self.assertIn('Existing installation detected; use the UPDATE',boot)
        self.assertNotIn('reset --hard',boot)
        self.assertNotIn('git push --force',boot)

    def test_update_never_requires_migration_database_or_restores_archive(self):
        script=self.read('deploy/update-existing.sh')
        self.assertIn('UPDATE_BACKUP_REQUIRED=NO',script)
        self.assertIn('UPDATE_RESTORES_DATABASES=NO',script)
        self.assertIn('MASIHA_UPDATE_OK=',script)
        self.assertNotIn('systemctl enable',script)
        for forbidden in ('--confirm-restore','restore_database','restore --portable',
                          'touch /etc/x-ui/x-ui.db','sqlite3 /etc/x-ui/x-ui.db'):
            self.assertNotIn(forbidden,script)
        self.assertIn('DATA_NOT_IMPORTED_YET=',script)
        self.assertIn('flock -n 9',script)
        self.assertIn('ROLLBACK_PATH=',script)
        self.assertIn('prepare-sidecar-source.sh',script)
        self.assertIn('BINARY_SWAPPED=1',script)

    def test_safe_source_overlays_and_pinned_build(self):
        helper=self.read('deploy/prepare-sidecar-source.sh')
        updater=self.read('deploy/update-existing.sh')
        self.assertIn('MASIHA_SIDECAR_SOURCE_READY',helper)
        self.assertIn('source-only',helper)
        self.assertIn('source-patch.sha256',updater)
        self.assertIn('3x-ui.patch',updater)
        self.assertIn('WEB_SWAPPED=1',updater)

    def test_shell_syntax_and_help_offline(self):
        files=['install.sh','update.sh','deploy/bootstrap.sh',
               'deploy/update-existing.sh','deploy/prepare-sidecar-source.sh']
        for relative in files:
            with self.subTest(relative=relative):
                completed=subprocess.run(['bash','-n',str(BASE/relative)],
                                         capture_output=True,text=True)
                self.assertEqual(completed.returncode,0,completed.stderr)
        for name in ['install.sh','update.sh']:
            completed=subprocess.run(['bash',str(BASE/name),'--help'],
                                     capture_output=True,text=True)
            self.assertEqual(completed.returncode,0,completed.stderr)

    def test_installer_and_updater_keep_data_separate(self):
        install=self.read('deploy/install-ubuntu-24.04.sh')
        update=self.read('deploy/update-existing.sh')
        self.assertIn('Existing installation detected at',install)
        self.assertNotIn('install-ubuntu-24.04.sh',update)
        self.assertIn('BACKUP_RESTORE_NOT_STARTED=YES',update)
        self.assertIn('MIGRATION_DATABASES_NOT_TOUCHED=YES',update)

if __name__=='__main__':
    unittest.main()
