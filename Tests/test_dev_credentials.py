"""Regression tests for developer credential verification.

The credential is a signed document; these tests cover the signature, the
claims, the scope/repository allowlist and the file-permission rules that keep
a credential out of the repository and out of a world-readable file.
"""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dev_credentials as dc
import feed_signing
import lab_signing

SEED = bytes(range(32))


def document(**overrides):
    now = int(time.time())
    value = {'format': 1, 'developer': 'BrOKenAG', 'keyId': dc.KEY_ID,
             'issuedAt': now - 60, 'notBefore': now - 60,
             'expires': now + 24 * 3600,
             'scopes': ['plugin.publish', 'plugin.localTest', 'repository.create'],
             'repositories': ['BrOKenAG/ordina-plugin-*']}
    value.update(overrides)
    return value


def signed(value):
    payload = feed_signing.canonical(value)
    signature = base64.b64encode(lab_signing.signature(SEED, payload)).decode('ascii')
    return json.dumps({'payload': base64.b64encode(payload).decode('ascii'),
                       'signature': signature}, separators=(',', ':'))


class LabEnvironment(unittest.TestCase):
    """Every test runs as an isolated lab process with a throwaway key."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dev-credential-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.key = self.root / 'signing.key'
        self.key.write_bytes(base64.b64encode(SEED))
        self.environment = patch.dict(os.environ, {
            'ORDINA_DEV_LAB': '1', 'ORDINA_DEV_SIGN_KEY': str(self.key)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, text, mode=0o600, name='credential.json'):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        path.chmod(mode)
        return path


class SignatureTests(LabEnvironment):
    def test_signed_document_verifies(self):
        value = dc.verify_raw(signed(document()))
        self.assertEqual(value['developer'], 'BrOKenAG')

    def test_tampered_payload_is_rejected(self):
        raw = json.loads(signed(document()))
        payload = json.loads(base64.b64decode(raw['payload']))
        payload['scopes'].append('tool.install')
        raw['payload'] = base64.b64encode(feed_signing.canonical(payload)).decode()
        with self.assertRaises(dc.CredentialError) as caught:
            dc.verify_raw(json.dumps(raw))
        self.assertEqual(caught.exception.code, 'auth.signature')

    def test_unsigned_or_malformed_documents_are_rejected(self):
        for text in ('', '{}', json.dumps({'payload': 'x'}),
                     json.dumps({'payload': 'x', 'signature': 'y'})):
            with self.assertRaises(dc.CredentialError) as caught:
                dc.verify_raw(text)
            self.assertIn(caught.exception.code, ('auth.invalid', 'auth.signature'))

    def test_oversized_credential_is_rejected(self):
        with self.assertRaises(dc.CredentialError) as caught:
            dc.verify_raw('x' * 9000)
        self.assertEqual(caught.exception.code, 'auth.invalid')


class ClaimTests(LabEnvironment):
    def check(self, **overrides):
        return dc.check(document(**overrides))

    def test_trusted_claims_pass(self):
        self.assertTrue(self.check())

    def test_untrusted_key_is_rejected(self):
        with self.assertRaises(dc.CredentialError) as caught:
            self.check(keyId='someone-else')
        self.assertEqual(caught.exception.code, 'auth.signature')

    def test_expiry_and_not_before(self):
        now = int(time.time())
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(issuedAt=now - 7200, notBefore=now - 7200,
                              expires=now - 3600))
        self.assertEqual(caught.exception.code, 'auth.expired')
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(issuedAt=now, notBefore=now + 3600,
                              expires=now + 7200))
        self.assertEqual(caught.exception.code, 'auth.notyet')

    def test_lifetime_is_bounded(self):
        now = int(time.time())
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(issuedAt=now, notBefore=now,
                              expires=now + dc.MAX_LIFETIME + 60))
        self.assertEqual(caught.exception.code, 'auth.invalid')
        with self.assertRaises(dc.CredentialError):
            dc.check(document(issuedAt=now, notBefore=now, expires=now - 1))

    def test_scopes_must_be_known_and_non_empty(self):
        for scopes in ([], ['plugin.everything'], 'plugin.publish'):
            with self.assertRaises(dc.CredentialError) as caught:
                self.check(scopes=scopes)
            self.assertEqual(caught.exception.code, 'auth.invalid')

    def test_scope_must_cover_the_action(self):
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(scopes=['tool.install']), scope='plugin.publish')
        self.assertEqual(caught.exception.code, 'auth.scope')

    def test_repository_allowlist_is_enforced(self):
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(repositories=['BrOKenAG/other-*']),
                     repository='BrOKenAG/ordina-plugin-demo')
        self.assertEqual(caught.exception.code, 'auth.repository')
        with self.assertRaises(dc.CredentialError) as caught:
            dc.check(document(repositories=['BrOKenAG/ordina-releases']),
                     repository='BrOKenAG/ordina-releases')
        self.assertEqual(caught.exception.code, 'auth.repository')

    def test_repository_scope_shape_is_validated(self):
        for repositories in ([], ['other/repo'], ['*/ordina-plugin-x'],
                             ['BrOKenAG/' + 'x' * 90],
                             ['BrOKenAG/a'] * 9):
            with self.assertRaises(dc.CredentialError) as caught:
                self.check(repositories=repositories)
            self.assertEqual(caught.exception.code, 'auth.invalid')


class FileSourceTests(LabEnvironment):
    def test_owner_only_file_is_accepted(self):
        path = self.write(signed(document()))
        raw, source = dc.read_raw(path)
        self.assertEqual(source, 'file')
        self.assertTrue(raw.startswith('{'))

    def test_world_readable_file_is_rejected(self):
        path = self.write(signed(document()), mode=0o644)
        with self.assertRaises(dc.CredentialError) as caught:
            dc.read_raw(path)
        self.assertEqual(caught.exception.code, 'auth.permissions')

    def test_file_source_requires_lab(self):
        path = self.write(signed(document()))
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0'}):
            with self.assertRaises(dc.CredentialError) as caught:
                dc.read_raw(path)
        self.assertEqual(caught.exception.code, 'auth.lab')

    def test_missing_keychain_entry(self):
        with patch.object(dc, '_read_keychain', return_value=None), \
                patch.dict(os.environ, {'ORDINA_DEV_LAB': '0'}):
            with self.assertRaises(dc.CredentialError) as caught:
                dc.read_raw()
        self.assertEqual(caught.exception.code, 'auth.missing')

    def test_load_returns_document_and_source(self):
        path = self.write(signed(document()))
        value, source = dc.load(path, scope='plugin.publish',
                                repository='BrOKenAG/ordina-plugin-demo')
        self.assertEqual(source, 'file')
        self.assertEqual(value['scopes'][0], 'plugin.publish')


class StatusTests(LabEnvironment):
    def test_status_reports_a_valid_credential_without_secrets(self):
        path = self.write(signed(document()))
        status = dc.status(path)
        self.assertTrue(status['valid'])
        self.assertEqual(status['source'], 'file')
        self.assertNotIn('payload', json.dumps(status))
        self.assertNotIn('signature', json.dumps(status))

    def test_status_reports_a_code_for_an_invalid_credential(self):
        path = self.write(signed(document()), mode=0o644)
        status = dc.status(path)
        self.assertFalse(status['valid'])
        self.assertEqual(status['code'], 'auth.permissions')


if __name__ == '__main__':
    unittest.main()
