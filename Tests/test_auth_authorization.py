import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dev_publish as engine

class AuthorizationTests(unittest.TestCase):
    def test_production_uses_official_permission_check(self):
        options = SimpleNamespace(backend='production', credentials=None)
        source = {'plugins': {'demo': {'repository': 'BrOKenAG/ordina-plugin-demo'}}}
        with patch.object(engine, 'lab_enabled', return_value=False), \
             patch.object(engine, 'lifecycle_source', return_value=source), \
             patch.object(engine, 'environment_repository', return_value=Path('/tmp')), \
             patch.object(engine, 'production_authorize') as authorize, \
             patch.object(engine, 'ok'):
            engine.command_auth_authorize(options)
        authorize.assert_called_once_with(options, 'BrOKenAG/ordina-plugin-demo')

    def test_permission_denial_is_not_authorization(self):
        options = SimpleNamespace(backend='production', credentials=None)
        source = {'plugins': {'demo': {'repository': 'BrOKenAG/ordina-plugin-demo'}}}
        with patch.object(engine, 'lab_enabled', return_value=False), \
             patch.object(engine, 'lifecycle_source', return_value=source), \
             patch.object(engine, 'environment_repository', return_value=Path('/tmp')), \
             patch.object(engine, 'production_authorize', side_effect=engine.Failure('auth.github', 'denied')), \
             patch.object(engine, 'ok') as ok:
            with self.assertRaises(engine.Failure):
                engine.command_auth_authorize(options)
            ok.assert_not_called()
