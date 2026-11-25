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

    def test_registry_carries_descriptor_but_no_asset_secrets(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(registry['format'], 2)
        for name, descriptor in registry['plugins'].items():
            for leaked in ('sha256', 'size', 'asset', 'source', 'published', 'url', 'files'):
                self.assertNotIn(leaked, descriptor, f'{name} 泄漏了 {leaked}')
            required = {'repository', 'feed', 'downloadPrefix', 'kind', 'name',
                        'summary', 'symbol', 'version', 'minClientVersion',
                        'protocol', 'capabilities'}
            self.assertEqual(set(descriptor), required)
            self.assertEqual(descriptor['protocol'], 2)

    def test_legacy_feed_contains_every_published_plugin(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(set(legacy['plugins']), set(bf.published(self.plugins)))
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
            self.assertIn('/BrOKenAG/ordina-releases/releases/download/',
                          legacy['plugins'][name]['url'])

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


if __name__ == '__main__':
    unittest.main()
