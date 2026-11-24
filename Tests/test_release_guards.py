"""Dangerous cross-repository operations fail closed during preparation."""
import subprocess
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class ReleaseGuardTests(unittest.TestCase):
    def check_blocked(self, script, *args):
        result = subprocess.run([sys.executable, str(ROOT/'scripts'/script), *args], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('暂', result.stderr)
    def test_rollback_cannot_push(self): self.check_blocked('rollback_feeds.py', '--push')
    def test_plugin_publish_cannot_push(self): self.check_blocked('publish_plugin.py', 'minutes')
    def test_feeds_cannot_push(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/build_feeds.py'), '--push'], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('停用', result.stderr)
