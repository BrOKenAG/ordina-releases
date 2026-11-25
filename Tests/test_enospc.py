"""ENOSPC (disk full) regressions: no half-written state, consistent recovery.

A real disk-full failure is an ``OSError`` with ``errno.ENOSPC``, not an
application-level exception. These tests inject exactly that errno at the
three write sites and check what is left on disk:

  * journal save  -> no journal, no temporary ``.journal-*`` file, no writes
  * feed write    -> journal stays ``publishing``, no partial feed, resumes once
  * asset write   -> no release asset, no recorded asset, HTTP read is 404
  * CLI           -> structured ``tool.error``, never a traceback or a secret

Local plugin installation state (utility script + state.json byte equality)
is covered end to end by ``verify_developer_center.py::enospc_consistency``.
"""
import contextlib
import errno
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dev_lab_backend                                        # noqa: E402
import dev_publish as dp                                      # noqa: E402
import durable_transaction as tx                              # noqa: E402

REPOS = ('BrOKenAG/ordina-releases', 'BrOKenAG/ordina-plugin-demo')


def enospc():
    return OSError(errno.ENOSPC, 'No space left on device')


class MemoryBackend:
    """Smallest backend satisfying the durable transaction contract."""

    def __init__(self, repos):
        self.state = {repo: b'old' for repo in repos}
        self.heads = {repo: 'h0' for repo in repos}
        self.writes = []
        self.fail_next_write = False
        self.token = None

    def acquire(self, token):
        self.token = token

    def release(self, token):
        pass

    def verify_assets(self, plan):
        pass

    def verify_online(self, plan):
        pass

    def read(self, repo):
        return self.heads[repo], self.state[repo]

    def write(self, repo, expected, data, token):
        if self.fail_next_write:
            self.fail_next_write = False
            raise enospc()
        assert expected == self.heads[repo]
        self.writes.append((repo, data))
        self.state[repo] = data
        self.heads[repo] = 'h' + str(len(self.writes))
        return self.heads[repo]

    def is_transaction_commit(self, repo, head, previous, token):
        return False


def release_plan(repos=REPOS):
    return {
        'id': 'enospc-tx',
        'asset': {'url': 'https://example.invalid/asset', 'size': 3,
                  'sha256': '0' * 64},
        'feeds': {repo: 'new-' + repo.split('/')[-1] for repo in repos},
    }


def leftovers(directory):
    return sorted(item.name for item in Path(directory).iterdir())


class EnospcTests(unittest.TestCase):
    def test_journal_save_enospc_creates_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'tx.json'
            backend = MemoryBackend(REPOS)
            with patch.object(tx, '_save', side_effect=enospc()):
                with self.assertRaises(OSError) as caught:
                    tx.publish(release_plan(), path, backend)
            self.assertEqual(caught.exception.errno, errno.ENOSPC)
            self.assertFalse(path.exists(), 'a half-written journal was left behind')
            self.assertEqual([name for name in leftovers(raw)
                              if name.startswith('.journal-')], [],
                             'a temporary journal file was left behind')
            self.assertEqual(backend.writes, [], 'feeds were written before the journal')

    def test_journal_save_enospc_then_retry_completes_once(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'tx.json'
            backend = MemoryBackend(REPOS)
            real_save, calls = tx._save, {'count': 0}

            def flaky_save(target, document):
                calls['count'] += 1
                if calls['count'] == 1:
                    raise enospc()
                return real_save(target, document)

            with patch.object(tx, '_save', flaky_save):
                with self.assertRaises(OSError):
                    tx.publish(release_plan(), path, backend)
            tx.publish(release_plan(), path, backend)
            document = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(document['status'], 'complete')
            self.assertEqual(len(backend.writes), len(REPOS),
                             'retry wrote a repository more than once')

    def test_feed_write_enospc_keeps_resumable_journal(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'tx.json'
            backend = MemoryBackend(REPOS)
            backend.fail_next_write = True
            with self.assertRaises(OSError) as caught:
                tx.publish(release_plan(), path, backend)
            self.assertEqual(caught.exception.errno, errno.ENOSPC)
            document = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(document['status'], 'publishing')
            self.assertTrue(all(record['after_head'] is None
                                for record in document['repositories'].values()),
                            'a partial write was recorded as complete')
            self.assertEqual([name for name in leftovers(raw)
                              if name.startswith('.journal-')], [])
            self.assertEqual(backend.state, {repo: b'old' for repo in REPOS},
                             'a feed was half-written')
            tx.resume(path, backend)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'],
                             'complete')
            self.assertEqual(len(backend.writes), len(REPOS),
                             'resume duplicated a feed write')

    def test_asset_enospc_leaves_no_release_asset(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'plugin.json'
            source.write_bytes(b'{}')
            gateway = dev_lab_backend.LabGateway(root)
            with patch.dict(os.environ, {'ORDINA_DEV_LAB_ENOSPC': 'asset'}):
                with self.assertRaises(OSError) as caught:
                    gateway.create_release('BrOKenAG/ordina-plugin-lab', 'tag-v1',
                                           source, 'title', 'notes')
            self.assertEqual(caught.exception.errno, errno.ENOSPC)
            assets = root / 'lab' / 'assets'
            self.assertEqual(list(assets.rglob('*')) if assets.exists() else [], [],
                             'a half-written release asset was left behind')
            self.assertEqual(gateway.assets, {}, 'asset was recorded as published')
            destination = root / 'downloaded'
            code = gateway.http_get(
                'https://github.com/BrOKenAG/ordina-plugin-lab/releases/'
                'download/tag-v1/plugin.json', destination, 5)
            self.assertEqual(code, '404')

    def test_cli_reports_structured_error_without_secrets(self):
        buffer = io.StringIO()

        def explode(_options):
            raise enospc()

        with patch.object(sys, 'argv', ['ordina-publisher', 'hello']), \
             patch.dict(dp.COMMANDS, {'hello': explode}), \
             contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as caught:
                dp.main()
        self.assertEqual(caught.exception.code, 1)
        payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error']['code'], 'tool.error')
        self.assertIn('No space left on device', payload['error']['message'])
        self.assertNotIn('Traceback', buffer.getvalue())
        self.assertNotIn('ordina-dev-publisher', buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
