import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Ordina"))
import resolve_bridge as bridge
from test_resolve_bridge import FakeManager, FakeProject


class OpenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = FakeManager()
        self.metadata = {"projectID": "target", "database": self.manager.database,
                         "folders": ["2026", "Q4", "20261102_测试"], "projectName": "测试"}
        (self.root / ".magic-resolve.json").write_text(json.dumps(self.metadata))
        self.request = {"action": "open", "projectRoot": str(self.root)}
        self.manager.GotoRootFolder()
        for folder in self.metadata["folders"]:
            self.manager.CreateFolder(folder)
            self.manager.OpenFolder(folder)
        self.manager.path = ["existing"]
        self.loaded = Mock()
        self.loaded.GetUniqueId.return_value = "target"
        self.manager.LoadProject = Mock(return_value=self.loaded)

    def test_loads_linked_identity_and_restores_navigation(self):
        result = bridge.run(self.manager, self.request)
        self.assertTrue(result["opened"])
        self.manager.LoadProject.assert_called_once_with("测试")
        self.assertEqual(self.manager.path, ["existing"])

    def test_new_project_requests_setup_with_opened_project_id(self):
        script = self.root / "Project Setup.py"
        script.touch()
        self.manager.Fusion = Mock()
        launch = self.manager.Fusion.return_value.RunScript
        launch.return_value = None
        with patch.object(bridge, "__file__", str(self.root / "resolve_bridge.py")):
            result = bridge.run(self.manager, dict(self.request, action="open_new_project"))
        self.assertEqual(result["projectID"], "target")
        launch.assert_called_once_with(str(script.resolve()), {"OrdoExpectedProjectID": "target"})
        self.assertEqual(self.manager.path, ["existing"])

    def test_never_saves_or_closes_open_work(self):
        project = FakeProject(name="正在剪辑")
        self.manager.current = project
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertIs(self.manager.current, project)
        self.manager.LoadProject.assert_not_called()
        self.assertEqual(self.manager.path, ["existing"])

    def test_does_not_switch_database(self):
        self.manager.database = {"DbType": "Disk", "DbName": "other"}
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.manager.LoadProject.assert_not_called()

    def test_missing_association_does_not_guess_by_name(self):
        (self.root / ".magic-resolve.json").unlink()
        with self.assertRaises(FileNotFoundError):
            bridge.run(self.manager, self.request)
        self.manager.LoadProject.assert_not_called()
