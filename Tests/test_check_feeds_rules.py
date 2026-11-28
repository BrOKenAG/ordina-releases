"""Regression tests for the client-matching feed URL rules."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_feeds as cf

MINUTES_PREFIX = '/BrOKenAG/ordina-plugin-minutes/releases/download/'
MULTICAM_PREFIX = '/BrOKenAG/ordina-plugin-multicam/releases/download/'
LEGACY_PREFIX = '/BrOKenAG/ordina-releases/releases/download/'
SHA = 'a' * 64
MINUTES_URL = ('https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/'
               'plugin-minutes-v0.2.0/minutes.json')
MULTICAM_URL = ('https://github.com/BrOKenAG/ordina-plugin-multicam/releases/download/'
                'plugin-multicam-v2.1/Multicam.Tools.py')


def bundle(url=MINUTES_URL):
    return {'version': '0.2.0', 'url': url, 'sha256': SHA, 'kind': 'bundle'}


def script(url=MULTICAM_URL):
    return {'version': '2.1', 'url': url, 'sha256': SHA}


class FeedRuleTests(unittest.TestCase):
    def test_independent_repository_urls_are_accepted(self):
        # This is the exact case ordina 1.9.5 rejected.
        self.assertIsNone(cf.entry_url_error(bundle(), MINUTES_PREFIX))
        self.assertIsNone(cf.entry_url_error(script(), MULTICAM_PREFIX))

    def test_legacy_main_repository_prefix_is_rejected(self):
        self.assertIn('前缀', cf.entry_url_error(bundle(), LEGACY_PREFIX))

    def test_cross_plugin_prefix_is_rejected(self):
        self.assertIn('前缀', cf.entry_url_error(bundle(), MULTICAM_PREFIX))

    def test_prefix_must_be_a_branded_release_path(self):
        for bad in ('/BrOKenAG/ordina-plugin-minutes/releases/',
                    'https://evil.example.com/releases/download/',
                    '/BrOKenAG/../../releases/download/',
                    '/BrOKenAG/x//releases/download/'):
            with self.subTest(bad=bad):
                self.assertIsNotNone(cf.entry_url_error(bundle(), bad))

    def test_entry_url_must_be_github_https_direct_link(self):
        for bad in ('http://github.com' + MINUTES_PREFIX + 'x.json',
                    'https://evil.example.com' + MINUTES_PREFIX + 'x.json',
                    MINUTES_URL + '?token=1',
                    'https://user:pw@github.com' + MINUTES_PREFIX + 'x.json'):
            with self.subTest(bad=bad):
                self.assertIsNotNone(cf.entry_url_error(bundle(bad), MINUTES_PREFIX))

    def test_extension_must_match_kind(self):
        self.assertIsNotNone(cf.entry_url_error(
            bundle(MINUTES_URL.replace('.json', '.py')), MINUTES_PREFIX))
        self.assertIsNotNone(cf.entry_url_error(
            script(MULTICAM_URL.replace('.py', '.json')), MULTICAM_PREFIX))

    def test_version_and_digest_are_validated(self):
        broken = bundle()
        broken['version'] = 'latest'
        self.assertIsNotNone(cf.entry_url_error(broken, MINUTES_PREFIX))
        broken = bundle()
        broken['sha256'] = 'xyz'
        self.assertIsNotNone(cf.entry_url_error(broken, MINUTES_PREFIX))

    def test_registry_feed_rules(self):
        self.assertTrue(cf.valid_feed(
            'https://brokenag.github.io/ordina-plugin-minutes/manifest.json'))
        for bad in ('http://brokenag.github.io/ordina-plugin-minutes/manifest.json',
                    'https://evil.example.com/ordina-plugin-minutes/manifest.json',
                    'https://brokenag.github.io/ordina-plugin-minutes/manifest.json?x=1',
                    'https://brokenag.github.io/other/manifest.json'):
            with self.subTest(bad=bad):
                self.assertFalse(cf.valid_feed(bad))

    def test_published_source_feeds_all_pass_client_rules(self):
        import build_feeds as bf
        plugins = bf.load_source()
        registry, legacy, per_plugin = bf.documents(plugins)
        for name, descriptor in registry['plugins'].items():
            self.assertTrue(cf.valid_feed(descriptor['feed']))
            self.assertTrue(cf.valid_prefix(descriptor['downloadPrefix']))
            if name in legacy['plugins']:
                self.assertIsNone(cf.entry_url_error(legacy['plugins'][name], LEGACY_PREFIX))
            else:
                self.assertFalse(plugins[name].get('legacy'))
            self.assertIsNone(cf.entry_url_error(per_plugin[name]['plugins'][name],
                                                 descriptor['downloadPrefix']))


if __name__ == '__main__':
    unittest.main()
