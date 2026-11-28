"""Regression tests for the single-source feed derivation."""
import copy
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_feeds as bf


class FeedDerivationTests(unittest.TestCase):
    def setUp(self):
        self.plugins = bf.load_source()

    def test_source_lists_published_and_unpublished_plugins(self):
        self.assertIn('minutes', self.plugins)
        self.assertIn('multicam', self.plugins)
        self.assertTrue(self.plugins['minutes']['published'])
        self.assertFalse(self.plugins['yueliu']['published'])

    def test_registry_keeps_old_clients_compatible_with_native_capability(self):
        source = copy.deepcopy(self.plugins['minutes'])
        source['capabilities'] = ['resolve.utilityScript', 'macos.application']
        source['minClientVersion'] = '1.10.0'
        entry = bf.registry_descriptor('minutes', source)
        self.assertEqual(entry['capabilities'], ['resolve.utilityScript'])
        self.assertIn('macos.application', source['capabilities'])

    def test_registry_carries_descriptor_but_no_asset_secrets(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(registry['format'], 2)
        for name, descriptor in registry['plugins'].items():
            for leaked in ('sha256', 'size', 'asset', 'source', 'published', 'url', 'files'):
                self.assertNotIn(leaked, descriptor, f'{name} 泄漏了 {leaked}')
            required = {'repository', 'feed', 'downloadPrefix', 'kind', 'name',
                        'summary', 'symbol', 'version', 'minClientVersion',
                        'protocol', 'capabilities'}
            self.assertEqual(set(descriptor) - {'iconID'}, required)
            if 'iconID' in descriptor:
                self.assertIsInstance(descriptor['iconID'], str)
            self.assertEqual(descriptor['protocol'], 2)

    def test_legacy_feed_contains_every_published_plugin(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        expected = {name for name, meta in bf.published(self.plugins).items() if meta.get('legacy')}
        self.assertEqual(set(legacy['plugins']), expected)
        self.assertEqual(legacy['format'], 1)

    def test_each_plugin_feed_contains_only_itself(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(set(per_plugin), set(bf.published(self.plugins)))
        for name, document in per_plugin.items():
            self.assertEqual(set(document['plugins']), {name})
            entry = document['plugins'][name]
            self.assertEqual(entry['version'], registry['plugins'][name]['version'])
            self.assertEqual(entry['descriptor']['id'], name)
            self.assertTrue(entry['files'])
            # Legacy feeds are frozen at versions that still exist in the
            # original release repository, so they may lag the current one.
            if name in legacy['plugins']:
                self.assertIn('/BrOKenAG/ordina-releases/releases/download/',
                              legacy['plugins'][name]['url'])
            else:
                self.assertFalse(self.plugins[name].get('legacy'))

    def test_bundle_kind_is_only_emitted_for_bundles(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(legacy['plugins']['minutes']['kind'], 'bundle')
        self.assertNotIn('kind', legacy['plugins']['multicam'])

    def test_entry_url_is_built_from_own_repository(self):
        result = bf.entry('minutes', self.plugins['minutes'])
        self.assertEqual(result['version'], self.plugins['minutes']['version'])
        self.assertEqual(
            result['url'],
            'https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/'
            f'plugin-minutes-v{self.plugins["minutes"]["version"]}/minutes.json')
        self.assertTrue(result['url'].startswith(
            'https://github.com/' + self.plugins['minutes']['repository']))
        self.assertFalse(result['url'].startswith(
            'https://github.com/BrOKenAG/ordina-releases/releases/'))

    def test_195_and_196_receive_different_feed_paths_and_urls(self):
        with patch.object(bf, 'envelope_bytes', return_value=b'signed'):
            generated = bf.generate(self.plugins)
        for name in ('minutes', 'multicam'):
            legacy = generated['compat-plugin:' + name][0]['plugins'][name]
            current = generated['plugin:' + name][0]['plugins'][name]
            self.assertIn('/ordina-releases/releases/download/', legacy['url'])
            self.assertIn('/ordina-plugin-' + name + '/releases/download/', current['url'])
            self.assertTrue(self.plugins[name]['feed'].endswith('/manifest-v2.json'))

    def test_source_comment_is_never_published(self):
        raw = (ROOT / 'plugins' / 'registry.source.json').read_text(encoding='utf-8')
        self.assertIn('_comment', raw)
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertNotIn('_comment', registry)
        self.assertNotIn('_comment', legacy)

    def test_missing_field_is_rejected(self):
        broken = copy.deepcopy(self.plugins)
        del broken['minutes']['sha256']
        with self.assertRaises(ValueError):
            bf.documents(broken)

    def test_unpublished_plugin_is_excluded_everywhere(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertNotIn('yueliu', registry['plugins'])
        self.assertNotIn('yueliu', legacy['plugins'])
        self.assertNotIn('yueliu', per_plugin)

    def test_withdrawn_ids_are_signed_outside_legacy_plugin_map(self):
        plugins = copy.deepcopy(self.plugins)
        plugins['minutes']['published'] = False
        plugins['minutes']['everPublished'] = True
        registry, legacy, feeds = bf.documents(plugins)
        self.assertNotIn('minutes', registry['plugins'])
        self.assertIn('minutes', registry['withdrawn'])
        self.assertNotIn('yueliu', registry['withdrawn'])
        self.assertNotIn('minutes', legacy['plugins'])
        self.assertEqual(feeds['minutes']['plugins'], {})

    def test_withdrawal_empties_both_signed_plugin_feeds(self):
        plugins = copy.deepcopy(self.plugins)
        plugins['minutes'].update(published=False, everPublished=True)
        with patch.object(bf, 'envelope_bytes', return_value=b'signed'):
            generated = bf.generate(plugins)
        self.assertEqual(generated['plugin:minutes'][0]['plugins'], {})
        self.assertEqual(generated['compat-plugin:minutes'][0]['plugins'], {})


if __name__ == '__main__':
    unittest.main()
