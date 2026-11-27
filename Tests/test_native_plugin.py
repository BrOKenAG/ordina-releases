import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dev_zip
import publish_plugin

class NativePackageTests(unittest.TestCase):
    def test_zip_and_packaging_verify_native_app(self):
        with tempfile.TemporaryDirectory(prefix='ordina-native-package-') as temporary:
            root = Path(temporary); bundle = root/'source'; app = bundle/'Example.app'; macos = app/'Contents/MacOS'
            macos.mkdir(parents=True)
            shutil.copyfile('/usr/bin/true', macos/'Example'); (macos/'Example').chmod(0o700)
            resources = app/'Contents/Resources'; resources.mkdir()
            (resources/'fixture.bin').write_bytes(os.urandom(1_100_000))
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'test.ordina.fixture','CFBundleExecutable':'Example','CFBundleName':'Example','CFBundlePackageType':'APPL','CFBundleVersion':'1'}))
            subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)], check=True, capture_output=True)
            (bundle/'entry.py').write_text('# resolve')
            files = {'entry.py': {'role':'utilityScript'}}
            for path in app.rglob('*'):
                if path.is_file():
                    files[path.relative_to(bundle).as_posix()] = {'role':'application','executable':path.name == 'Example'}
            meta = {'id':'native-demo','name':'Native Demo','summary':'test','symbol':'puzzlepiece','version':'1.0.0','minClientVersion':'1.10.0','protocol':2,'kind':'bundle','capabilities':['resolve.utilityScript','macos.application'],'files':files}
            (bundle/'plugin.json').write_text(json.dumps(meta))
            archive = root/'candidate.zip'
            with zipfile.ZipFile(archive,'w') as out:
                for path in bundle.rglob('*'):
                    if path.is_file(): out.write(path, 'wrapper/' + path.relative_to(bundle).as_posix())
            with patch.dict(os.environ, {'ORDINA_DEV_LAB':'1','ORDINA_DEV_BACKEND':'lab'}):
                extracted = dev_zip.extract(archive, root/'quarantine')
                data, version, inventory = publish_plugin.build_bundle(extracted)
                self.assertEqual(version, '1.0.0'); self.assertTrue(data)
                self.assertTrue(any(item['role']=='application' for item in inventory))
                with patch.dict(os.environ, {'ORDINA_DEV_LAB':'0','ORDINA_DEV_BACKEND':'production'}):
                    production_data, _, _ = publish_plugin.build_bundle(extracted)
                    self.assertEqual(production_data, data)
                target = extracted/'Example.app/Contents/MacOS/Example'
                target.write_bytes(target.read_bytes()+b'tampered')
                with self.assertRaises(ValueError): publish_plugin.build_bundle(extracted)
