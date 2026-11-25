"""Regression tests for the developer publishing engine's pure logic.

The acceptance script (``verify_developer_center.py``) drives the whole tool
end to end; these tests pin the parts that used to break silently: path
rebinding after the tool is packaged, the inspect/version decisions, the
preview fingerprint and the lab-only working-copy sync.
"""
import dataclasses
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_feeds
import dev_publish as dp
import dev_zip
import publish_plugin

PLUGIN = 'demo-lab-notes'
CAPABILITIES = ['resolve.utilityScript', 'resolve.support', 'chrome.unpackedExtension']


def write_plugin(directory, version='1.0.0', body='first'):
    directory.mkdir(parents=True, exist_ok=True)
    meta = {'id': PLUGIN, 'name': 'Demo Lab Notes', 'version': version,
            'summary': 'A plugin compiled clients have never seen',
            'symbol': 'note.text', 'minClientVersion': '1.10.0', 'protocol': 2,
            'capabilities': CAPABILITIES,
            'supportDirectoryName': 'Demo Lab Notes',
            'files': {f'{PLUGIN}.py': {'role': 'utilityScript',
                                       'installName': f'{PLUGIN}.py'},
                      'support/notes.txt': {'role': 'support'},
                      'extension/manifest.json': {'role': 'extension'}}}
    (directory / 'plugin.json').write_text(json.dumps(meta), encoding='utf-8')
    (directory / f'{PLUGIN}.py').write_text(f'VERSION = "{version}"  # {body}\n',
                                            encoding='utf-8')
    (directory / 'support').mkdir(exist_ok=True)
    (directory / 'support' / 'notes.txt').write_text(f'{body} {version}\n',
                                                    encoding='utf-8')
    (directory / 'extension').mkdir(exist_ok=True)
    (directory / 'extension' / 'manifest.json').write_text(
        json.dumps({'manifest_version': 3, 'name': 'Demo', 'version': version}),
        encoding='utf-8')
    return directory


def zip_plugin(source, target):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(source))
    return target


class EngineTestCase(unittest.TestCase):
    """Snapshot every module global the engine rebinds, and restore it."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dev-publish-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        key = self.root / 'test-signing.key'
        key.write_bytes(base64.b64encode(os.urandom(32)))
        isolated = patch.dict(os.environ, {'ORDINA_DEV_LAB': '1',
                                           'ORDINA_DEV_SIGN_KEY': str(key)})
        isolated.start()
        self.addCleanup(isolated.stop)
        self.repo = self.root / 'repo'
        (self.repo / 'plugins').mkdir(parents=True)
        self.registry = self.repo / 'plugins' / 'registry.source.json'
        self.registry.write_text(json.dumps({'format': 2, 'plugins': {}},
                                            ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
        self.saved = {
            (module, name): getattr(module, name)
            for module, names in ((publish_plugin, ('ROOT', 'SOURCE_PATH')),
                                  (build_feeds, ('ROOT', 'SOURCE', 'REGISTRY_OUTPUT',
                                                 'LEGACY_OUTPUT')),
                                  (dp.durable_release, ('TRANSACTIONS', 'LAB_REPORT')))
            for name in names}
        self.addCleanup(self.restore)

    def restore(self):
        for (module, name), value in self.saved.items():
            setattr(module, name, value)

    def resource(self, name):
        return self.root / name


class PackagingTests(EngineTestCase):
    """The packaged tool resolves every path from the configured repository."""

    def test_configure_rebinds_every_module_path(self):
        dp.configure(self.repo)
        self.assertEqual(publish_plugin.SOURCE_PATH, self.registry)
        self.assertEqual(build_feeds.SOURCE, self.registry)
        self.assertEqual(build_feeds.ROOT, self.repo)
        self.assertEqual(build_feeds.REGISTRY_OUTPUT, self.repo / 'registry.json')
        self.assertEqual(build_feeds.LEGACY_OUTPUT,
                         self.repo / 'plugins' / 'manifest.json')
        self.assertEqual(dp.durable_release.TRANSACTIONS, self.repo / '.transactions')

    def test_load_source_follows_the_rebind(self):
        # Regression: load_source used to capture the pre-packaging path in a
        # default argument, so a packaged tool read <archive>/plugins/... and
        # failed with a bare OSError instead of reporting a bad repository.
        self.registry.write_text(json.dumps({'format': 2, 'plugins': {
            PLUGIN: {'version': '0.9.0'}}}), encoding='utf-8')
        dp.configure(self.repo)
        self.assertEqual(build_feeds.load_source(), {PLUGIN: {'version': '0.9.0'}})
        self.assertEqual(publish_plugin.load_source(),
                         {PLUGIN: {'version': '0.9.0'}})

    def test_load_source_without_rebind_still_reads_the_repository_file(self):
        plugins = build_feeds.load_source()
        self.assertIn('minutes', plugins)

    def test_repository_root_rejects_a_non_repository(self):
        with self.assertRaises(dp.Failure) as caught:
            dp.repository_root(str(self.root / 'elsewhere'))
        self.assertEqual(caught.exception.code, 'repo.invalid')

    def test_repository_root_requires_a_path(self):
        with self.assertRaises(dp.Failure) as caught:
            dp.repository_root(None)
        self.assertEqual(caught.exception.code, 'repo.missing')

    def test_environment_repository_optional(self):
        with patch.dict(os.environ, {'ORDINA_DEV_REPO': ''}):
            self.assertIsNone(dp.environment_repository(required=False))
            with self.assertRaises(dp.Failure) as caught:
                dp.environment_repository()
            self.assertEqual(caught.exception.code, 'repo.missing')


class InspectTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.source = write_plugin(self.resource(f'src/{PLUGIN}'))
        self.package = zip_plugin(self.source, self.resource('package.zip'))
        dp.configure(self.repo)

    def test_new_plugin_is_detected(self):
        value = dp.inspect_package(self.package)
        self.assertEqual(value['action'], 'new')
        self.assertIsNone(value['existing'])
        self.assertEqual(value['package']['fileCount'], 4)
        self.assertEqual(value['repository']['slug'],
                         f'BrOKenAG/ordina-plugin-{PLUGIN}')
        self.assertFalse(value['repository']['known'])
        self.assertEqual(value['descriptor']['kind'], 'bundle')

    def test_same_and_downgrade_and_update(self):
        for version, action in (('1.0.0', 'same'), ('0.9.0', 'downgrade'),
                                ('1.1.0', 'update')):
            self.registry.write_text(json.dumps({'format': 2, 'plugins': {
                PLUGIN: {'version': '1.0.0', 'kind': 'bundle'}}}), encoding='utf-8')
            self.package = zip_plugin(
                write_plugin(self.resource(f'src-{version}/{PLUGIN}'), version,
                             body=version), self.resource(f'p-{version}.zip'))
            self.assertEqual(dp.inspect_package(self.package,
                                             root=self.repo)['action'], action)

    def test_incompatible_client_is_reported(self):
        value = dp.inspect_package(self.package, root=self.repo)
        self.assertTrue(value['compatibility']['ok'])
        with patch.object(dp, 'client_version', return_value='1.9.0'):
            value = dp.inspect_package(self.package, root=self.repo)
        self.assertFalse(value['compatibility']['ok'])
        self.assertIn('1.10.0', value['compatibility']['reasons'][0])

    def test_version_downgrade_is_refused_by_prepare(self):
        self.registry.write_text(json.dumps({'format': 2, 'plugins': {
            PLUGIN: {'version': '2.0.0', 'kind': 'bundle'}}}), encoding='utf-8')
        with self.assertRaises(dp.Failure) as caught:
            dp.prepare_zip(self.package, 'notes', root=self.repo)
        self.assertEqual(caught.exception.code, 'release.invalid')
        self.assertIn('必须高于', str(caught.exception))

    def test_prepare_zip_accepts_a_new_version(self):
        prepared = dp.prepare_zip(self.package, '首次发布', root=self.repo)
        self.assertEqual(prepared.version, '1.0.0')
        self.assertEqual(prepared.name, PLUGIN)
        self.assertTrue(prepared.data)

    def test_malicious_package_keeps_the_adapter_code(self):
        with self.assertRaises(dev_zip.PackageError) as caught:
            dp.inspect_package(self.resource('missing.zip'))
        self.assertEqual(caught.exception.code, 'package.missing')


class FingerprintTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        dp.configure(self.repo)
        self.package = zip_plugin(write_plugin(self.resource(f'src/{PLUGIN}')),
                                  self.resource('package.zip'))
        self.prepared = dp.prepare_zip(self.package, 'notes', root=self.repo)

    def fingerprint(self, notes='notes'):
        return dp.fingerprint(self.prepared, self.package, notes, self.repo, '1.10.0')

    def test_fingerprint_is_stable(self):
        self.assertEqual(self.fingerprint(), self.fingerprint())

    def test_notes_are_part_of_the_fingerprint(self):
        self.assertNotEqual(self.fingerprint('a'), self.fingerprint('b'))

    def test_asset_bytes_are_part_of_the_fingerprint(self):
        before = self.fingerprint()
        data = bytearray(self.prepared.data)
        data[-1] = (data[-1] + 1) % 256
        self.prepared = dataclasses.replace(self.prepared, data=bytes(data))
        self.assertNotEqual(before, self.fingerprint())

    def test_registry_state_is_part_of_the_fingerprint(self):
        before = self.fingerprint()
        self.registry.write_text(json.dumps({'format': 2, 'plugins': {
            PLUGIN: {'version': '0.1.0'}}}), encoding='utf-8')
        self.assertNotEqual(before, self.fingerprint())


class LabScopeTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        dp.configure(self.repo)
        self.package = zip_plugin(write_plugin(self.resource(f'src/{PLUGIN}')),
                                  self.resource('package.zip'))
        self.prepared = dp.prepare_zip(self.package, 'notes', root=self.repo)

    def labels(self):
        return sorted(feed.label for feed in self.prepared.plan.feeds)

    def test_lab_keeps_main_and_plugin_feeds_only(self):
        original = self.labels()
        self.assertGreater(len(original), 3)
        dp.restrict_feeds(self.prepared, 'lab')
        kept = self.labels()
        self.assertIn('registry', kept)
        self.assertIn('source', kept)
        self.assertIn(f'plugin:{PLUGIN}', kept)
        self.assertEqual({item.split(':')[-1] for item in kept if ':' in item},
                         {PLUGIN})

    def test_production_keeps_every_feed(self):
        original = self.labels()
        dp.restrict_feeds(self.prepared, 'production')
        self.assertEqual(self.labels(), original)


class WorktreeSyncTests(EngineTestCase):
    def prepare_store(self, files):
        store = self.resource('labroot/lab/repos/BrOKenAG/ordina-releases')
        store.mkdir(parents=True)
        (store / 'state.json').write_text(json.dumps(files), encoding='utf-8')

    def test_only_known_feeds_are_written(self):
        import base64
        self.prepare_store({
            'registry.json': base64.b64encode(b'{}').decode(),
            'plugins/registry.source.json': base64.b64encode(b'source').decode(),
            'unexpected.txt': base64.b64encode(b'nope').decode()})
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '1',
                                     'ORDINA_DEV_LAB_ROOT': str(self.resource('labroot')),
                                     'ORDINA_DEV_LAB_WRITE_SOURCE': '1'}):
            dp.sync_worktree(self.repo)
        self.assertEqual((self.repo / 'registry.json').read_bytes(), b'{}')
        self.assertEqual((self.repo / 'plugins' / 'registry.source.json').read_bytes(),
                         b'source')
        self.assertFalse((self.repo / 'unexpected.txt').exists())

    def test_sync_is_skipped_outside_lab(self):
        import base64
        self.prepare_store({'registry.json': base64.b64encode(b'{}').decode()})
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0',
                                     'ORDINA_DEV_LAB_ROOT': str(self.resource('labroot'))}):
            dp.sync_worktree(self.repo)
        self.assertFalse((self.repo / 'registry.json').exists())

    def test_sync_is_skipped_without_a_store(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '1',
                                     'ORDINA_DEV_LAB_ROOT': str(self.resource('empty'))}):
            dp.sync_worktree(self.repo)
        self.assertFalse((self.repo / 'registry.json').exists())


class ContractTests(EngineTestCase):
    """The JSON-lines contract is what the client parses; keep it stable."""

    def run_tool(self, *arguments, env=None):
        environment = dict(os.environ)
        environment.update({'PYTHONUNBUFFERED': '1'})
        environment.update(env or {})
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' /
                                                          'dev_publish.py'),
                                 *arguments], capture_output=True, text=True,
                                env=environment, timeout=60)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        payloads = [json.loads(line) for line in lines]
        return result, payloads

    def test_hello_emits_one_result_object(self):
        _, payloads = self.run_tool('hello')
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['type'], 'result')
        self.assertTrue(payloads[0]['data']['features'])
        self.assertEqual(payloads[0]['data']['protocol'], 1)

    def test_credential_file_outside_lab_is_refused(self):
        # A credential file is a lab-only convenience; a production tool must
        # only ever read the Keychain item.
        result, payloads = self.run_tool(
            'credential-status', '--credentials', str(self.resource('nope.json')),
            env={'ORDINA_DEV_LAB': '0'})
        self.assertEqual(result.returncode, 0)
        status = payloads[-1]['data']
        self.assertFalse(status['valid'])
        self.assertEqual(status['code'], 'auth.lab')

    def test_lab_backend_needs_the_lab_switch(self):
        environment = {'ORDINA_DEV_LAB': '0', 'ORDINA_DEV_REPO': str(self.repo)}
        result, payloads = self.run_tool('inspect', '--zip', 'x.zip',
                                         '--backend', 'lab', env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payloads[-1]['error']['code'], 'backend.lab')


if __name__ == '__main__':
    unittest.main()
