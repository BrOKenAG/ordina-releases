import copy
import hashlib
import json
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

    def test_durable_plan_preserves_release_asset_and_signed_feeds(self):
        from unittest.mock import patch
        asset = b'previous release asset'
        self.source['plugins']['minutes']['sha256'] = hashlib.sha256(asset).hexdigest()
        self.source['plugins']['minutes']['size'] = len(asset)
        change = plugin_lifecycle.plan(self.source, 'minutes', False)
        with patch('build_feeds.envelope_bytes', side_effect=lambda doc, tool=None: json.dumps(doc).encode()):
            durable = plugin_lifecycle.prepare_transaction(change, asset, 'test-transaction')
        self.assertEqual(durable['asset']['sha256'], hashlib.sha256(asset).hexdigest())
        self.assertEqual(durable['lifecycle']['id'], 'minutes')
        self.assertFalse(durable['lifecycle']['published'])
        self.assertEqual(set(durable['feeds']), {'BrOKenAG/ordina-releases',
                                                 'BrOKenAG/ordina-plugin-minutes'})
        with self.assertRaises(ValueError):
            plugin_lifecycle.prepare_transaction(change, b'wrong', 'test-transaction')

    def test_transaction_rejects_changes_to_unrelated_plugins(self):
        from unittest.mock import patch
        from github_lab_backend import GitHubLabMultiBackend
        asset = b'previous release asset'
        self.source['plugins']['minutes']['sha256'] = hashlib.sha256(asset).hexdigest()
        self.source['plugins']['minutes']['size'] = len(asset)
        change = plugin_lifecycle.plan(self.source, 'minutes', False)
        fake_sign = lambda doc, tool=None: json.dumps(doc, sort_keys=True).encode()
        with patch('build_feeds.envelope_bytes', side_effect=fake_sign):
            before = build_feeds.generate(self.source['plugins'])
            durable = plugin_lifecycle.prepare_transaction(change, asset, 'tx-1')
        main = {'registry.json': before['registry'][1],
                'plugins/manifest.json': before['legacy'][1],
                'plugins/registry.source.json': json.dumps(self.source).encode()}
        target = {'manifest-v2.json': before['plugin:minutes'][1],
                  'manifest.json': before['compat-plugin:minutes'][1]}
        states = {'BrOKenAG/ordina-releases': GitHubLabMultiBackend.encode_files(main),
                  'BrOKenAG/ordina-plugin-minutes': GitHubLabMultiBackend.encode_files(target)}
        class Remote:
            def read(self, repo):
                return 'head', states[repo]
        with patch('feed_signing.verify_envelope', side_effect=lambda raw: json.loads(raw)):
            plugin_lifecycle.verify_transaction(durable, Remote())
            altered = copy.deepcopy(durable)
            altered['lifecycle']['before']['plugins']['multicam']['name'] = 'Forged'
            with self.assertRaises(ValueError):
                plugin_lifecycle.verify_transaction(altered, Remote())

    def test_lab_backend_checks_state_transaction_before_write(self):
        import base64
        import tempfile
        from unittest.mock import patch
        import dev_lab_backend
        asset = b'previous release asset'
        digest = hashlib.sha256(asset).hexdigest()
        transaction = {'lifecycle': {'id': 'minutes-lab', 'published': False},
                       'asset': {'url': 'https://github.com/BrOKenAG/ordina-plugin-minutes-lab/'
                                        'releases/download/plugin-minutes-lab-v1.0.0/minutes-lab.json',
                                 'size': len(asset), 'sha256': digest}, 'feeds': {}}
        with tempfile.TemporaryDirectory() as tmp:
            backend = dev_lab_backend.LabBackend(tmp)
            path = backend.asset_path(transaction['asset']['url'])
            path.parent.mkdir(parents=True)
            path.write_bytes(asset)
            with patch('plugin_lifecycle.verify_transaction', side_effect=ValueError('forged')) as verify:
                with self.assertRaisesRegex(ValueError, 'forged'):
                    backend.verify_assets(transaction)
                verify.assert_called_once_with(transaction, backend)

    def test_tool_preview_requires_valid_credential_and_fingerprints_state(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import dev_publish
        options = SimpleNamespace(plugin_id='minutes', published='no', backend='lab', credentials=None)
        with patch.object(dev_publish, 'lifecycle_source', return_value=self.source), \
             patch.object(dev_publish, 'credential'):
            preview = dev_publish.lifecycle_preview(options, ROOT)
        self.assertFalse(preview['published'])
        self.assertTrue(preview['fingerprint'])
        self.assertFalse(preview['noop'])


if __name__ == '__main__':
    unittest.main()
