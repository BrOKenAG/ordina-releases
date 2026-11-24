"""Regression tests for the release transaction and its hard invariant."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import release_transaction as rt


def make_plan(data=b'asset-bytes', signature=None):
    asset = rt.Asset('plugin.json', data,
                     'https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/'
                     'plugin-minutes-v0.3.0/plugin.json')
    return rt.Plan(
        label='Test v0.3.0',
        repository='BrOKenAG/ordina-plugin-minutes',
        tag='plugin-minutes-v0.3.0',
        title='Test v0.3.0',
        notes='test',
        asset=asset,
        ed_signature=signature,
        feeds=[rt.FeedTarget('registry', b'{}'), rt.FeedTarget('legacy', b'{}')],
        commit_paths=['registry.json'],
    )


class RecordingGateway(rt.Gateway):
    def __init__(self, data=b'asset-bytes', code='200', exists=False,
                 served=None, signature_ok=True):
        self.calls = []
        self.data = data
        self.code = code
        self.exists = exists
        self.served = served
        self.signature_ok = signature_ok

    def preflight(self, plan):
        self.calls.append('preflight')

    def release_exists(self, repository, tag):
        self.calls.append('release_exists')
        return self.exists

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        self.calls.append('create_release')

    def http_get(self, url, dest, timeout):
        self.calls.append('http_get')
        dest.write_bytes(self.served if self.served is not None else self.data)
        return self.code

    def verify_signature(self, path, signature):
        self.calls.append('verify_signature')
        return self.signature_ok

    def write_feeds(self, feeds):
        self.calls.append('write_feeds')

    def push(self, plan):
        self.calls.append('push')

    def postflight(self, plan):
        self.calls.append('postflight')


class TransactionTests(unittest.TestCase):
    def test_stage_order_and_all_stages_complete(self):
        plan = make_plan()
        gateway = RecordingGateway()
        with tempfile.TemporaryDirectory() as temporary:
            result = rt.execute(plan, gateway, Path(temporary))
        self.assertEqual(result['stages'], list(rt.STAGES))
        self.assertEqual(result['asset'], 'created')
        self.assertLess(gateway.calls.index('create_release'),
                        gateway.calls.index('http_get'))
        self.assertLess(gateway.calls.index('http_get'),
                        gateway.calls.index('write_feeds'))
        self.assertLess(gateway.calls.index('write_feeds'),
                        gateway.calls.index('push'))

    def test_gate_failure_never_writes_feeds(self):
        for label, kwargs in (
            ('http 404', {'code': '404'}),
            ('size mismatch', {'served': b'short'}),
            ('hash mismatch', {'served': b'asset-BYTES!'}),
        ):
            with self.subTest(label=label):
                gateway = RecordingGateway(**kwargs)
                with tempfile.TemporaryDirectory() as temporary:
                    with self.assertRaises(rt.ReleaseError) as caught:
                        rt.execute(make_plan(), gateway, Path(temporary))
                self.assertEqual(caught.exception.stage, 'gate')
                self.assertEqual(caught.exception.completed, ('preflight', 'asset'))
                self.assertNotIn('write_feeds', gateway.calls)
                self.assertNotIn('push', gateway.calls)
                self.assertNotIn('postflight', gateway.calls)

    def test_bad_ed_signature_fails_the_gate(self):
        gateway = RecordingGateway(signature_ok=False)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(rt.ReleaseError) as caught:
                rt.execute(make_plan(signature='sig'), gateway, Path(temporary))
        self.assertEqual(caught.exception.stage, 'gate')
        self.assertNotIn('write_feeds', gateway.calls)

    def test_idempotent_retry_reuses_identical_release(self):
        gateway = RecordingGateway(exists=True)
        with tempfile.TemporaryDirectory() as temporary:
            result = rt.execute(make_plan(), gateway, Path(temporary))
        self.assertEqual(result['asset'], 'reused')
        self.assertNotIn('create_release', gateway.calls)
        self.assertEqual(gateway.calls.count('http_get'), 2)  # reuse check + GATE

    def test_idempotent_retry_rejects_different_asset(self):
        gateway = RecordingGateway(exists=True, served=b'different')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(rt.ReleaseError) as caught:
                rt.execute(make_plan(), gateway, Path(temporary))
        self.assertEqual(caught.exception.stage, 'asset')
        self.assertNotIn('write_feeds', gateway.calls)

    def test_preflight_failure_aborts_before_asset(self):
        class Failing(RecordingGateway):
            def preflight(self, plan):
                raise ValueError('工作区不干净')

        gateway = Failing()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(rt.ReleaseError) as caught:
                rt.execute(make_plan(), gateway, Path(temporary))
        self.assertEqual(caught.exception.stage, 'preflight')
        self.assertEqual(caught.exception.completed, ())
        self.assertNotIn('create_release', gateway.calls)

    def test_feed_stage_failure_reports_completed_stages(self):
        class Failing(RecordingGateway):
            def write_feeds(self, feeds):
                self.calls.append('write_feeds')
                raise ValueError('推送失败')

        gateway = Failing()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(rt.ReleaseError) as caught:
                rt.execute(make_plan(), gateway, Path(temporary))
        self.assertEqual(caught.exception.stage, 'feeds')
        self.assertEqual(caught.exception.completed,
                         ('preflight', 'asset', 'gate'))
        self.assertNotIn('push', gateway.calls)

    def test_dry_run_gateway_records_full_pipeline(self):
        gateway = rt.DryRunGateway(b'asset-bytes')
        with tempfile.TemporaryDirectory() as temporary:
            result = rt.execute(make_plan(), gateway, Path(temporary))
        self.assertEqual(result['stages'], list(rt.STAGES))
        self.assertIn('create_release', gateway.calls)
        self.assertIn(('write_feeds', ['registry', 'legacy']), gateway.calls)

    def test_failure_description_guides_retry(self):
        error = rt.ReleaseError('gate', 'HTTP 404', ('preflight', 'asset'))
        text = rt.describe_failure(error)
        self.assertIn('gate', text)
        self.assertIn('重跑', text)
        self.assertIn('未被写入', text)

    def test_check_asset_accepts_exact_match(self):
        asset = make_plan().asset
        self.assertTrue(rt.check_asset('200', asset.data, asset))


if __name__ == '__main__':
    unittest.main()

class AppReleaseStagesTests(unittest.TestCase):
    def plan(self, publish=False):
        plan = make_plan()
        plan.feeds = [rt.FeedTarget('appcast', b'appcast'), rt.FeedTarget('registry', b'registry')]
        plan.publish_appcast = publish
        return plan

    def gateway(self, failure=None):
        class Fake(RecordingGateway):
            def write_feeds(self, feeds):
                self.calls.append('write:' + feeds[0].label)
                if failure == 'write:' + feeds[0].label: raise OSError('offline')
            def push(self, plan):
                self.calls.append('push:' + plan.commit_paths[0])
                if failure == 'push:' + plan.commit_paths[0]: raise OSError('push failed')
            def verify_registry_online(self, target, timeout):
                self.calls.append('registry_gate')
                if failure == 'registry_gate': raise OSError('Pages delayed')
            def verify_client_validation(self, plan):
                self.calls.append('client_validation')
                if failure == 'client_validation': raise OSError('not validated')
        return Fake()

    def test_prepare_stops_before_appcast(self):
        gateway = self.gateway()
        with tempfile.TemporaryDirectory() as tmp:
            result = rt.execute(self.plan(), gateway, Path(tmp))
        self.assertTrue(result['awaiting_client_validation'])
        self.assertIn('registry_gate', gateway.calls)
        self.assertNotIn('write:appcast', gateway.calls)

    def test_every_registry_or_client_failure_blocks_appcast(self):
        for failure in ('write:registry', 'push:registry.json', 'registry_gate', 'client_validation'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                gateway = self.gateway(failure)
                with self.assertRaises(rt.ReleaseError): rt.execute(self.plan(True), gateway, Path(tmp))
                self.assertNotIn('write:appcast', gateway.calls)

    def test_validated_appcast_follows_online_gate(self):
        gateway = self.gateway()
        with tempfile.TemporaryDirectory() as tmp:
            rt.execute(self.plan(True), gateway, Path(tmp))
        self.assertLess(gateway.calls.index('registry_gate'), gateway.calls.index('client_validation'))
        self.assertLess(gateway.calls.index('client_validation'), gateway.calls.index('write:appcast'))

class PartialPublicationTests(unittest.TestCase):
    def test_remote_feed_partial_failure_never_pushes_main_repo(self):
        class Partial(RecordingGateway):
            def write_feeds(self, feeds):
                self.calls.append('plugin-one-written')
                raise ConnectionError('network interrupted before plugin two')
        plan = make_plan()
        plan.feeds = [rt.FeedTarget('plugin:first', b'one', repository='example/one'),
                      rt.FeedTarget('plugin:second', b'two', repository='example/two')]
        gateway = Partial()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(rt.ReleaseError) as error:
                rt.execute(plan, gateway, Path(tmp))
        self.assertEqual(error.exception.stage, 'feeds')
        self.assertIn('plugin-one-written', gateway.calls)
        self.assertNotIn('push', gateway.calls)
        self.assertIn('部分写入', rt.describe_failure(error.exception))

    def test_interrupted_registry_publish_never_writes_appcast(self):
        class Offline(RecordingGateway):
            def write_feeds(self, feeds): self.calls.append('write:' + feeds[0].label)
            def push(self, plan): raise ConnectionError('network interrupted')
        plan = make_plan()
        plan.feeds = [rt.FeedTarget('appcast', b'new'), rt.FeedTarget('registry', b'new')]
        gateway = Offline()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(rt.ReleaseError): rt.execute(plan, gateway, Path(tmp))
        self.assertNotIn('write:appcast', gateway.calls)

class RegistryPropagationTests(unittest.TestCase):
    def test_pages_delay_then_success(self):
        responses = iter([None, b'old', b'new'])
        sleeps = []
        rt.wait_for_registry(lambda: next(responses), lambda: True, b'new', lambda: sleeps.append(1))
        self.assertEqual(len(sleeps), 2)

    def test_network_and_failed_validation_stop(self):
        responses = iter([OSError('offline'), b'new', b'new'])
        def fetch():
            value = next(responses)
            if isinstance(value, Exception): raise value
            return value
        with self.assertRaises(ValueError):
            rt.wait_for_registry(fetch, lambda: False, b'new', lambda: None, attempts=3)

class FeedTransitionTests(unittest.TestCase):
    @staticmethod
    def doc(version, digest='a'):
        import json
        return json.dumps({'format': 1, 'plugins': {'p': {'version': version, 'sha256': digest}}}).encode()
    @staticmethod
    def decode(data):
        import json
        return json.loads(data)
    def test_retry_and_url_migration_keep_same_digest(self):
        data = self.doc('2.1')
        rt.validate_feed_transition(data, data, self.decode)
    def test_downgrade_and_same_version_change_are_rejected(self):
        for old, new in ((self.doc('2.2'), self.doc('2.1')), (self.doc('2.1','a'), self.doc('2.1','b'))):
            with self.assertRaises(ValueError): rt.validate_feed_transition(old, new, self.decode)
    def test_signature_error_stops_before_write(self):
        def bad(_): raise ValueError('bad signature')
        with self.assertRaises(ValueError): rt.validate_feed_transition(b'bad', self.doc('2.1'), bad)
