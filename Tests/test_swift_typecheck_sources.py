import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_swift_typecheck as check


class SourceSelectionTests(unittest.TestCase):
    def test_selects_only_matching_configuration_generated_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Ordina').mkdir()
            (root / 'Ordina' / 'App.swift').write_text('')
            for config in ('Debug', 'Release'):
                path = root / '.build/Build/Intermediates.noindex/Ordina.build' / config / 'Ordina.build/DerivedSources/GeneratedAssetSymbols.swift'
                path.parent.mkdir(parents=True)
                path.write_text('')
            with patch.object(check, 'ROOT', root):
                debug = check.sources('Debug')
                release = check.sources('Release')
            self.assertEqual(sum(x.endswith('GeneratedAssetSymbols.swift') for x in debug), 1)
            self.assertTrue(any('/Debug/' in x for x in debug))
            self.assertTrue(any('/Release/' in x for x in release))
            self.assertFalse(any('/Release/' in x for x in debug))


if __name__ == '__main__':
    unittest.main()
