"""Regression tests for the lab-only Ed25519 signer.

The lab signs with a throwaway key so acceptance never touches the developer's
Keychain. Signatures must be byte-identical to Sparkle's, because the client
verifies them with CryptoKit's Curve25519 implementation.
"""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lab_signing


# RFC 8032 section 7.1, test vectors 1 and 2.
VECTORS = (
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
     '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
     '5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c',
     '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da'
     '085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
)


class VectorTests(unittest.TestCase):
    def test_public_key_and_signature_match_rfc8032(self):
        for seed_hex, key_hex, message_hex, signature_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            message = bytes.fromhex(message_hex)
            self.assertEqual(lab_signing.public_key(seed).hex(), key_hex)
            self.assertEqual(lab_signing.signature(seed, message).hex(), signature_hex)

    def test_verification_accepts_and_rejects(self):
        for seed_hex, key_hex, message_hex, signature_hex in VECTORS:
            key = bytes.fromhex(key_hex)
            message = bytes.fromhex(message_hex)
            signature = bytes.fromhex(signature_hex)
            self.assertTrue(lab_signing.verify(key, message, signature))
            self.assertFalse(lab_signing.verify(key, message + b'x', signature))
            tampered = signature[:-1] + bytes([signature[-1] ^ 0x01])
            self.assertFalse(lab_signing.verify(key, message, tampered))

    def test_short_material_is_refused(self):
        with self.assertRaises(ValueError):
            lab_signing.signature(b'short', b'message')
        self.assertFalse(lab_signing.verify(b'x' * 31, b'm', b'y' * 64))


class SeedFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lab-signing-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.seed = bytes(range(32))

    def write(self, data):
        path = self.root / 'key'
        path.write_bytes(data)
        return path

    def test_reads_base64_hex_and_raw_seeds(self):
        for payload in (base64.b64encode(self.seed), self.seed.hex().encode(),
                        self.seed):
            self.assertEqual(lab_signing.read_seed(self.write(payload)), self.seed)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            lab_signing.read_seed(self.write(b'short'))

    def test_public_key_helper(self):
        expected = base64.b64encode(lab_signing.public_key(self.seed)).decode()
        path = self.write(base64.b64encode(self.seed))
        self.assertEqual(lab_signing.public_key_base64_file(path), expected)


class FeedSigningTests(unittest.TestCase):
    """feed_signing must only use the lab key inside an explicit lab run."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lab-feed-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.seed = bytes(range(32))
        self.key = self.root / 'key'
        self.key.write_bytes(base64.b64encode(self.seed))
        self.environment = {}

    def test_lab_mode_signs_and_verifies_with_the_throwaway_key(self):
        import os
        from unittest.mock import patch
        import feed_signing
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '1',
                                     'ORDINA_DEV_SIGN_KEY': str(self.key)}):
            self.assertEqual(feed_signing.signing_mode(), 'lab-key')
            raw = feed_signing.envelope_bytes({'format': 1, 'plugins': {}})
            document = json.loads(raw)
            payload = base64.b64decode(document['payload'])
            signature = base64.b64decode(document['signature'])
            self.assertTrue(lab_signing.verify(lab_signing.public_key(self.seed),
                                               payload, signature))
            self.assertEqual(feed_signing.verify_envelope(raw),
                             {'format': 1, 'plugins': {}})

    def test_key_outside_lab_is_ignored(self):
        import os
        from unittest.mock import patch
        import feed_signing
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0',
                                     'ORDINA_DEV_SIGN_KEY': str(self.key)}):
            self.assertIsNone(feed_signing.lab_seed())
            self.assertEqual(feed_signing.signing_mode(), 'keychain')


if __name__ == '__main__':
    unittest.main()
