import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from github_production_backend import ProductionBackend
from release_transaction import LocalGateway
import production_authorization as auth

MAIN = 'BrOKenAG/ordina-releases'

class ProductionWriteGuards(unittest.TestCase):
    def test_main_guard_checks_server_permission_and_signing(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0'}), \
             patch.object(auth.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"permissions":{"push":true}}')) as gh, \
             patch('feed_signing.sign_document') as sign:
            auth.authorize_main_write()
            self.assertEqual(gh.call_count, 1)
            sign.assert_called_once()

    def test_no_write_permission_never_reaches_signer(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0'}), \
             patch.object(auth.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"permissions":{"push":false}}')), \
             patch('feed_signing.sign_document') as sign:
            with self.assertRaises(ValueError): auth.authorize_main_write()
            sign.assert_not_called()

    def test_lab_refuses_before_github(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '1'}), patch.object(auth.subprocess, 'run') as gh:
            with self.assertRaises(ValueError): auth.authorize_main_write()
            gh.assert_not_called()

    def test_signer_failure_is_denied(self):
        with patch.dict(os.environ, {'ORDINA_DEV_LAB': '0'}), \
             patch.object(auth.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"permissions":{"push":true}}')), \
             patch('feed_signing.sign_document', side_effect=ValueError('untrusted')):
            with self.assertRaises(ValueError): auth.authorize_main_write()

    def test_main_backend_checks_each_write_and_lock(self):
        from github_lab_backend import GitHubLabMultiBackend
        backend=ProductionBackend(MAIN,MAIN,'v1','a.json',{MAIN:['appcast.xml']},{MAIN})
        with patch('production_authorization.authorize_main_write', side_effect=ValueError('denied')), \
             patch.object(GitHubLabMultiBackend,'acquire') as acquire, \
             patch.object(GitHubLabMultiBackend,'write') as write:
            with self.assertRaises(ValueError): backend.acquire('token')
            with self.assertRaises(ValueError): backend.write(MAIN,'head',b'data','token')
            acquire.assert_not_called(); write.assert_not_called()

    def test_release_creation_rejects_unknown_target_before_network(self):
        with patch('release_transaction.subprocess.run') as run:
            with self.assertRaises(ValueError):
                LocalGateway().create_release('other/repo','v1',Path('/tmp/a'),'title','notes')
            run.assert_not_called()

    def test_legacy_direct_writes_are_unavailable(self):
        gateway = LocalGateway()
        with patch('release_transaction.subprocess.run') as run:
            with self.assertRaises(ValueError): gateway._put_remote(MAIN, 'registry.json', b'bad')
            with self.assertRaises(ValueError): gateway.push(SimpleNamespace(commit_paths=['registry.json']))
            run.assert_not_called()

    def test_mixed_repository_backend_checks_plugin_authorization(self):
        plugin = 'BrOKenAG/ordina-plugin-demo'
        backend = ProductionBackend(MAIN, MAIN, 'v1', 'a.json', {MAIN:['registry.json'],plugin:['manifest-v2.json']}, {MAIN,plugin})
        with patch('production_authorization.authorize_main_write'), \
             patch('dev_publish.authorize_direct_plugin_write', side_effect=ValueError('denied')) as guard:
            with self.assertRaises(ValueError): backend.acquire('token')
            guard.assert_called_once()

    def test_signed_tool_feed_is_recognized_and_unsigned_rejected(self):
        import base64, json
        backend = ProductionBackend(MAIN, MAIN, 'dev-tool-v1', 'tool.pyz', {MAIN:['dev-tool/manifest.json']}, {MAIN})
        content = b'signed'
        plan = {'feeds': {MAIN: json.dumps({'dev-tool/manifest.json': base64.b64encode(content).decode()})}}
        valid = {'format':1, 'tool':'ordina-publisher', 'channel':'stable', 'versions':[]}
        with patch('feed_signing.verify_envelope', return_value=valid):
            self.assertEqual(backend._feed_documents(plan)[0][3], valid)
        with patch('feed_signing.verify_envelope', side_effect=ValueError('bad signature')):
            with self.assertRaises(ValueError): backend._feed_documents(plan)

    def test_tool_feed_rejects_changed_historical_assets(self):
        from github_production_backend import validate_tool_transition
        old = {'format':1,'tool':'ordina-publisher','channel':'stable','versions':[{'version':'1.0.0','sha256':'old'}]}
        new = {'format':1,'tool':'ordina-publisher','channel':'stable','versions':[{'version':'1.0.0','sha256':'changed'}]}
        with self.assertRaises(ValueError): validate_tool_transition(old, new)

    def test_production_backend_rejects_unrelated_files(self):
        with self.assertRaises(ValueError):
            ProductionBackend(MAIN, MAIN, 'v1', 'a.json', {MAIN:['README.md']}, {MAIN})
        plugin = 'BrOKenAG/ordina-plugin-demo'
        with self.assertRaises(ValueError):
            ProductionBackend(MAIN, plugin, 'v1', 'a.json', {plugin:['arbitrary.json']}, {MAIN, plugin})

    def test_direct_plugin_authorization_rejects_lab_before_signing_or_network(self):
        import dev_publish
        with patch.dict(os.environ, {'ORDINA_DEV_LAB':'1'}), \
             patch.object(dev_publish, 'credential'), \
             patch.object(dev_publish.subprocess, 'run') as gh, \
             patch('feed_signing.sign_document') as sign:
            with self.assertRaises(dev_publish.Failure):
                dev_publish.production_authorize(SimpleNamespace(credentials=None), 'BrOKenAG/ordina-plugin-demo')
            gh.assert_not_called(); sign.assert_not_called()

    def test_rechecks_authorization_immediately_before_ref_update(self):
        from github_lab_backend import GitHubLabMultiBackend
        backend = ProductionBackend(MAIN, MAIN, 'v1', 'a.json', {MAIN:['appcast.xml']}, {MAIN})
        with patch('production_authorization.authorize_main_write', side_effect=ValueError('revoked')), \
             patch.object(GitHubLabMultiBackend, '_advance') as advance:
            with self.assertRaises(ValueError): backend._advance(MAIN, 'heads/main', 'a'*40)
            advance.assert_not_called()
