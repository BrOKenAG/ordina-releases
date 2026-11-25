"""Dangerous cross-repository operations fail closed during preparation."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGuardTests(unittest.TestCase):
    def run_script(self, script, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *args],
                              cwd=ROOT, capture_output=True, text=True)

    def check_blocked(self, script, *args, contains):
        result = self.run_script(script, *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(contains, result.stderr)

    def test_rollback_cannot_push(self):
        self.check_blocked('rollback_feeds.py', '--push', contains='暂')

    def test_feeds_cannot_push(self):
        result = self.run_script('build_feeds.py', '--push')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('停用', result.stderr)

    def test_plugin_publish_requires_explicit_execute(self):
        self.check_blocked('publish_plugin.py', 'minutes', contains='--execute')

    def test_application_publish_requires_explicit_execute(self):
        self.check_blocked('publish_app_release.py', '--app', '/not-used',
                           '--archive', '/not-used', '--feed', '/not-used',
                           '--notes-file', '/not-used', contains='--execute')

    def test_plugin_publish_requires_lab_report(self):
        self.check_blocked('publish_plugin.py', 'minutes', '--execute',
                           '--lab-report', '/not-used', contains='隔离仓库验证报告')

    def test_application_publish_requires_lab_report(self):
        self.check_blocked('publish_app_release.py', '--app', '/not-used',
                           '--archive', '/not-used', '--feed', '/not-used',
                           '--notes-file', '/not-used', '--execute',
                           '--lab-report', '/not-used', contains='隔离仓库验证报告')

    def test_rollback_dry_run_does_not_change_feeds(self):
        paths = [ROOT / 'appcast.xml', ROOT / 'plugins/manifest.json',
                 ROOT / 'registry.json']
        before = [p.read_bytes() if p.is_file() else None for p in paths]
        result = self.run_script('rollback_feeds.py', '--to', 'HEAD~1', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, [p.read_bytes() if p.is_file() else None for p in paths])

    def test_journal_cli_requires_an_action(self):
        result = self.run_script('durable_journal.py', '/not-used')
        self.assertNotEqual(result.returncode, 0)
