import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_app_acceptance as acceptance


class AcceptanceIsolationTests(unittest.TestCase):
    def test_only_exact_debug_build_is_accepted(self):
        self.assertTrue(acceptance.is_debug_app(acceptance.DEBUG_APP))
        self.assertFalse(acceptance.is_debug_app(ROOT / 'dist' / 'ordina.app'))
        self.assertFalse(acceptance.is_debug_app(Path('/Applications/ordina.app')))


if __name__ == '__main__':
    unittest.main()
