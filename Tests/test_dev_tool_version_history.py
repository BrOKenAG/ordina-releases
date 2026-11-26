import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_dev_tool_versions as checks


class VersionHistoryTests(unittest.TestCase):
    def test_candidate_preserves_older_versions_and_requires_unique_latest(self):
        items = [{'version': '1.0.0'}, {'version': '1.1.0'}]
        self.assertEqual(checks.current_entry(items, '1.1.0'), items[1])
        with self.assertRaises(AssertionError):
            checks.current_entry(items, '1.0.0')
        with self.assertRaises(AssertionError):
            checks.current_entry(items + [items[1]], '1.1.0')


if __name__ == '__main__':
    unittest.main()
