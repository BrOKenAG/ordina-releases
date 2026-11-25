import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import durable_transaction as tx

class MemoryRemote:
    def __init__(self):
        self.heads = {'one':'h0', 'two':'h0'}
        self.values = {'one':b'old-one', 'two':b'old-two'}
        self.lock = None
        self.asset_valid = True
        self.fail_repo = None
        self.markers = {}
    def acquire(self, token):
        if self.lock not in (None, token): raise RuntimeError('locked')
        self.lock = token
    def release(self, token):
        if self.lock != token: raise RuntimeError('not owner')
        self.lock = None
    def verify_assets(self, plan):
        if not self.asset_valid: raise RuntimeError('asset unavailable')
    def read(self, repo): return self.heads[repo], self.values[repo]
    def is_transaction_commit(self, repo, head, previous, token):
        return self.markers.get((repo,head)) == (previous,token)
    def write(self, repo, expected, data, token):
        if self.fail_repo == repo:
            self.fail_repo = None
            raise ConnectionError('interrupted')
        if self.heads[repo] != expected: raise RuntimeError('head changed')
        old = self.heads[repo]
        self.heads[repo] += 'x'
        self.markers[(repo,self.heads[repo])] = (old,token)
        self.values[repo] = data
        return self.heads[repo]

class DurableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal.json';self.remote=MemoryRemote()
        self.plan={'id':'task-1','feeds':{'one':'new-one','two':'new-two'}}
    def test_resume_after_partial_failure(self):
        self.remote.fail_repo='two'
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['one'], b'new-one')
        self.assertEqual(self.remote.values['two'], b'old-two')
        tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['two'], b'new-two')
        self.assertIsNone(self.remote.lock)
        self.assertEqual(json.loads(self.path.read_text())['status'],'complete')
    def test_assets_fail_before_any_feed_is_changed(self):
        self.remote.asset_valid=False
        with self.assertRaises(RuntimeError): tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['one'],b'old-one')
        self.assertEqual(self.remote.values['two'],b'old-two')
    def test_plan_change_never_reuses_journal(self):
        self.remote.fail_repo='two'
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        with self.assertRaises(ValueError): tx.publish({'id':'task-1','feeds':{'one':'different'}},self.path,self.remote)
    def test_rollback_refuses_foreign_head(self):
        self.remote.fail_repo='two'
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        self.remote.heads['one']='someone-else'
        with self.assertRaises(RuntimeError): tx.rollback(self.path,self.remote)
        self.assertEqual(self.remote.values['one'],b'new-one')
    def test_crash_after_remote_commit_can_be_reconciled(self):
        original = self.remote.write
        def interrupted(repo, expected, data, token):
            result = original(repo, expected, data, token)
            self.remote.write = original
            raise ConnectionError('response lost after commit')
        self.remote.write = interrupted
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['two'], b'new-two')
    def test_complete_journal_releases_lock_after_crash(self):
        original = self.remote.release
        def interrupted(token): raise ConnectionError('release failed')
        self.remote.release = interrupted
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        self.remote.release = original
        tx.publish(self.plan,self.path,self.remote)
        self.assertIsNone(self.remote.lock)
    def test_rollback_interruption_can_resume(self):
        self.remote.fail_repo='two'
        with self.assertRaises(ConnectionError): tx.publish(self.plan,self.path,self.remote)
        original = self.remote.write
        def interrupted(repo, expected, data, token):
            result = original(repo, expected, data, token)
            self.remote.write = original
            raise ConnectionError('rollback response lost')
        self.remote.write = interrupted
        with self.assertRaises(ConnectionError): tx.rollback(self.path,self.remote)
        tx.rollback(self.path,self.remote)
        self.assertEqual(self.remote.values['one'],b'old-one')
    def test_rollback_reconciles_write_response_lost_without_continuing_publish(self):
        original = self.remote.write
        def interrupted(repo, expected, data, token):
            original(repo, expected, data, token)
            self.remote.write = original
            raise ConnectionError('write response lost')
        self.remote.write = interrupted
        with self.assertRaises(ConnectionError): tx.publish(self.plan, self.path, self.remote)
        tx.rollback(self.path, self.remote)
        self.assertEqual(self.remote.values, {'one': b'old-one', 'two': b'old-two'})
        self.assertIsNone(self.remote.lock)

    def test_repeat_rollback_does_not_modify_repos(self):
        self.remote.fail_repo = 'two'
        with self.assertRaises(ConnectionError): tx.publish(self.plan, self.path, self.remote)
        tx.rollback(self.path, self.remote)
        heads = dict(self.remote.heads)
        tx.rollback(self.path, self.remote)
        self.assertEqual(heads, self.remote.heads)
        self.assertIsNone(self.remote.lock)

    def test_completed_retry_detects_foreign_change(self):
        tx.publish(self.plan,self.path,self.remote)
        self.remote.heads['one'] = 'new-foreign-head'
        self.remote.values['one'] = b'foreign'
        with self.assertRaises(RuntimeError): tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['one'], b'foreign')

    def test_reserved_lock_name_rejected(self):
        bad = {'id': 'free', 'feeds': {'one': 'bad'}}
        with self.assertRaises(ValueError): tx.publish(bad,self.path,self.remote)
        self.assertIsNone(self.remote.lock)

    def test_concurrent_lock_rejected(self):
        self.remote.lock='other'
        with self.assertRaises(RuntimeError): tx.publish(self.plan,self.path,self.remote)
        self.assertEqual(self.remote.values['one'],b'old-one')
