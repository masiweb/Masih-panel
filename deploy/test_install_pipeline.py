"""Regression guards for Git-checkout -> fresh installer -> independent UI.

Read-only tests: NEVER execute the fresh-host installer on production.
"""
import ast
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent

def file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class GitInstallerTests(unittest.TestCase):
    def test_bash_syntax_and_complete_repository_files(self):
        scripts = (
            "deploy/install-ubuntu-24.04.sh",
            "deploy/finalize-ubuntu-24.04.sh",
            "deploy/finish-install.sh",
            "deploy/install-sidecar.sh",
            "deploy/verify-sidecar-menu.sh",
            "deploy/release-preflight.sh",
            "deploy/build-3x-ui.sh",
        )
        subprocess.run(["bash", "-n", *[str(ROOT / x) for x in scripts]], check=True)
        for path in (
            "sidecar/server.py",
            "sidecar/nginx-locations.conf",
            "sidecar/nav.js",
            "sidecar/nav.css",
            "sidecar/test_nav.cjs",
            "sidecar/masiha-vpn-sidecar.service",
            "sidecar/masiha-standalone.tsx",
            "sidecar/masiha-vite.config.mjs",
            "runtime/manager.py",
            "runtime/sales.py",
            "runtime/unified.py",
            "deploy/3x-ui.patch",
            "deploy/full-backup.py",
        ):
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertGreater(len(file(path)), 100, path)

    def test_fresh_install_preserves_existing_live_host_and_checks_domain(self):
        s = file("deploy/install-ubuntu-24.04.sh")
        self.assertIn('Only fresh Ubuntu 24.04 amd64 is supported', s)
        self.assertIn('Existing installation detected', s)
        self.assertIn('This release requires PANEL_PATH=/xui/', s)
        self.assertLess(s.index('getent ahostsv4'), s.index('apt-get update'))
        self.assertIn('source /etc/os-release', s)
        self.assertIn('chmod 0600 /etc/x-ui/install-result.env', s)
        self.assertIn('chmod 0600 /root/masiha-upstream-install.log', s)
        self.assertIn('bash "$REPO_ROOT/deploy/finalize-ubuntu-24.04.sh"', s)
        self.assertNotIn("MASIHA VPN INSTALLED SUCCESSFULLY", s)

    def test_current_pinned_upstream_toolchain_and_full_backup_source(self):
        install = file("deploy/install-ubuntu-24.04.sh")
        build = file("deploy/build-3x-ui.sh")
        patch = file("deploy/3x-ui.patch")
        self.assertIn('PIN="c621ba9014a6aa21fd7c9ecf45655e44e24ebf13"', build)
        self.assertIn("go1.27.3.linux-amd64.tar.gz", install)
        self.assertIn("65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40", install)
        self.assertIn("node-v26.13.0-linux-x64.tar.xz", install)
        self.assertIn("d28a96af6c38596f6d9d97c0ee531c13d238d757742387d1928de822d959e997", install)
        self.assertIn("source-patch.sha256", build)
        self.assertIn("frontend-source.sha256", build)
        self.assertIn("frontend_source_hash()", build)
        for package in ("strongswan", "strongswan-swanctl", "xl2tpd", "ppp",
                        "libpcre2-8-0", "iproute2", "ffmpeg",
                        "libchromaprint-tools", "python3-numpy", "python3-scipy"):
            self.assertIn(package, install)
        self.assertIn('install -m 0644 "$REPO_ROOT/runtime/full_backup.py"', install)
        self.assertEqual(file("runtime/full_backup.py"), file("deploy/full-backup.py"))
        self.assertIn("masihaSelfUpdateDisabledMessage", patch)
        self.assertIn("Update through the Masiha Git deployment process", patch)

    def test_private_chat_and_admin_audit_deployed_with_fresh_install(self):
        install = file("deploy/install-ubuntu-24.04.sh")
        finish = file("deploy/finish-install.sh")
        for module in ("ai_admin_audit.py","channel_manager.py","channel_manager_worker.py"):
            self.assertIn(module, install)
        for module in ("test_channel_manager.py","test_ai_admin_audit.py"):
            self.assertIn(module, install)
        self.assertIn('bash "$ROOT/deploy/install-channel-manager.sh"', finish)

    def test_no_early_success_until_sidecar(self):
        finalize = file("deploy/finalize-ubuntu-24.04.sh")
        finish = file("deploy/finish-install.sh")
        self.assertLess(finalize.index('rm -f "$FINALIZE_MARKER"'),
                        finalize.index('deploy/finish-install.sh'))
        self.assertNotIn('printf \' Username', finalize)
        self.assertLess(finish.index('deploy/install-sidecar.sh'),
                        finish.index('deploy/verify-sidecar-menu.sh'))
        self.assertLess(finish.index('deploy/verify-install.sh'),
                        finish.index("print(' Admin username"))
        self.assertLess(finish.index('deploy/verify-sidecar-menu.sh'),
                        finish.index("print(' Admin password"))
        self.assertIn('finish-install.sh --resume', finalize)
        self.assertIn('cmp -s "$BUILD_ROOT/x-ui"', finish)
        self.assertIn("MASIHA_EXPECT_DEFAULT_INBOUNDS=1", finish)

    def test_terminal_credentials_script_is_parseable_and_secure(self):
        f = file("deploy/finish-install.sh")
        start = f.index("from pathlib import Path", f.index("python3 - "))
        end = f.index("\nPY", start)
        ast.parse(f[start:end])
        self.assertIn("stat -c '%u:%a'", f)
        self.assertIn("shlex.split", f)
        self.assertNotIn("source $CREDS", f)
        self.assertIn("Admin username", f)
        self.assertIn("Admin password", f)
        self.assertIn("Masiha management", f)
        self.assertIn("Recovery file", f)

    def test_native_backup_does_not_capture_sidecar_upload(self):
        install = file("deploy/install-ubuntu-24.04.sh")
        exact = re.search(
            r'location = \$\{PANEL_PATH\}panel/api/masiha-backup/import \{(.*?)\n  \}',
            install, re.S)
        self.assertIsNotNone(exact)
        self.assertIn('proxy_pass http://127.0.0.1:61794;', exact.group(1))
        self.assertIn('MASIHA-SIDECAR BEGIN', file('sidecar/nginx-locations.conf'))
        self.assertIn('masiha-backup/', file('sidecar/nginx-locations.conf'))
        self.assertIn('legacy proxy', file('deploy/install-sidecar.sh'))

    def test_all_admin_modules_and_update_safe_navigation(self):
        ui = file('sidecar/masiha-standalone.tsx')
        for text in ('SalesBotPage','VPNPage','BackupRestorePanel','openvpn','openconnect','bot','backup'):
            self.assertIn(text, ui)
        installer=file('deploy/install-sidecar.sh')
        self.assertIn('MASIHA_BUILD_ROOT',installer)
        self.assertIn('install -m 0644 "$SIDE/nav.js"',installer)
        self.assertIn('install -m 0644 "$SIDE/nav.css"',installer)
        self.assertIn('sub_filter "</body>"', installer)
        bridge=file('sidecar/nav.js')
        self.assertIn('MutationObserver',bridge)
        self.assertIn('masiha-sidecar-menu-item',bridge)
        self.assertIn('OpenConnect',bridge)
        self.assertIn('OpenVPN',bridge)
        self.assertIn("backup",bridge)

if __name__ == '__main__':
    unittest.main()
