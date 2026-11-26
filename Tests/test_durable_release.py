"""Regression tests for the durable release driver and production adapter."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import durable_release
from github_production_backend import ProductionBackend
from release_transaction import (Asset, DryRunGateway, FeedTarget, Plan,
                                 ReleaseError)

MAIN = 'BrOKenAG/ordina-releases'
PLUGIN = 'BrOKenAG/ordina-plugin-minutes'
URL = f'https://github.com/{PLUGIN}/releases/download/plugin-minutes-v0.3.0/a.json'


def make_plan():
    asset = Asset('a.json', b'abc', URL)
    return Plan(
        label='Minutes v0.3.0', repository=PLUGIN,
        tag='plugin-minutes-v0.3.0', title='Minutes v0.3.0', notes='test',
        asset=asset,
        feeds=[
            FeedTarget('registry', b'R', path=ROOT / 'registry.json'),
            FeedTarget('legacy', b'L', path=ROOT / 'plugins' / 'manifest.json'),
            FeedTarget('appcast', b'A', path=ROOT / 'appcast.xml'),
            FeedTarget('source', b'S', path=ROOT / 'plugins' / 'registry.source.json'),
            FeedTarget('plugin:minutes', b'M', repository=PLUGIN,
                       remote_path='manifest-v2.json'),
            FeedTarget('compat-plugin:minutes', b'C', repository=PLUGIN,
                       remote_path='manifest.json'),
        ],
    )


class MemoryRemote:
    def __init__(self, repos):
        self.heads = {repo: 'h0' for repo in repos}
        self.values = {repo: b'old' for repo in repos}
        self.lock = None
        self.asset_valid = True
        self.online = True
        self.markers = {}

    def acquire(self, token):
        if self.lock not in (None, token):
            raise RuntimeError('locked')
        self.lock = token

    def release(self, token):
        if self.lock != token:
            raise RuntimeError('not owner')
        self.lock = None

    def verify_assets(self, plan):
        if not self.asset_valid:
            raise RuntimeError('asset unavailable')

    def verify_online(self, plan):
        if not self.online:
            raise RuntimeError('pages unavailable')

    def read(self, repo):
        return self.heads[repo], self.values[repo]

    def is_transaction_commit(self, repo, head, previous, token):
        return self.markers.get((repo, head)) == (previous, token)

    def write(self, repo, expected, data, token):
        if self.heads[repo] != expected:
            raise RuntimeError('head changed')
        old = self.heads[repo]
        self.heads[repo] += 'x'
        self.markers[(repo, self.heads[repo])] = (old, token)
        self.values[repo] = data
        return self.heads[repo]


class DurableReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.plan = make_plan()

    def test_main_repository_files_are_never_attributed_to_the_plugin_repo(self):
        files = durable_release.collect_files(self.plan)
        self.assertEqual(set(files), {MAIN, PLUGIN})
        self.assertEqual(set(files[MAIN]),
                         {'registry.json', 'plugins/manifest.json', 'appcast.xml',
                          'plugins/registry.source.json'})
        self.assertEqual(set(files[PLUGIN]), {'manifest.json', 'manifest-v2.json'})

    def test_appcast_can_be_excluded_or_isolated(self):
        without = durable_release.collect_files(self.plan, include_appcast=False)
        self.assertNotIn('appcast.xml', without[MAIN])
        self.assertIn(PLUGIN, without)
        only = durable_release.collect_files(self.plan, include_appcast=True,
                                             only_appcast=True)
        self.assertEqual(only, {MAIN: {'appcast.xml': b'A'}})

    def test_prepare_is_deterministic_and_guards_the_staged_asset(self):
        first = durable_release.prepare(self.plan, 'plugin-minutes-v0.3.0',
                                        journal_dir=self.directory)
        second = durable_release.prepare(self.plan, 'plugin-minutes-v0.3.0',
                                         journal_dir=self.directory)
        self.assertEqual(first.plan, second.plan)
        self.assertEqual(first.journal, second.journal)
        self.assertEqual(first.asset_source.read_bytes(), b'abc')
        first.asset_source.write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            durable_release.prepare(self.plan, 'plugin-minutes-v0.3.0',
                                    journal_dir=self.directory)

    def test_lab_report_requires_every_check(self):
        path = self.directory / 'report.json'
        report = {'format': 1, 'passed': True,
                  'checks': {name: 'pass' for name in durable_release.LAB_CHECKS}}
        path.write_text(json.dumps(report))
        self.assertTrue(durable_release.lab_report_valid(path))
        report['checks']['rollback'] = 'fail'
        path.write_text(json.dumps(report))
        self.assertFalse(durable_release.lab_report_valid(path))
        self.assertFalse(durable_release.lab_report_valid(self.directory / 'missing.json'))

    def test_six_stages_run_in_order_with_the_journal(self):
        prepared = durable_release.prepare(self.plan, 'stage-order',
                                           include_appcast=False,
                                           journal_dir=self.directory)
        remote = MemoryRemote(prepared.plan['feeds'])
        gateway = DryRunGateway(b'abc')
        result = durable_release.execute(self.plan, gateway, remote, prepared,
                                         self.directory)
        self.assertEqual(result['stages'],
                         ['preflight', 'asset', 'gate', 'feeds', 'push', 'postflight'])
        self.assertEqual(json.loads(Path(result['journal']).read_text())['status'],
                         'complete')
        self.assertIsNone(remote.lock)

    def test_gate_failure_never_starts_a_transaction(self):
        prepared = durable_release.prepare(self.plan, 'gate-failure',
                                           include_appcast=False,
                                           journal_dir=self.directory)
        remote = MemoryRemote(prepared.plan['feeds'])

        class Offline(DryRunGateway):
            def http_get(self, url, dest, timeout):
                return '404'

        with self.assertRaises(ReleaseError) as caught:
            durable_release.execute(self.plan, Offline(b'abc'), remote, prepared,
                                    self.directory)
        self.assertEqual(caught.exception.stage, 'gate')
        self.assertEqual(caught.exception.completed, ('preflight', 'asset'))
        self.assertFalse(prepared.journal.exists())
        self.assertEqual(set(remote.values.values()), {b'old'})

    def test_feeds_failure_leaves_a_resumable_journal(self):
        prepared = durable_release.prepare(self.plan, 'feeds-failure',
                                           include_appcast=False,
                                           journal_dir=self.directory)
        remote = MemoryRemote(prepared.plan['feeds'])
        remote.asset_valid = False
        with self.assertRaises(ReleaseError) as caught:
            durable_release.execute(self.plan, DryRunGateway(b'abc'), remote,
                                    prepared, self.directory)
        self.assertEqual(caught.exception.stage, 'feeds')
        journal = prepared.journal
        self.assertNotEqual(json.loads(journal.read_text())['status'], 'complete')
        self.assertEqual(set(remote.values.values()), {b'old'})
        remote.asset_valid = True
        durable_release.resume_journal(journal, remote)
        self.assertEqual(json.loads(journal.read_text())['status'], 'complete')
        self.assertIsNone(remote.lock)

    def test_dry_run_backend_runs_the_full_transaction_without_network(self):
        prepared = durable_release.prepare(self.plan, 'dry-run',
                                           include_appcast=False,
                                           journal_dir=self.directory)
        backend = durable_release.DryRunBackend(prepared.plan['feeds'])
        result = durable_release.execute(self.plan, DryRunGateway(b'abc'),
                                         backend, prepared, self.directory)
        self.assertEqual(result['stages'],
                         ['preflight', 'asset', 'gate', 'feeds', 'push', 'postflight'])
        self.assertIsNone(backend.lock)
        self.assertIn('verify_assets', backend.calls)
        self.assertIn('verify_online', backend.calls)
        writes = [call for call in backend.calls
                  if isinstance(call, tuple) and call[0] == 'write']
        self.assertEqual(len(writes), len(prepared.plan['feeds']))
        self.assertEqual(json.loads(prepared.journal.read_text())['status'],
                         'complete')

    def test_backend_can_be_rebuilt_from_the_persisted_plan(self):
        prepared = durable_release.prepare(self.plan, 'rebuild',
                                           include_appcast=False,
                                           journal_dir=self.directory)
        journal = prepared.journal
        journal.write_text(json.dumps({'format': 1, 'plan': prepared.plan,
                                       'status': 'publishing',
                                       'repositories': {}}))
        backend, durable, _ = durable_release.backend_from_journal(journal)
        self.assertEqual(durable, prepared.plan)
        self.assertEqual(set(backend.paths), set(prepared.plan['feeds']))
        self.assertIn(PLUGIN, backend.allowed)


class ProductionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.repo = MAIN
        self.backend = ProductionBackend(self.repo, self.repo, 'v1', 'a.json',
                                         {self.repo: ['registry.json', 'appcast.xml']},
                                         {self.repo})
        self.content = b'signed-registry'
        self.snapshot = json.dumps({
            'registry.json': base64.b64encode(self.content).decode(),
            'appcast.xml': base64.b64encode(b'<rss/>').decode(),
        })

    def test_pages_delay_is_retried_before_completion(self):
        plan = {'asset': {'url': URL, 'sha256': '0' * 64, 'size': 1},
                'feeds': {self.repo: self.snapshot}}
        with patch('feed_signing.verify_envelope',
                   return_value={'format': 2, 'plugins': {}}), \
             patch.object(self.backend, '_fetch_public',
                          side_effect=[b'old', b'old', self.content, b'<rss/>',
                                       self.content, b'<rss/>']), \
             patch('github_production_backend.time.sleep'):
            self.backend.verify_online(plan)

    def test_appcast_must_reference_and_sign_the_published_asset(self):
        url = f'https://github.com/{MAIN}/releases/download/v1/a.json'
        xml = (
            '<?xml version="1.0"?>'
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">'
            '<channel><item><sparkle:version>35</sparkle:version>'
            f'<enclosure url="{url}" length="3" sparkle:edSignature="SIG"/>'
            '</item></channel></rss>\n<!-- edSignature: SIG -->'
        ).encode()
        asset = {'url': url, 'size': 3, 'sha256': '0' * 64,
                 'ed_signature': 'SIG'}
        documents = [(self.repo, 'appcast.xml', xml, None)]
        with patch.object(self.backend, '_verify_appcast_signature'):
            self.backend._validate_appcast({}, documents, asset)
            bad = dict(asset, ed_signature='OTHER')
            with self.assertRaises(ValueError):
                self.backend._validate_appcast({}, documents, bad)
            unsigned = xml.replace(b'<!-- edSignature: SIG -->', b'')
            with self.assertRaises(ValueError):
                self.backend._validate_appcast(
                    {}, [(self.repo, 'appcast.xml', unsigned, None)], asset)
        with patch.object(self.backend, '_verify_appcast_signature', side_effect=ValueError('bad signature')):
            with self.assertRaises(ValueError):
                self.backend._validate_appcast({}, documents, asset)

    def test_appcast_transition_rejects_removing_history(self):
        def feed(versions):
            items = ''.join(
                f'<item><sparkle:version>{v}</sparkle:version></item>' for v in versions)
            return ('<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
                    f'<channel>{items}</channel></rss>').encode()
        self.backend._validate_appcast_transition(feed(['34', '35']), feed(['34', '35', '36']))
        with self.assertRaises(ValueError):
            self.backend._validate_appcast_transition(feed(['34', '35']), feed(['36']))


if __name__ == '__main__':
    unittest.main()
