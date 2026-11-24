"""Regression tests for independent bundle-plugin packaging and installation."""
import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'Integrations/OptionalPlugins/Minutes Roughcut'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # publish_plugin imports sparkle_tools at module load time; inject a stub.
    if name == 'publish_plugin':
        stub = type(sys)('sparkle_tools')
        stub.ensure_tools = lambda: ROOT / '.build/sparkle-2.10.1/bin'
        stub.KEYCHAIN_ACCOUNT = 'test'
        sys.modules['sparkle_tools'] = stub
    spec.loader.exec_module(module)
    return module


class BundlePackagingTests(unittest.TestCase):
    def setUp(self):
        self.publisher = load('publish_plugin')

    def test_container_is_deterministic_and_excludes_installer(self):
        first, version = self.publisher.build_bundle(BUNDLE)
        second, _ = self.publisher.build_bundle(BUNDLE)
        self.assertEqual(first, second)
        self.assertEqual(version, '0.2.0')
        document = json.loads(first)
        self.assertEqual(document['format'], 1)
        self.assertEqual(document['id'], 'minutes')
        self.assertEqual(document['version'], version)
        self.assertNotIn('install.py', document['files'])
        for required in self.publisher.BUNDLE_REQUIRED:
            self.assertIn(required, document['files'])
        for name in document['files']:
            self.assertFalse(name.startswith('/'))
            self.assertNotIn('..', name.split('/'))
            self.assertTrue(base64.b64decode(document['files'][name], validate=True))

    def test_missing_version_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for item in self.publisher.BUNDLE_REQUIRED:
                path = directory / item
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            with self.assertRaises(Exception):
                self.publisher.build_bundle(directory)

    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'plugin.json').write_text(json.dumps({'id': 'minutes', 'version': 'latest'}))
            with self.assertRaises(ValueError):
                self.publisher.build_bundle(directory)

    def test_installer_writes_version_marker_in_isolated_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'home'
            home.mkdir()
            environment = {**os.environ, 'HOME': str(home),
                           'MINUTES_INSTALL_SOURCE': str(BUNDLE)}
            subprocess.run(['/usr/bin/python3', str(BUNDLE / 'install.py')],
                           env=environment, check=True, capture_output=True)
            support = home / 'Library/Application Support/Minutes Roughcut'
            marker = json.loads((support / 'installed.json').read_text())
            self.assertEqual(marker['version'], '0.2.0')
            self.assertEqual(marker['id'], 'minutes')
            self.assertTrue((support / 'extension/manifest.json').is_file())
            self.assertTrue((home / 'Library/Application Support/Google/Chrome/NativeMessagingHosts/'
                             'com.minutesroughcut.bridge.json').is_file())
            self.assertTrue((home / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                             'Fusion/Scripts/Utility/Minutes Roughcut.py').is_file())
            # A second install must remain idempotent and keep the marker.
            subprocess.run(['/usr/bin/python3', str(BUNDLE / 'install.py')],
                           env=environment, check=True, capture_output=True)
            self.assertEqual(json.loads((support / 'installed.json').read_text())['version'], '0.2.0')


if __name__ == '__main__':
    unittest.main()
