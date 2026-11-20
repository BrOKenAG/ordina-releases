import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("resolve_bridge", Path(__file__).resolve().parents[1] / "Ordina/resolve_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeProject:
    def __init__(self, name="工作工程", timelines=0, clips=None, folders=None):
        self.name = name
        self.timelines = timelines
        self.clips = clips if clips is not None else []
        self.folders = folders if folders is not None else []

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return self.timelines

    def GetMediaPool(self):
        return self

    def GetRootFolder(self):
        return self

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.folders

    def GetUniqueId(self):
        return "unique-project"


class FakeManager:
    def __init__(self):
        self.path = ["existing"]
        self.folders = {(): ["existing"], ("existing",): []}
        self.projects = {}
        self.current = None
        self.created = []
        self.database = {"DbType": "PostgreSQL", "DbName": "test", "IpAddress": "localhost"}
        self.save = True

    def GetProjectManager(self):
        return self

    def GetCurrentDatabase(self):
        return self.database

    def GetCurrentProject(self):
        return self.current

    def GetCurrentFolder(self):
        return self.path[-1] if self.path else "root"

    def GotoParentFolder(self):
        if not self.path:
            return False
        self.path.pop()
        return True

    def GotoRootFolder(self):
        self.path = []
        return True

    def GetFolderListInCurrentFolder(self):
        return self.folders[tuple(self.path)]

    def OpenFolder(self, name):
        if name not in self.GetFolderListInCurrentFolder():
            return False
        self.path.append(name)
        return True

    def CreateFolder(self, name):
        self.folders[tuple(self.path)].append(name)
        self.folders[tuple(self.path + [name])] = []
        self.created.append(name)
        return True

    def GetProjectListInCurrentFolder(self):
        return self.projects.get(tuple(self.path), [])

    def CreateProject(self, name):
        self.projects[tuple(self.path)] = [name]
        self.current = FakeProject()
        return self.current

    def SaveProject(self):
        return self.save

    def CloseProject(self, project):
        self.current = None
        return True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()
        self.request = {"action": "create", "database": self.manager.database.copy(),
                        "folders": ["2026", "Q4", "20261102_测试"], "projectName": "测试"}

    def populate(self):
        bridge.run(self.manager, self.request)
        self.manager.created.clear()

    def test_creates_exactly_one_project_and_restores_navigation(self):
        result = bridge.run(self.manager, self.request)
        self.assertEqual(self.manager.path, ["existing"])
        self.assertEqual(self.manager.projects[tuple(self.request["folders"])], ["测试"])
        self.assertEqual(result["projectID"], "unique-project")
        self.assertIsNone(self.manager.current)

    def test_preflight_does_not_create(self):
        self.request["action"] = "preflight"
        bridge.run(self.manager, self.request)
        self.assertEqual(self.manager.created, [])
        self.assertEqual(self.manager.path, ["existing"])

    def test_year_starts_at_library_root(self):
        self.request["folders"] = ["2027", "Q1", "20270102_测试"]
        bridge.run(self.manager, self.request)
        self.assertIn("2027", self.manager.folders[()])
        self.assertNotIn("项目", self.manager.folders[()])
        self.assertEqual(self.manager.projects[("2027", "Q1", "20270102_测试")], ["测试"])

    def test_legacy_extra_root_is_rejected(self):
        self.request["folders"].insert(0, "项目")
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertEqual(self.manager.created, [])

    def test_existing_project_is_never_modified_or_duplicated(self):
        self.populate()
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertEqual(self.manager.created, [])
        self.assertEqual(self.manager.path, ["existing"])

    def test_other_named_project_also_blocks_creation(self):
        self.populate()
        self.manager.projects[tuple(self.request["folders"])] = ["other"]
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)

    def test_database_mismatch_stops_before_navigation(self):
        self.request["database"]["IpAddress"] = "other"
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertEqual(self.manager.path, ["existing"])
        self.assertEqual(self.manager.created, [])

    def test_open_project_is_not_saved_or_closed(self):
        current = FakeProject()
        self.manager.current = current
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertIs(self.manager.current, current)
        self.assertEqual(self.manager.created, [])

    def test_empty_default_project_does_not_block_preflight(self):
        current = FakeProject(name="Untitled Project")
        self.manager.current = current
        self.request["action"] = "preflight"
        result = bridge.run(self.manager, self.request)
        self.assertTrue(result["ready"])
        self.assertIs(self.manager.current, current)
        self.assertEqual(self.manager.created, [])

    def test_empty_default_project_allows_creation(self):
        self.manager.current = FakeProject(name="Untitled Project")
        result = bridge.run(self.manager, self.request)
        self.assertEqual(result["projectName"], "测试")
        self.assertEqual(self.manager.projects[tuple(self.request["folders"])], ["测试"])

    def test_default_project_with_content_is_protected(self):
        for arguments in [{"timelines": 1}, {"clips": ["media"]}, {"folders": ["bin"]}]:
            with self.subTest(arguments=arguments):
                current = FakeProject(name="Untitled Project", **arguments)
                self.manager.current = current
                with self.assertRaises(bridge.BridgeError):
                    bridge.run(self.manager, self.request)
                self.assertIs(self.manager.current, current)
                self.assertEqual(self.manager.created, [])

    def test_saved_default_named_project_is_protected(self):
        current = FakeProject(name="Untitled Project")
        self.manager.current = current
        self.manager.projects[tuple(self.manager.path)] = ["Untitled Project"]
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertIs(self.manager.current, current)
        self.assertEqual(self.manager.created, [])

    def test_save_failure_keeps_project_and_restores_navigation(self):
        self.manager.save = False
        with self.assertRaises(bridge.BridgeError):
            bridge.run(self.manager, self.request)
        self.assertIsNotNone(self.manager.current)
        self.assertEqual(self.manager.path, ["existing"])

    def test_local_database_uses_same_creation_rules(self):
        self.manager.database["DbType"] = "Disk"
        self.request["database"] = self.manager.database.copy()
        result = bridge.run(self.manager, self.request)
        self.assertEqual(result["folders"], ["2026", "Q4", "20261102_测试"])
        self.assertEqual(result["projectName"], "测试")


if __name__ == "__main__":
    unittest.main()
