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
        self.assertNotIn('systemctl enable masiha-vpn-extension',script)
        for forbidden in ('--confirm-restore','restore_database','restore --portable',
                          'touch /etc/x-ui/x-ui.db','sqlite3 /etc/x-ui/x-ui.db'):
            self.assertNotIn(forbidden,script)
        self.assertIn('DATA_NOT_IMPORTED_YET=',script)
        self.assertIn('flock -n 9',script)
        self.assertIn('ROLLBACK_PATH=',script)
        self.assertIn('prepare-sidecar-source.sh',script)
        self.assertIn('BINARY_SWAPPED=1',script)
        self.assertIn('merge-sidecar-nginx.py',script)
        self.assertIn('masiha-vpn-sidecar.service',script)
        self.assertIn('systemctl enable --now masiha-vpn-sidecar.service',script)
        self.assertIn('Masiha sidecar health test failed',script)
        self.assertIn('Masiha sales-bot redirect check failed',script)

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

    def test_missing_webroot_is_allowed_in_update_preflight(self):
        updater=self.read('deploy/update-existing.sh')
        self.assertIn('WEBROOT_MISSING_WILL_CREATE=',updater)
        self.assertIn('WEB_PREEXISTED=0',updater)
        self.assertIn('WEBROOT_CREATED=$WEB',updater)
        self.assertIn('Masiha UI HTTP check failed',updater)
        self.assertIn('if [[ "$WEB_PREEXISTED" == 1 ]];then',updater)
        self.assertIn('elif [[ "$WEB_PREEXISTED" == 0 ]]',updater)
        self.assertNotIn("Masiha web extension directory is missing.",updater)
        # Use a copied script in the same deploy/ folder to preserve ROOT.
        # --check only acquires the lock and checks preconditions; it does not
        # touch live webroots, binaries, or any database.
        if os.geteuid()!=0 or not Path('/usr/local/x-ui/x-ui').exists():
            self.skipTest('Requires installed root-owned test host')
        with tempfile.TemporaryDirectory(prefix="masiha-webroot-fixture-") as tmp:
            root=Path(tmp)
            fake=root/'absent-static-ui'
            script=BASE/'deploy'/'.update-missing-webroot-fixture.sh'
            try:
                script.write_text(updater.replace(
                    'WEB=/var/www/masiha-ui',
                    'WEB='+str(fake)))
                run=subprocess.run(['bash',str(script),'--check'],
                                   capture_output=True,text=True,timeout=20)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertIn('WEBROOT_MISSING_WILL_CREATE='+str(fake),run.stdout)
                self.assertIn('MASIHA_UPDATE_CHECK_OK',run.stdout)
                self.assertFalse(fake.exists())
                fake.symlink_to(root/'target')
                run=subprocess.run(['bash',str(script),'--check'],
                                   capture_output=True,text=True,timeout=20)
                self.assertNotEqual(run.returncode,0)
                self.assertIn('Unexpected webroot type',run.stderr)
            finally:
                script.unlink(missing_ok=True)

    def test_update_installs_and_refreshes_sidecar_nginx_routes_idempotently(self):
        import importlib.util
        helper_path=BASE/'deploy/merge-sidecar-nginx.py'
        spec=importlib.util.spec_from_file_location('merge_sidecar_nginx',helper_path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        bundle=BASE/'sidecar/nginx-locations.conf'
        original='''server {
  listen 443 ssl;
  location /sub/ { proxy_pass http://127.0.0.1:2096; }
  location / {
    proxy_pass http://127.0.0.1:62053;
    proxy_set_header Upgrade $http_upgrade;
  }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'site.conf'
            target.write_text(original)
            module.merge(target,bundle)
            first=target.read_text()
            self.assertIn('location ^~ /masiha-ui/',first)
            self.assertIn('reinspect',first)
            self.assertIn('sub_filter_once on;',first)
            module.merge(target,bundle)
            second=target.read_text()
            self.assertEqual(first,second)
            self.assertEqual(second.count('location ^~ /masiha-ui/'),1)
            self.assertEqual(second.count('MASIHA-SIDECAR BEGIN'),1)

    def test_installer_and_updater_keep_data_separate(self):
        install=self.read('deploy/install-ubuntu-24.04.sh')
        update=self.read('deploy/update-existing.sh')
        self.assertIn('Existing installation detected at',install)
        self.assertNotIn('install-ubuntu-24.04.sh',update)
        self.assertIn('BACKUP_RESTORE_NOT_STARTED=YES',update)
        self.assertIn('MIGRATION_DATABASES_NOT_TOUCHED=YES',update)

if __name__=='__main__':
    unittest.main()
