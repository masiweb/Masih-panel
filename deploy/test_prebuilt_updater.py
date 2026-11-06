#!/usr/bin/env python3
"""Offline regression: release-binary fast path skips Go compilation entirely."""
from pathlib import Path
import hashlib
import os
import subprocess
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
UPDATER=(ROOT/"deploy/update-existing.sh").read_text()
PATCH_SHA=hashlib.sha256((ROOT/"deploy/3x-ui.patch").read_bytes()).hexdigest()
PIN="c621ba9014a6aa21fd7c9ecf45655e44e24ebf13"

class PrebuiltTests(unittest.TestCase):
    def run_fixture(self,valid):
        with tempfile.TemporaryDirectory(prefix="masiha-prebuilt-test-") as tmp:
            root=Path(tmp)
            cache=root/"cache"
            assets=root/"assets"
            assets.mkdir()
            # The mock native executable is only a fixture, never installed.
            native=root/"x-ui"
            native.write_text("#!/bin/sh\nif [ \"$1\" = -v ];then echo 3.11.0;fi\n")
            native.chmod(0o755)
            arc=assets/"x-ui-linux-amd64.tar.gz"
            with tarfile.open(arc,"w:gz") as tar:
                tar.add(native,arcname="x-ui")
            h=hashlib.sha256(arc.read_bytes()).hexdigest()
            (assets/"x-ui-linux-amd64.tar.gz.sha256").write_text(
                h+"  x-ui-linux-amd64.tar.gz\n")
            (assets/"build-id.txt").write_text(PIN+" "+(PATCH_SHA if valid else "incorrect")+"\n")
            bindir=root/"bin";bindir.mkdir()
            curl=bindir/"curl"
            curl.write_text("""#!/bin/bash
set -Eeuo pipefail
out=''
url=''
while [[ $# -gt 0 ]];do
 if [[ "$1" == '-o' ]];then out="$2";shift 2
 elif [[ "$1" == -* ]];then shift
 else url="$1";shift
 fi
done
cp "$FIXTURE_ASSETS/${url##*/}" "$out"
""")
            curl.chmod(0o755)
            start=UPDATER.index("download_verified_prebuilt() {")
            end=UPDATER.index('\n[[ "$(uname -m)" == x86_64 ]]',start)
            code=UPDATER[start:end]
            runner=root/"run.sh"
            runner.write_text("""#!/bin/bash
set -Eeuo pipefail
BR="$TEST_CACHE"
PIN="$TEST_PIN"
PATCH_HASH="$TEST_PATCH_HASH"
"""+code+"""
if download_verified_prebuilt;then
  echo "FIXTURE_ACCEPTED=YES"
else
  echo "FIXTURE_ACCEPTED=NO"
fi
""")
            runner.chmod(0o755)
            env={**os.environ,
                 "TEST_CACHE":str(cache),"TEST_PIN":PIN,
                 "TEST_PATCH_HASH":PATCH_SHA,"FIXTURE_ASSETS":str(assets),
                 "PATH":str(bindir)+os.pathsep+os.environ.get("PATH","")}
            result=subprocess.run(["bash",str(runner)],env=env,
                                  capture_output=True,text=True,timeout=35)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(f"FIXTURE_ACCEPTED={'YES' if valid else 'NO'}",result.stdout)
            if valid:
                self.assertEqual((cache/"source-patch.sha256").read_text().strip(),
                                 PIN+" "+PATCH_SHA)
                self.assertEqual(subprocess.check_output(
                    [str(cache/"x-ui"),"-v"],text=True).strip(),"3.11.0")
                self.assertEqual(subprocess.run(
                    ["sha256sum","-c","--status","x-ui.sha256"],
                    cwd=cache).returncode,0)
            else:
                self.assertFalse((cache/"x-ui").exists())

    def test_verifies_prebuilt_and_prevents_go_compile(self):
        self.run_fixture(True)

    def test_rejects_wrong_release_provenance(self):
        self.run_fixture(False)

    def test_default_update_will_not_compile_go_when_release_unavailable(self):
        script=UPDATER
        self.assertIn("Precompiled x-ui is not available yet. Local Go compilation was NOT started.",script)
        self.assertIn('MASIHA_ALLOW_LOCAL_BUILD:-0',script)
        self.assertIn("Optional emergency fallback: MASIHA_ALLOW_LOCAL_BUILD=1",script)

    def test_frontend_mode_is_separate_from_backend(self):
        p=(ROOT/"deploy/build-3x-ui.sh").read_text()
        q=(ROOT/"deploy/prepare-portable-build.sh").read_text()
        self.assertIn("FRONTEND_ONLY_READY: skipped ALL Go module downloads",p)
        self.assertLess(p.index("FRONTEND_ONLY_READY"),p.index("prefetch_build_dependencies()"))
        self.assertIn("PORTABLE_FRONTEND_PREPARED=",q)
        self.assertIn("MASIHA_FRONTEND_ONLY",q)

if __name__=="__main__":
    unittest.main()
