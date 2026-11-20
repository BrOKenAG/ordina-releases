import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('setup_scan', Path(__file__).resolve().parents[1] / 'Integrations/Project Setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ManualScanTests(unittest.TestCase):
    def test_readonly_manual_scan_process_returns_result_without_changing_settings(self):
        self.assertTrue(hasattr(setup, 'ArchiveScanJob'), 'Asynchronous scan job is missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'archive_worker.py').write_text(
                "import sys,json\nrequest=json.load(sys.stdin)\n"
                "assert sys.argv[1]=='--once'\n"
                "assert request['action']=='archive_scan'\n"
                "print('MAGIC_ARCHIVE_SCAN:'+json.dumps({'ok':True,'data':{'projectID':request['projectID'],'items':[]}}))\n")
            config = root / 'config.json'
            config.write_text(json.dumps({'automatic': False, 'enabled': False}))
            before = config.read_bytes()
            job = setup.ArchiveScanJob(root, {'action': 'archive_scan', 'projectID': 'project'}, python=sys.executable)
            self.addCleanup(job.cancel)
            deadline = time.monotonic() + 5
            result = None
            while result is None and time.monotonic() < deadline:
                result = job.poll()
                time.sleep(0.01)
            self.assertTrue(result['ok'])
            self.assertEqual(result['data']['projectID'], 'project')
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse(job.directory.exists())

    def test_stalled_scan_can_be_cancelled_and_temp_data_removed(self):
        self.assertTrue(hasattr(setup, 'ArchiveScanJob'), 'Asynchronous scan job is missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'archive_worker.py').write_text('import time\ntime.sleep(30)\n')
            job = setup.ArchiveScanJob(root, {'action': 'archive_scan'}, python=sys.executable)
            job.cancel()
            self.assertIsNotNone(job.process.poll())
            self.assertFalse(job.directory.exists())

    def test_manual_scan_completion_does_not_enable_background_service(self):
        self.assertTrue(hasattr(setup.StartWorkApp, 'poll_archive_scan'), 'Scan completion handler is missing')
        app = object.__new__(setup.StartWorkApp)
        app.archive_job = Mock()
        app.archive_job.poll.return_value = {'ok': True, 'data': {'projectID': 'project', 'items': []}}
        app.archive_scan_timer = Mock()
        app.archive_scan_result = None
        app.archive_scan_error = ''
        app.archive_window = Mock()
        app.archive_window.GetItems.return_value = {'BtnArchiveScan': Mock()}
        app.tick_archive_status = Mock()
        with patch.object(setup, 'archive_runtime') as runtime:
            app.poll_archive_scan()
        runtime.assert_not_called()
        self.assertIsNone(app.archive_job)
        self.assertEqual(app.archive_scan_result['scan']['projectID'], 'project')
        app.archive_scan_timer.Stop.assert_called_once_with()

    def test_runtime_prefers_updated_local_component_over_old_app(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            runtime = home / 'Library/Application Support/com.example.MagicTools.Youth/Archive'
            runtime.mkdir(parents=True)
            (runtime / 'archive_service.py').write_text('ARCHIVE_RUNTIME_VERSION = 2\n')
            old = home / 'Applications/Ordo.app'
            resources = old / 'Contents/Resources'
            resources.mkdir(parents=True)
            import plistlib
            (old / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.example.MagicTools.Youth'}))
            (resources / 'archive_service.py').write_text('ARCHIVE_RUNTIME_VERSION = 1\n')
            with patch.object(setup.Path, 'home', return_value=home), \
                 patch.object(setup, 'archive_app_candidates', return_value=[old]):
                service, selected = setup.archive_runtime()
            self.assertEqual(selected, runtime)
            self.assertEqual(service.ARCHIVE_RUNTIME_VERSION, 2)

    def test_timeout_stops_scan_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'archive_worker.py').write_text('import time\ntime.sleep(30)\n')
            job = setup.ArchiveScanJob(root, {}, python=sys.executable)
            self.addCleanup(job.cancel)
            job.started -= 601
            result = job.poll()
            self.assertFalse(result['ok'])
            self.assertIn('超时', result['error'])
            self.assertIsNotNone(job.process.poll())
            self.assertFalse(job.directory.exists())

    def test_closing_window_cancels_only_manual_job(self):
        app = object.__new__(setup.StartWorkApp)
        job = app.archive_job = Mock()
        app.archive_scan_timer = Mock()
        app.archive_window = Mock()
        app.dispatcher = Mock()
        with patch.object(setup, 'archive_runtime') as runtime:
            app.hide_archive_status()
        job.cancel.assert_called_once_with()
        self.assertIsNone(app.archive_job)
        self.assertFalse(app.archive_open)
        runtime.assert_not_called()
        app.dispatcher.ExitLoop.assert_not_called()

    def test_manual_scan_is_project_bound_and_does_not_start_background(self):
        app = object.__new__(setup.StartWorkApp)
        app.busy = False
        app.archive_job = None
        app.archive_scan_timer = Mock()
        app.manager = Mock()
        project = app.manager.GetCurrentProject.return_value
        project.GetUniqueId.return_value = 'project-1'
        app.manager.GetCurrentDatabase.return_value = {'DbType': 'Disk', 'DbName': 'Local'}
        app.archive_window = Mock()
        app.archive_window.GetItems.return_value = {'BtnArchiveScan': Mock()}
        app.tick_archive_status = Mock()
        service = Mock()
        service.ARCHIVE_RUNTIME_VERSION = 2
        service.configuration_path.return_value = Path('/test/state/config.json')
        service.read_config.return_value = {'automatic': False, 'enabled': False, 'projectRoots': ['/project']}
        with patch.object(setup, 'archive_runtime', return_value=(service, Path('/runtime'))), \
             patch.object(setup, 'ArchiveScanJob') as job:
            app.start_archive_scan()
            app.start_archive_scan()
        job.assert_called_once()
        payload = job.call_args.args[1]
        self.assertEqual(payload['action'], 'archive_scan')
        self.assertEqual(payload['projectID'], 'project-1')
        self.assertEqual(payload['database']['DbName'], 'Local')
        self.assertEqual(payload['projectRoots'], ['/project'])
        service.configure_service.assert_not_called()

    def test_manual_result_for_switched_project_is_not_displayed(self):
        app = object.__new__(setup.StartWorkApp)
        app.manager = Mock()
        project = app.manager.GetCurrentProject.return_value
        project.GetUniqueId.return_value = 'project-2'
        project.GetName.return_value = '另一个项目'
        database = {'DbType': 'Disk', 'DbName': 'Local'}
        app.manager.GetCurrentDatabase.return_value = database
        app.archive_scan_result = {'scan': {'projectID': 'project-1', 'database': database,
                                           'items': [{'name': '原项目素材'}]}, 'updated': '状态更新 12:00'}
        with patch.object(setup, 'read_archive_snapshot', return_value={'items': [], 'counts': None, 'status': '暂无扫描'}):
            data = app.archive_window_data()
        self.assertEqual(data['items'], [])
        self.assertIsNone(data['counts'])


if __name__ == '__main__':
    unittest.main()
