"""The tool-channel release script must reuse stage A, not bypass it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_dev_tool as pdt                                # noqa: E402
import durable_release as dr                                 # noqa: E402
from release_transaction import DryRunGateway                 # noqa: E402
from release_transaction import ROOT as TX_ROOT               # noqa: E402

SCRIPT = ROOT / 'scripts' / 'publish_dev_tool.py'


def run(*arguments):
    return subprocess.run([sys.executable, '-B', str(SCRIPT), *arguments],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)


class PublishDevToolTests(unittest.TestCase):
    def test_asset_failure_is_persisted_and_same_release_resumes(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            data = pdt.build_dev_tool.build('1.5.0')
            plan = pdt.build_plan('1.5.0', data, b'{}', '')
            staged = dr.prepare(plan, 'dev-tool-v1.5.0', journal_dir=tmp / 'journals')
            class Gateway(DryRunGateway):
                created = False
                fail_gate = True
                def create_release(self, *args, **kwargs):
                    assert staged.journal.exists(), 'upload preceded journal'
                    self.created = True
                def release_exists(self, *args):
                    return self.created
                def http_get(self, url, dest, timeout):
                    if self.fail_gate:
                        self.fail_gate = False
                        return '500'
                    return super().http_get(url, dest, timeout)
            gateway = Gateway(data)
            backend = dr.DryRunBackend(staged.plan['feeds'])
            with self.assertRaises(Exception):
                pdt.execute_release(plan, gateway, backend, staged, tmp, 1)
            self.assertTrue(gateway.created)
            self.assertEqual(json.loads(staged.journal.read_text())['status'], 'preparing')
            result = pdt.execute_release(plan, gateway, backend, staged, tmp, 1)
            self.assertEqual(result['asset'], 'reused')
            self.assertEqual(json.loads(staged.journal.read_text())['status'], 'complete')
            pdt.transaction.rollback(staged.journal, backend)
            self.assertEqual(json.loads(staged.journal.read_text())['status'], 'rolled_back')
            self.assertEqual(backend.values[plan.repository], b'')
            with self.assertRaisesRegex(ValueError, '已回滚'):
                pdt.execute_release(plan, gateway, backend, staged, tmp, 1)

    def test_manifest_signature_checked_before_upload(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '1'}):
            with patch('feed_signing.verify_envelope') as verify:
                with self.assertRaisesRegex(ValueError, '隔离环境'):
                    pdt.validate_signed_manifest(b'{}', {})
                verify.assert_not_called()
        with patch('feed_signing.verify_envelope', side_effect=ValueError('invalid')):
            with self.assertRaisesRegex(ValueError, 'invalid'):
                pdt.validate_signed_manifest(b'{}', {'version': '1.5.0'})
        with patch('feed_signing.verify_envelope', return_value={'version': 'different'}):
            with self.assertRaisesRegex(ValueError, '不一致'):
                pdt.validate_signed_manifest(b'{}', {'version': '1.5.0'})

    def test_rehearsal_uses_the_six_stage_transaction(self):
        result = run('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['stages'],
                         ['preflight', 'asset', 'gate', 'feeds', 'push', 'postflight'])
        self.assertFalse(plan['published'])
        self.assertFalse(plan['signedDuringRehearsal'])
        self.assertEqual(plan['feedTargets'], ['dev-tool-manifest'])
        self.assertEqual(plan['feeds']['BrOKenAG/ordina-releases'],
                         ['dev-tool/manifest.json'])
        self.assertEqual(plan['tag'], 'dev-tool-v' + pdt.build_dev_tool.read_version())

    def test_rehearsal_writes_nothing(self):
        before = {
            path: path.read_bytes() if path.exists() else None
            for path in (ROOT / 'dev-tool' / 'manifest.json',
                         ROOT / 'dev-tool' / 'manifest.source.json',
                         ROOT / 'dist' / 'dev-tool' / ('ordina-publisher-' + pdt.build_dev_tool.read_version() + '.pyz'))
        }
        journals = ROOT / '.transactions'
        existed = journals.exists()
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path, data in before.items():
            actual = path.read_bytes() if path.exists() else None
            self.assertEqual(actual, data, f'{path} 被预演改动')
        self.assertEqual(journals.exists(), existed, '预演创建了事务目录')

    def test_commands_never_bypass_the_transaction(self):
        result = run('--commands')
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertNotIn('gh release create', text)
        self.assertIn('--publish-manifest --execute', text)
        self.assertNotIn('git push', text)
        self.assertNotIn('gh api', text)
        self.assertNotIn('curl ', text)
        self.assertIn('durable_journal.py --rollback', text)

    def test_execute_requires_explicit_publish_and_lab_report(self):
        result = run('--execute')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--publish-manifest', result.stderr + result.stdout)
        result = run('--publish-manifest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--execute', result.stderr + result.stdout)
        with tempfile.TemporaryDirectory() as raw:
            result = run('--publish-manifest', '--execute',
                         '--lab-report', str(Path(raw) / 'missing.json'))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('隔离仓库验证报告', result.stderr + result.stdout)

    def test_plan_feed_is_inside_the_repository_root(self):
        self.assertEqual(TX_ROOT, ROOT, '事务模块路径与仓库根不一致')
        data = pdt.build_dev_tool.build('1.0.0')
        plan = pdt.build_plan('1.0.0', data, b'{}', '')
        self.assertEqual(plan.repository, 'BrOKenAG/ordina-releases')
        self.assertEqual(plan.tag, 'dev-tool-v1.0.0')
        self.assertEqual(len(plan.feeds), 1)
        target = plan.feeds[0]
        self.assertTrue(target.path.is_relative_to(ROOT), target.path)
        self.assertEqual(target.path.relative_to(ROOT).as_posix(),
                         'dev-tool/manifest.json')
        self.assertEqual(plan.asset.sha256, pdt.hashlib.sha256(data).hexdigest())

    def test_rollback_refuses_an_empty_manifest(self):
        result = run('--rollback-to', '0.9.0', '--prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('没有可回退到的版本', result.stderr + result.stdout)

    def test_rollback_to_requires_prepare(self):
        result = run('--rollback-to', '1.0.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--prepare', result.stderr + result.stdout)


if __name__ == '__main__':
    unittest.main()
