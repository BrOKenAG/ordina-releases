"""Protocol-level regression tests shared by every release tool."""
import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plugin_protocol as pp  # noqa: E402


def meta(**overrides):
    value = {
        'kind': 'bundle',
        'version': '1.0.0',
        'name': 'Demo Notes',
        'summary': 'A plugin the client has never seen',
        'symbol': 'note.text',
        'minClientVersion': '1.9.6',
        'protocol': 2,
        'capabilities': ['resolve.utilityScript'],
    }
    value.update(overrides)
    return value


def file_entry(path='demo.py', data=b'print(1)\n', role='utilityScript',
               install='demo.py', executable=None):
    entry = {
        'path': path,
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
        'role': role,
    }
    if install is not None:
        entry['installName'] = install
    if executable is not None:
        entry['executable'] = executable
    return entry


def bundle_files():
    return [
        file_entry('plugin.json', b'{}', role='metadata', install=None),
        file_entry('demo.py'),
    ]


class DescriptorTests(unittest.TestCase):
    def test_descriptor_is_derived_from_flat_metadata(self):
        value = pp.descriptor('demo', meta(files=None))
        self.assertEqual(value['id'], 'demo')
        self.assertEqual(value['protocol'], 2)
        self.assertEqual(value['capabilities'], ['resolve.utilityScript'])

    def test_utility_script_is_recorded_for_migration(self):
        value = pp.descriptor('demo', meta(), files=bundle_files())
        self.assertEqual(value['utilityScript'], 'demo.py')

    def test_missing_presentation_fields_are_rejected(self):
        for key in ('name', 'summary', 'symbol', 'minClientVersion', 'capabilities'):
            with self.subTest(key=key):
                broken = meta()
                broken.pop(key)
                with self.assertRaises(ValueError):
                    pp.validate_descriptor('demo', pp.descriptor('demo', broken))

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(ValueError):
            pp.validate_descriptor('demo', pp.descriptor(
                'demo', meta(capabilities=['shell.run'])))

    def test_empty_or_duplicate_capabilities_are_rejected(self):
        for capabilities in ([], ['resolve.utilityScript', 'resolve.utilityScript']):
            with self.subTest(capabilities=capabilities):
                with self.assertRaises(ValueError):
                    pp.validate_descriptor('demo', pp.descriptor(
                        'demo', meta(capabilities=capabilities)))

    def test_protocol_must_be_v2(self):
        with self.assertRaises(ValueError):
            pp.validate_descriptor('demo', pp.descriptor('demo', meta(protocol=3)))

    def test_support_directory_cannot_escape(self):
        for value in ('../escape', 'a/b', '.hidden'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pp.validate_descriptor('demo', pp.descriptor(
                        'demo', meta(supportDirectoryName=value)))

    def test_legacy_names_allow_unicode_but_not_paths(self):
        accepted = pp.descriptor('demo', meta(legacyNames=['导入阅流.py']))
        pp.validate_descriptor('demo', accepted)
        for value in ('../escape.py', '/abs.py', '.hidden.py', 'x.txt'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pp.validate_descriptor('demo', pp.descriptor(
                        'demo', meta(legacyNames=[value])))

    def test_plugin_id_must_be_bounded(self):
        for value in ('../evil', 'Demo', 'a' * 40, ''):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pp.validate_descriptor(value, pp.descriptor(value, meta()))


class InventoryTests(unittest.TestCase):
    def test_bundle_inventory_requires_metadata_and_one_script(self):
        self.assertTrue(pp.validate_files('demo', 'bundle', bundle_files()))
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'bundle', [file_entry()])
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'bundle', [
                file_entry('plugin.json', b'{}', role='metadata', install=None),
                file_entry('a.py'), file_entry('b.py')])

    def test_script_inventory_is_exactly_one_utility_script(self):
        self.assertTrue(pp.validate_files('demo', 'script', [file_entry()]))
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'script', bundle_files())

    def test_inventory_rejects_traversal_and_bad_hashes(self):
        for path in ('../escape.py', '/absolute.py', '.hidden', 'a/b/c/d/e.py',
                     'a\\b.py', 'a:b.py'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    pp.validate_files('demo', 'script', [file_entry(path)])
        broken = file_entry()
        broken['sha256'] = 'xyz'
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'script', [broken])
        broken = file_entry()
        broken['size'] = 0
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'script', [broken])
        broken = file_entry(install='../evil.py')
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'script', [broken])
        broken = file_entry(role='shell')
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'script', [broken])

    def test_duplicate_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            pp.validate_files('demo', 'bundle', bundle_files() + [file_entry('demo.py')])


class ManifestTests(unittest.TestCase):
    def entry(self, **overrides):
        value = {
            'version': '1.0.0',
            'url': 'https://github.com/BrOKenAG/ordina-plugin-demo/releases/download/'
                   'plugin-demo-v1.0.0/demo.json',
            'sha256': 'a' * 64,
            'size': 100,
            'kind': 'bundle',
            'descriptor': pp.descriptor('demo', meta(), files=bundle_files()),
            'files': bundle_files(),
        }
        value.update(overrides)
        return value

    def test_valid_manifest_entry(self):
        self.assertTrue(pp.validate_manifest_entry('demo', self.entry()))

    def test_version_and_kind_must_match_descriptor(self):
        broken = self.entry()
        broken['version'] = '1.0.1'
        with self.assertRaises(ValueError):
            pp.validate_manifest_entry('demo', broken)
        broken = self.entry(kind=None)
        broken['descriptor'] = dict(broken['descriptor'], kind='bundle')
        with self.assertRaises(ValueError):
            pp.validate_manifest_entry('demo', broken)

    def test_missing_descriptor_or_inventory_is_rejected(self):
        broken = self.entry()
        del broken['descriptor']
        with self.assertRaises(ValueError):
            pp.validate_manifest_entry('demo', broken)
        broken = self.entry()
        del broken['files']
        with self.assertRaises(ValueError):
            pp.validate_manifest_entry('demo', broken)

    def test_digest_and_size_are_required(self):
        for key, value in (('sha256', 'xyz'), ('size', 0), ('size', 5_000_000)):
            with self.subTest(key=key):
                broken = self.entry()
                broken[key] = value
                with self.assertRaises(ValueError):
                    pp.validate_manifest_entry('demo', broken)


class FeedTests(unittest.TestCase):
    def test_registry_carries_descriptor_without_asset_secrets(self):
        import build_feeds as bf
        plugins = bf.load_source()
        registry, legacy, per_plugin = bf.documents(plugins)
        for name, descriptor in registry['plugins'].items():
            for leaked in ('sha256', 'size', 'asset', 'source', 'url', 'files'):
                self.assertNotIn(leaked, descriptor)
            self.assertEqual(descriptor['protocol'], 2)
            self.assertTrue(descriptor['capabilities'])
        for name, document in per_plugin.items():
            entry = document['plugins'][name]
            self.assertEqual(entry['descriptor']['id'], name)
            self.assertTrue(entry['descriptor']['utilityScript'])
            self.assertTrue(entry['files'])
        self.assertEqual(set(legacy['plugins']), set(registry['plugins']))

    def test_new_plugin_metadata_can_be_registered(self):
        import json
        import tempfile
        import publish_plugin as publish
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'plugin.json').write_text(json.dumps({
                'id': 'demo-notes', 'name': 'Demo Notes', 'version': '1.0.0',
                'summary': 'x', 'symbol': 'note.text', 'minClientVersion': '1.9.6',
                'protocol': 2, 'capabilities': ['resolve.utilityScript'],
                'files': {},
            }), encoding='utf-8')
            value = publish.register_plugin('demo-notes', directory)
        self.assertEqual(value['repository'], 'BrOKenAG/ordina-plugin-demo-notes')
        self.assertEqual(value['feed'], 'https://brokenag.github.io/ordina-plugin-demo-notes/manifest-v2.json')
        self.assertEqual(value['name'], 'Demo Notes')
        self.assertEqual(value['protocol'], 2)


if __name__ == '__main__':
    unittest.main()

class NativeCapabilityTests(unittest.TestCase):
    def test_native_capability_requires_new_client(self):
        value = pp.descriptor('demo', meta(capabilities=['resolve.utilityScript', 'macos.application'], minClientVersion='1.10.0'))
        pp.validate_descriptor('demo', value)
        value['minClientVersion'] = '1.9.6'
        with self.assertRaises(ValueError): pp.validate_descriptor('demo', value)

    def test_native_inventory_requires_single_app_root(self):
        files = bundle_files() + [file_entry('Example.app/Contents/Info.plist', b'plist', role='application', install=None),
                                  file_entry('Example.app/Contents/MacOS/Example', b'binary', role='application', install=None, executable=True)]
        pp.validate_files('demo', 'bundle', files)
        files.append(file_entry('Other.app/Contents/Info.plist', b'other', role='application', install=None))
        with self.assertRaises(ValueError): pp.validate_files('demo', 'bundle', files)
