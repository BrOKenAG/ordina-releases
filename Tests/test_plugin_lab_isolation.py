import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_plugin_protocol as lab


class PluginLabIsolationTests(unittest.TestCase):
    def test_signer_and_client_use_throwaway_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            lab.configure_lab_signing(Path(tmp))
            import feed_signing
            self.assertEqual(feed_signing.signing_mode(), 'lab-key')
            self.assertTrue(Path(os.environ['ORDINA_DEV_SIGN_KEY']).is_file())
            self.assertTrue(os.environ['ORDINA_PLUGIN_PUBLIC_KEY'])


if __name__ == '__main__':
    unittest.main()
