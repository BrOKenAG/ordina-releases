import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Ordina'))
import resolve_bridge as bridge
from test_resolve_bridge import FakeManager, FakeProject


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = FakeManager()
        self.manager.current = FakeProject()
        self.request = {'action': 'bind', 'projectRoot': str(self.root)}
        self.mount = f'server on {self.root} (smbfs, nodev)'

    def test_binding_stores_database_and_project_identity(self):
        with patch.object(subprocess, 'check_output', return_value=self.mount):
            result = bridge.run(self.manager, self.request)
        stored = json.loads((self.root / '.magic-resolve.json').read_text())
        self.assertEqual(stored, result)
        self.assertEqual(stored['projectID'], 'unique-project')
        self.assertEqual(stored['database'], self.manager.database)

    def test_conflicting_association_is_never_overwritten(self):
        manifest = self.root / '.magic-resolve.json'
        manifest.write_text('{"projectID":"another-project"}')
        with patch.object(subprocess, 'check_output', return_value=self.mount):
            with self.assertRaises(bridge.BridgeError):
                bridge.run(self.manager, self.request)
        self.assertEqual(manifest.read_text(), '{"projectID":"another-project"}')

    def test_local_destination_preserves_identity_binding(self):
        with patch.object(subprocess, 'check_output', return_value='/dev/disk1 on / (apfs, local)'):
            result = bridge.run(self.manager, self.request)
        self.assertEqual(result["projectID"], "unique-project")
        self.assertTrue((self.root / '.magic-resolve.json').exists())

    def test_project_closed_during_binding_does_not_write(self):
        with patch.object(subprocess, 'check_output', return_value=self.mount), patch.object(self.manager, 'GetCurrentProject', side_effect=[FakeProject(), None]):
            with self.assertRaises(bridge.BridgeError):
                bridge.run(self.manager, self.request)
        self.assertFalse((self.root / '.magic-resolve.json').exists())
