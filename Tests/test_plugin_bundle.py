"""Regression tests for independent bundle-plugin packaging and installation."""
import base64
import hashlib
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
        first, version, files = self.publisher.build_bundle(BUNDLE)
        second, _, again = self.publisher.build_bundle(BUNDLE)
        self.assertEqual(first, second)
        self.assertEqual(version, json.loads((BUNDLE / 'plugin.json').read_text())['version'])
        self.assertEqual(files, again)
        document = json.loads(first)
        self.assertEqual(document['format'], 1)
        self.assertEqual(document['id'], 'minutes')
        self.assertEqual(document['version'], version)
        self.assertNotIn('install.py', document['files'])
        roles = {item['path']: item['role'] for item in files}
        self.assertEqual(roles['plugin.json'], 'metadata')
        self.assertEqual(roles['Minutes Roughcut.py'], 'utilityScript')
        self.assertEqual(roles['host.command'], 'nativeMessagingHost')
        self.assertEqual(roles['chrome/com.minutesroughcut.bridge.json'],
                         'nativeMessagingManifest')
        self.assertEqual(roles['extension/manifest.json'], 'extension')
        self.assertEqual(roles['extension/icons/logo.png'], 'extension')
        for item in files:
            self.assertFalse(item['path'].startswith('/'))
            self.assertNotIn('..', item['path'].split('/'))
            data = base64.b64decode(document['files'][item['path']], validate=True)
            self.assertTrue(data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
            self.assertEqual(len(data), item['size'])

    def test_bundle_can_use_a_plugin_id_the_client_never_knew(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'demo notes'
            directory.mkdir()
            (directory / 'plugin.json').write_text(json.dumps({
                'id': 'demo-notes',
                'name': 'Demo Notes',
                'version': '1.0.0',
                'summary': 'A plugin the client has never seen',
                'symbol': 'note.text',
                'minClientVersion': '1.9.6',
                'protocol': 2,
                'capabilities': ['resolve.utilityScript'],
                'files': {'Demo Notes.py': {'role': 'utilityScript'}},
            }), encoding='utf-8')
            (directory / 'Demo Notes.py').write_text('PLUGIN_VERSION = "v1.0.0"\n')
            data, version, files = self.publisher.build_bundle(directory, expected_id='demo-notes')
            self.assertEqual(version, '1.0.0')
            self.assertEqual(json.loads(data)['id'], 'demo-notes')
            self.assertEqual([item['role'] for item in files],
                             ['utilityScript', 'metadata'])

    def test_undeclared_file_role_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'plugin.json').write_text(json.dumps({
                'id': 'demo', 'name': 'Demo', 'version': '1.0.0',
                'summary': 'demo', 'symbol': 'doc', 'minClientVersion': '1.9.6',
                'capabilities': ['resolve.utilityScript'],
                'files': {'Demo.py': {'role': 'utilityScript'}},
            }), encoding='utf-8')
            (directory / 'Demo.py').write_text('x')
            (directory / 'extra.sh').write_text('#!/bin/sh')
            with self.assertRaises(ValueError):
                self.publisher.build_bundle(directory)

    def test_missing_utility_script_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'plugin.json').write_text(json.dumps({
                'id': 'demo', 'name': 'Demo', 'version': '1.0.0',
                'summary': 'demo', 'symbol': 'doc', 'minClientVersion': '1.9.6',
                'capabilities': ['resolve.utilityScript'], 'files': {},
            }), encoding='utf-8')
            with self.assertRaises(ValueError):
                self.publisher.build_bundle(directory)

    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'plugin.json').write_text(json.dumps({
                'id': 'minutes', 'name': 'Minutes', 'version': 'latest',
                'summary': 'x', 'symbol': 'doc', 'minClientVersion': '1.9.6',
                'capabilities': ['resolve.utilityScript'],
            }), encoding='utf-8')
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
            expected = json.loads((BUNDLE / 'plugin.json').read_text())['version']
            self.assertEqual(marker['version'], expected)
            self.assertEqual(marker['id'], 'minutes')
            self.assertTrue((support / 'extension/manifest.json').is_file())
            self.assertTrue((home / 'Library/Application Support/Google/Chrome/NativeMessagingHosts/'
                             'com.minutesroughcut.bridge.json').is_file())
            self.assertTrue((home / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                             'Fusion/Scripts/Utility/Minutes Roughcut.py').is_file())
            # A second install must remain idempotent and keep the marker.
            subprocess.run(['/usr/bin/python3', str(BUNDLE / 'install.py')],
                           env=environment, check=True, capture_output=True)
            self.assertEqual(
                json.loads((support / 'installed.json').read_text())['version'], expected)


if __name__ == '__main__':
    unittest.main()
