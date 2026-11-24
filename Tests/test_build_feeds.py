"""Regression tests for the single-source feed derivation."""
import copy
import sys
import unittest
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

    def test_registry_exposes_no_version_information(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(registry['format'], 2)
        for name, descriptor in registry['plugins'].items():
            for leaked in ('version', 'sha256', 'size', 'asset', 'source', 'published'):
                self.assertNotIn(leaked, descriptor, f'{name} 泄漏了 {leaked}')
            self.assertEqual(set(descriptor), {'repository', 'feed', 'downloadPrefix', 'kind'})

    def test_legacy_feed_contains_every_published_plugin(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(set(legacy['plugins']), set(bf.published(self.plugins)))
        self.assertEqual(legacy['format'], 1)

    def test_each_plugin_feed_contains_only_itself(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(set(per_plugin), set(bf.published(self.plugins)))
        for name, document in per_plugin.items():
            self.assertEqual(set(document['plugins']), {name})
            self.assertEqual(document['plugins'][name]['version'], legacy['plugins'][name]['version'])
            self.assertIn('/BrOKenAG/ordina-releases/releases/download/', legacy['plugins'][name]['url'])

    def test_bundle_kind_is_only_emitted_for_bundles(self):
        registry, legacy, per_plugin = bf.documents(self.plugins)
        self.assertEqual(legacy['plugins']['minutes']['kind'], 'bundle')
        self.assertNotIn('kind', legacy['plugins']['multicam'])

    def test_entry_url_is_built_from_own_repository(self):
        result = bf.entry('minutes', self.plugins['minutes'])
        self.assertEqual(result['version'], '0.2.0')
        self.assertEqual(
            result['url'],
            'https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/'
            'plugin-minutes-v0.2.0/minutes.json')
        self.assertTrue(result['url'].startswith(
            'https://github.com/' + self.plugins['minutes']['repository']))
        self.assertFalse(result['url'].startswith(
            'https://github.com/BrOKenAG/ordina-releases/releases/'))

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
