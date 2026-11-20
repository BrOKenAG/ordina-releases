import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('setup_status', Path(__file__).resolve().parents[1] / 'Integrations/Project Setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class StatusSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'config.json'
        self.database = {'DbType': 'Disk', 'DbName': 'Local', 'IpAddress': None}
        self.config = {'instance': 'current', 'automatic': True, 'enabled': True, 'pid': 12345}
        self.state = {'instance': 'current', 'running': True, 'status': '监测中', 'updatedAt': 1793664000,
                      'scan': {'projectID': 'project-1', 'projectName': '城市漫游', 'database': self.database,
                               'items': [{'name': 'a.wav', 'classification': 'pending', 'source': '/Downloads/a.wav'},
                                         {'name': 'camera.mov', 'classification': 'raw', 'source': '/Volumes/SSD/RAW/camera.mov'},
                                         {'name': 'title.mov', 'classification': 'archived', 'source': '/project/title.mov'}]}}

    def read(self, identity='project-1', database=None):
        self.path.write_text(json.dumps(self.config))
        self.path.with_name('status.json').write_text(json.dumps(self.state))
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        result = setup.read_archive_snapshot(self.path, project_id=identity,
                                             database=database or self.database, project_name='当前工程', alive=lambda pid: True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.iterdir()})
        return result

    def test_snapshot_reads_real_classifications_without_writes(self):
        result = self.read()
        self.assertEqual(result['project'], '当前工程')
        self.assertEqual(result['status'], '扫描完成')
        self.assertEqual(result['counts']['pending'], 1)
        self.assertEqual(result['counts']['raw'], 1)
        self.assertEqual(len(result['items']), 3)
        self.assertIn('状态更新', result['updated'])

    def test_old_service_instance_is_not_displayed_as_current_data(self):
        self.state['instance'] = 'old'
        result = self.read()
        self.assertEqual(result['items'], [])
        self.assertIsNone(result['counts'])

    def test_other_project_with_same_name_is_not_current_data(self):
        result = self.read(identity='project-2')
        self.assertEqual(result['items'], [])
        self.assertIn('当前工程', result['status'])

    def test_same_project_id_in_different_database_is_rejected(self):
        result = self.read(database={'DbType': 'Disk', 'DbName': 'Other', 'IpAddress': None})
        self.assertEqual(result['items'], [])
        self.assertIsNone(result['counts'])

    def test_closed_project_does_not_show_cached_project_materials(self):
        result = self.read(identity=None)
        self.assertEqual(result['status'], '未打开项目')
        self.assertEqual(result['items'], [])

    def test_disabled_background_preserves_only_labeled_previous_result(self):
        self.config['automatic'] = False
        result = self.read()
        self.assertEqual(result['status'], '后台归档已关闭')
        self.assertEqual(result['resultLabel'], '上次扫描')
        self.assertEqual(len(result['items']), 3)

    def test_latest_failure_does_not_claim_old_scan_is_fresh(self):
        self.state['status'] = 'NAS 不可访问'
        result = self.read()
        self.assertEqual(result['status'], 'NAS 不可访问')
        self.assertEqual(result['resultLabel'], '上次扫描')

    def test_corrupt_or_missing_status_is_clear_and_read_only(self):
        self.path.write_text('bad json')
        result = setup.read_archive_snapshot(self.path, project_id='project-1', database=self.database,
                                             project_name='当前工程', alive=lambda pid: False)
        self.assertIsNone(result['counts'])
        self.assertEqual(result['items'], [])
        self.assertFalse(self.path.with_name('status.json').exists())

    def test_dead_service_never_labels_old_result_as_fresh(self):
        self.read()
        result = setup.read_archive_snapshot(self.path, project_id='project-1', database=self.database,
                                             project_name='当前工程', alive=lambda pid: False)
        self.assertEqual(result['status'], '后台归档未运行')
        self.assertEqual(result['resultLabel'], '上次扫描')
        self.assertEqual(len(result['items']), 3)

    def test_legacy_service_does_not_fabricate_material_counts(self):
        self.state.pop('scan')
        self.state.update(archived=99, pending=2)
        result = self.read()
        self.assertIsNone(result['counts'])
        self.assertEqual(result['items'], [])
        self.assertIn('当前工程', result['status'])

    def test_invalid_timestamp_does_not_hide_valid_materials(self):
        self.state['updatedAt'] = float('nan')
        result = self.read()
        self.assertEqual(result['updated'], '')
        self.assertEqual(len(result['items']), 3)


class StatusWindowTests(unittest.TestCase):
    def controller(self):
        app = object.__new__(setup.StartWorkApp)
        app.archive_window = Mock()
        app.archive_window.GetItems.return_value = {'BtnArchiveScan': Mock()}
        app.archive_open = False
        app.archive_job = None
        app.archive_scan_timer = Mock()
        app.busy = False
        app.dispatcher = Mock()
        app.archive_window_data = Mock(return_value={'project': '城市漫游', 'status': '扫描完成'})
        app.render_archive_status = Mock()
        return app

    def test_repeated_open_reuses_local_window(self):
        app = self.controller()
        app.show_archive_status()
        app.show_archive_status()
        self.assertTrue(app.archive_open)
        self.assertEqual(app.archive_window.Show.call_count, 2)
        app.dispatcher.AddWindow.assert_not_called()
        self.assertEqual(app.render_archive_status.call_count, 2)

    def test_closing_status_does_not_exit_project_setup_event_loop(self):
        app = self.controller()
        app.archive_open = True
        app.hide_archive_status()
        self.assertFalse(app.archive_open)
        app.archive_window.Hide.assert_called_once_with()
        app.dispatcher.ExitLoop.assert_not_called()

    def test_hidden_panel_does_not_keep_reading_status(self):
        app = self.controller()
        app.tick_archive_status()
        app.archive_window_data.assert_not_called()

    def test_reading_status_does_not_traverse_resolve_materials(self):
        app = object.__new__(setup.StartWorkApp)
        app.manager = Mock()
        project = app.manager.GetCurrentProject.return_value
        project.GetUniqueId.return_value = 'current'
        project.GetName.return_value = '城市漫游'
        app.manager.GetCurrentDatabase.return_value = {'DbType': 'Disk', 'DbName': 'Local'}
        with patch.object(setup, 'read_archive_snapshot', return_value={'status': '扫描完成'}) as snapshot:
            app.archive_window_data()
        project.GetMediaPool.assert_not_called()
        snapshot.assert_called_once_with(project_id='current', project_name='城市漫游',
                                         database={'DbType': 'Disk', 'DbName': 'Local'})

    def test_refresh_failure_clears_previous_project_result(self):
        app = self.controller()
        app.archive_open = True
        app.archive_window_data.side_effect = RuntimeError('Resolve 不可用')
        app.tick_archive_status()
        data = app.render_archive_status.call_args.args[0]
        self.assertEqual(data['items'], [])
        self.assertIsNone(data['counts'])
        self.assertIn('Resolve 不可用', data['status'])

    def test_large_cached_list_limits_display_without_losing_counts(self):
        app = self.controller()
        labels = {name: Mock() for name in ('ArchiveProject', 'ArchiveState', 'ArchiveUpdated',
                  'ArchiveCounts', 'ArchiveEmpty', 'ArchiveLimit', 'ArchiveMaterials', 'ArchiveDestination',
                  'BtnArchiveScan', 'BtnArchiveRaw', 'BtnArchiveKeep', 'BtnArchiveReview', 'BtnArchiveClose')}
        labels['ArchiveShowAll'] = Mock(Checked=True)
        app.archive_window.GetItems.return_value = labels
        labels['ArchiveMaterials'].NewItem.side_effect = lambda: type('TreeRow', (), {'Text': {}})()
        data = {'project': '项目', 'status': '扫描完成', 'updated': '', 'resultLabel': '扫描结果',
                'counts': {kind: 0 for kind in setup.ARCHIVE_CLASSIFICATIONS},
                'items': [{'name': str(i), 'source': '/RAW/' + str(i), 'classification': 'raw'} for i in range(250)]}
        data['counts']['raw'] = 250
        setup.StartWorkApp.render_archive_status(app, data)
        self.assertEqual(labels['ArchiveMaterials'].AddTopLevelItem.call_count, 200)
        self.assertIn('250', labels['ArchiveLimit'].Text)
        self.assertIn('原始素材 250', labels['ArchiveCounts'].Text)
        labels['ArchiveShowAll'].Checked = False
        labels['ArchiveMaterials'].AddTopLevelItem.reset_mock()
        setup.StartWorkApp.render_archive_status(app, data)
        labels['ArchiveMaterials'].AddTopLevelItem.assert_not_called()


if __name__ == '__main__':
    unittest.main()
