import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_feeds
import plugin_lifecycle


class LifecyclePlanTests(unittest.TestCase):
    def setUp(self):
        self.source = {'format': 2, 'plugins': build_feeds.load_source()}

    def test_withdrawal_preserves_asset_and_other_plugins(self):
        before = copy.deepcopy(self.source)
        plan = plugin_lifecycle.plan(self.source, 'minutes', False)
        self.assertEqual(self.source, before)
        self.assertEqual(plan['after']['plugins']['multicam'], before['plugins']['multicam'])
        old = before['plugins']['minutes']
        new = plan['after']['plugins']['minutes']
        self.assertEqual(new['version'], old['version'])
        self.assertEqual(new['sha256'], old['sha256'])
        self.assertFalse(new['published'])
        self.assertTrue(new['everPublished'])
        self.assertFalse(plan['noop'])

    def test_republish_and_idempotency(self):
        unchanged = plugin_lifecycle.plan(self.source, 'minutes', True)
        self.assertTrue(unchanged['noop'])
        self.assertEqual(unchanged['after'], self.source)
        withdrawn = plugin_lifecycle.plan(self.source, 'minutes', False)['after']
        self.assertTrue(plugin_lifecycle.plan(withdrawn, 'minutes', False)['noop'])
        restored = plugin_lifecycle.plan(withdrawn, 'minutes', True)
        self.assertTrue(restored['after']['plugins']['minutes']['published'])
        self.assertEqual(restored['after']['plugins']['minutes']['version'],
                         self.source['plugins']['minutes']['version'])

    def test_unpublished_draft_cannot_be_mistaken_for_previous_release(self):
        with self.assertRaises(ValueError):
            plugin_lifecycle.plan(self.source, 'yueliu', True)
        with self.assertRaises(ValueError):
            plugin_lifecycle.plan(self.source, 'missing', False)


if __name__ == '__main__':
    unittest.main()
