from pathlib import Path
import sys
import tempfile
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Ordina'))
import archive_worker as worker


class FakeClip:
    def __init__(self, path, identity='clip-one'):
        self.path = str(path)
        self.identity = identity

    def GetClipProperty(self, key=None):
        properties = {'File Path': self.path, 'Type': 'Video + Audio'}
        return properties.get(key) if key else properties

    def GetUniqueId(self):
        return self.identity


class FakeProject:
    def __init__(self, identity='project-one'):
        self.identity = identity
        self.calls = []
        self.relink = True
        self.clips = []

    def GetRootFolder(self):
        return self

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return []

    def GetUniqueId(self):
        return self.identity

    def GetMediaPool(self):
        return self

    def RelinkClips(self, clips, parent):
        self.calls.append(parent)
        if self.relink:
            clips[0].path = str(Path(parent) / Path(clips[0].path).name)
        return self.relink


class FakeManager:
    def __init__(self, project):
        self.project = project
        self.database = {'DbType': 'PostgreSQL', 'DbName': 'test', 'IpAddress': 'server'}

    def GetCurrentProject(self):
        return self.project

    def GetCurrentDatabase(self):
        return self.database


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.nas = self.base / 'nas'
        self.nas.mkdir()
        self.source = self.base / 'local.mov'
        self.source.write_bytes(b'source content')
        self.clip = FakeClip(self.source)
        self.project = FakeProject()
        self.project.clips = [self.clip]
        self.manager = FakeManager(self.project)
        self.mounts = [(Path('/'), 'apfs'), (self.nas, 'smbfs')]
        self.records = {}
        self.binding_id = self.project.GetUniqueId()
        self.binding_database = self.manager.GetCurrentDatabase().copy()

    def archive(self, **options):
        return worker.archive_clip(self.manager, self.project, self.clip, self.nas, self.mounts, self.records,
                                   options.get('cancelled', lambda: False), options.get('progress', lambda *arguments: None),
                                   self.binding_id, self.binding_database)

    def test_local_file_is_verified_then_relinked(self):
        self.assertEqual(self.archive(), 'archived')
        self.assertTrue(self.source.exists())
        self.assertEqual(Path(self.clip.path).read_bytes(), self.source.read_bytes())
        self.assertEqual(self.records['clip-one']['status'], 'relinked')
        self.assertEqual(self.archive(), 'skipped')
        self.assertEqual(len(self.project.calls), 1)

    def test_watch_only_scans_local_project_without_any_project_writes(self):
        metadata = {'projectID': self.binding_id, 'database': self.binding_database}
        (self.nas / '.magic-resolve.json').write_text(json.dumps(metadata))
        self.project.GetName = lambda: 'Project'
        resolve = type('Resolve', (), {'GetProjectManager': lambda _: self.manager})()
        before = sorted(str(path) for path in self.nas.rglob('*'))
        events = []
        def emit(**event):
            events.append(event)
            worker.running = False
        self.addCleanup(setattr, worker, 'running', True)
        with patch.object(worker, 'keep_running', side_effect=lambda _: worker.running), \
             patch.object(worker, 'emit', side_effect=emit), \
             patch.object(worker, 'archive_clip', side_effect=AssertionError('automatic copy')), \
             patch.object(worker, 'organize_legacy_clip', side_effect=AssertionError('automatic cleanup')), \
             patch.object(worker, 'organize_unreferenced_legacy', side_effect=AssertionError('automatic cleanup')), \
             patch.object(worker, 'cleanup_legacy_directories', side_effect=AssertionError('automatic cleanup')):
            worker.watch(resolve, {'projectRoots': [str(self.nas)]})
        self.assertIn('classifications', events[0])
        self.assertEqual(events[0]['scan']['items'][0]['classification'], 'unknown')
        self.assertEqual(self.project.calls, [])
        self.assertEqual(before, sorted(str(path) for path in self.nas.rglob('*')))

    def test_background_waits_five_seconds_before_next_scan(self):
        import archive_plan
        event = {'projectName': 'Project', 'items': []}
        pauses = []
        events = []
        def emit(**value):
            if 'scan' in value:
                events.append(value)
                if len(events) == 2:
                    worker.running = False
        def sleep(duration):
            pauses.append(duration)
            if sum(pauses) > 6:
                self.fail('Worker did not scan after five seconds')
        self.addCleanup(setattr, worker, 'running', True)
        with patch.object(archive_plan, 'run', return_value=event) as scan, \
             patch.object(worker, 'keep_running', side_effect=lambda _: worker.running), \
             patch.object(worker, 'emit', side_effect=emit), \
             patch.object(worker.time, 'monotonic', side_effect=lambda: sum(pauses)), \
             patch.object(worker.time, 'sleep', side_effect=sleep):
            worker.watch(None, {})
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(sum(pauses), 5)
        self.assertTrue(all(pause <= 0.5 for pause in pauses))

    def test_failed_scan_also_waits_five_seconds_before_retry(self):
        import archive_plan
        pauses = []
        def sleep(duration):
            pauses.append(duration)
            if sum(pauses) >= 5:
                worker.running = False
        self.addCleanup(setattr, worker, 'running', True)
        with patch.object(archive_plan, 'run', side_effect=RuntimeError('存储未连接')) as scan, \
             patch.object(worker, 'keep_running', side_effect=lambda _: worker.running), \
             patch.object(worker, 'emit'), \
             patch.object(worker.time, 'monotonic', side_effect=lambda: sum(pauses)), \
             patch.object(worker.time, 'sleep', side_effect=sleep):
            worker.watch(None, {})
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(sum(pauses), 5)

    def test_interval_change_during_wait_does_not_require_worker_restart(self):
        pauses, events = [], []
        config_file = self.base / 'config.json'
        config_file.write_text(json.dumps({'scanIntervalSeconds': 600}))
        def sleep(duration):
            pauses.append(duration)
            if sum(pauses) == 30:
                config_file.write_text(json.dumps({'scanIntervalSeconds': 60}))
            if sum(pauses) > 61:
                self.fail('Interval change ignored')
        def emit(**event):
            if 'scan' in event:
                events.append(event)
                if len(events) == 2:
                    worker.running = False
        self.addCleanup(setattr, worker, 'running', True)
        with patch.object(worker, 'service_config', config_file), \
             patch.object(worker, 'keep_running', side_effect=lambda _: worker.running), \
             patch.object(worker, 'scan_once', return_value={'projectName': 'Project', 'items': []}) as scan, \
             patch.object(worker, 'emit', side_effect=emit), \
             patch.object(worker.time, 'monotonic', side_effect=lambda: sum(pauses)), \
             patch.object(worker.time, 'sleep', side_effect=sleep):
            worker.watch(None, {})
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(sum(pauses), 60)

    def test_scan_lock_prevents_parallel_background_and_manual_scan(self):
        import fcntl
        import archive_plan
        lock = self.base / '.scan-read.lock'
        self.assertTrue(hasattr(worker, 'scan_once'), 'Shared read-only scan entry is missing')
        with lock.open('a') as stream, patch.object(archive_plan, 'run') as scan:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(worker.ArchiveError, '正在扫描'):
                worker.scan_once(None, {'_scanStateDirectory': str(self.base)})
            scan.assert_not_called()

    def test_one_shot_entry_cannot_execute_copy_action(self):
        import archive_plan
        with patch.object(archive_plan, 'run', return_value={'items': []}) as scan:
            worker.scan_once(None, {'action': 'archive_execute', 'selectedIDs': ['clip']})
        self.assertEqual(scan.call_args.args[1]['action'], 'archive_scan')

    def test_created_project_is_automatically_matched_by_identity(self):
        metadata = {'projectID': self.binding_id, 'database': self.binding_database}
        (self.nas / '.magic-resolve.json').write_text(json.dumps(metadata))
        project, root, identity, database = worker.binding_for(self.manager, [], str(self.base))
        self.assertIs(project, self.project)
        self.assertEqual(root, self.nas)
        self.assertEqual(identity, self.binding_id)
        self.assertEqual(database, self.binding_database)

    def test_unregistered_project_is_not_matched_by_name(self):
        metadata = {'projectID': 'other', 'projectName': 'same-name', 'database': self.binding_database}
        (self.nas / '.magic-resolve.json').write_text(json.dumps(metadata))
        with self.assertRaises(worker.ArchiveError):
            worker.binding_for(self.manager, [], str(self.base))

    def test_ambiguous_identity_is_not_automatically_linked(self):
        metadata = {'projectID': self.binding_id, 'database': self.binding_database}
        (self.nas / '.magic-resolve.json').write_text(json.dumps(metadata))
        another = self.base / 'duplicate'
        another.mkdir()
        (another / '.magic-resolve.json').write_text(json.dumps(metadata))
        with self.assertRaises(worker.ArchiveError):
            worker.binding_for(self.manager, [], str(self.base))

    def test_external_disk_is_skipped(self):
        self.mounts.append((self.base, 'apfs'))
        self.assertEqual(self.archive(), 'skipped')
        self.assertEqual(self.project.calls, [])
        self.assertEqual(list(self.nas.iterdir()), [])

    def test_other_nas_source_is_skipped(self):
        self.mounts.append((self.base, 'smbfs'))
        self.assertEqual(self.archive(), 'skipped')
        self.assertEqual(self.project.calls, [])

    def test_project_switch_does_not_relink(self):
        def progress(*arguments):
            self.manager.project = FakeProject('different-project')
        with self.assertRaises(worker.ArchiveError):
            self.archive(progress=progress)
        self.assertEqual(self.project.calls, [])
        self.assertEqual(self.clip.path, str(self.source))

    def test_source_path_change_does_not_relink(self):
        def progress(*arguments):
            self.clip.path = str(self.base / 'another.mov')
        with self.assertRaises(worker.ArchiveError):
            self.archive(progress=progress)
        self.assertEqual(self.project.calls, [])

    def test_same_project_id_in_another_database_is_rejected(self):
        self.manager.database = dict(self.binding_database, DbName='another-database')
        self.manager.project = FakeProject(self.binding_id)
        with self.assertRaises(worker.ArchiveError):
            self.archive()
        self.assertEqual(self.project.calls, [])

    def test_relink_success_before_journal_commit_is_reconciled(self):
        real_write = worker.write_json
        calls = []
        def interrupted(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('simulated interruption')
            real_write(path, data)
        with patch.object(worker, 'write_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.archive()
        records = json.loads((self.nas / '.magic-archive-history.json').read_text())
        self.assertEqual(records['clip-one']['status'], 'copied')
        self.assertTrue(worker.reconcile_record(self.manager, self.clip, self.nas, records, self.binding_id, self.binding_database))
        self.assertEqual(records['clip-one']['status'], 'relinked')
        self.assertEqual(len(self.project.calls), 1)

    def test_failed_relink_keeps_verified_copy_for_retry(self):
        self.project.relink = False
        with self.assertRaises(worker.ArchiveError):
            self.archive()
        self.assertEqual(self.records['clip-one']['status'], 'copied')
        self.assertTrue(Path(self.records['clip-one']['destination']).is_file())
        self.project.relink = True
        with patch.object(worker, 'verified_copy', side_effect=AssertionError('不应重复复制')):
            self.assertEqual(self.archive(), 'archived')

    def legacy_archive(self):
        folder = self.nas / '3_素材/视频' / ('a' * 64 + '_' + 'b' * 16)
        folder.mkdir(parents=True)
        old = folder / self.source.name
        old.write_bytes(self.source.read_bytes())
        self.clip.path = str(old)
        self.records['clip-one'] = {'source': str(self.source), 'destination': str(old), 'category': '视频',
                                    'sha256': worker.digest(old), 'bytes': old.stat().st_size, 'status': 'relinked'}
        return old

    def organize(self):
        return worker.organize_legacy_clip(self.manager, self.project, self.clip, self.nas, self.records,
                                           self.binding_id, self.binding_database, lambda: False)

    def cleanup(self):
        worker.cleanup_legacy_directories(self.manager, self.project, self.nas, self.records,
                                          self.binding_id, self.binding_database, lambda: False)

    def test_legacy_archive_is_organized_and_relinked(self):
        old = self.legacy_archive()
        self.assertTrue(self.organize())
        self.assertEqual(Path(self.clip.path).parent.name, '归档')
        self.assertTrue(old.exists())
        self.cleanup()
        self.assertFalse(old.parent.exists())
        self.assertTrue(Path(self.records['clip-one']['legacyBackup']).is_file())
        self.assertTrue(self.source.is_file())
        self.assertTrue(Path(self.clip.path).is_file())

    def test_failed_legacy_relink_keeps_old_media(self):
        old = self.legacy_archive()
        self.project.relink = False
        with self.assertRaises(worker.ArchiveError):
            self.organize()
        self.assertTrue(old.is_file())
        self.assertEqual(self.clip.path, str(old))
        self.project.relink = True
        self.assertTrue(self.organize())

    def test_other_nas_media_is_not_organized(self):
        self.clip.path = str(self.nas / '原素材.mov')
        self.assertFalse(self.organize())
        self.assertEqual(self.project.calls, [])

    def test_legacy_directory_is_kept_while_still_referenced(self):
        old = self.legacy_archive()
        another = FakeClip(old)
        self.project.clips.append(another)
        self.organize()
        self.cleanup()
        self.assertTrue(old.is_file())

    def test_all_references_to_owned_legacy_media_are_updated(self):
        old = self.legacy_archive()
        another = FakeClip(old, 'clip-two')
        self.project.clips.append(another)
        self.organize()
        worker.organize_legacy_clip(self.manager, self.project, another, self.nas, self.records,
                                    self.binding_id, self.binding_database, lambda: False)
        self.cleanup()
        self.assertEqual(another.path, self.clip.path)
        self.assertFalse(old.parent.exists())

    def test_unreferenced_owned_legacy_media_is_also_organized(self):
        old = self.legacy_archive()
        self.project.clips = []
        worker.organize_unreferenced_legacy(self.manager, self.project, self.nas, self.records,
                                            self.binding_id, self.binding_database, lambda: False)
        self.cleanup()
        record = self.records['clip-one']
        self.assertEqual(Path(record['destination']).parent.name, '归档')
        self.assertEqual(record['status'], 'archived')
        self.assertFalse(old.parent.exists())
        self.assertTrue(Path(record['legacyBackup']).is_file())
        self.assertEqual(self.project.calls, [])

    def test_deleted_legacy_media_is_not_restored_and_empty_directory_is_hidden(self):
        old = self.legacy_archive()
        old.unlink()
        self.project.clips = []
        worker.organize_unreferenced_legacy(self.manager, self.project, self.nas, self.records,
                                            self.binding_id, self.binding_database, lambda: False)
        self.cleanup()
        self.assertEqual(self.records['clip-one']['status'], 'removed')
        self.assertFalse(old.parent.exists())
        self.assertTrue((self.nas / '3_素材/视频/归档').is_dir())
        self.assertEqual(list((self.nas / '3_素材/视频/归档').iterdir()), [])

    def test_smb_pending_delete_placeholder_is_preserved_in_hidden_backup(self):
        old = self.legacy_archive()
        old.unlink()
        (old.parent / '.smbdeleteAAA123').write_bytes(b'pending delete')
        self.project.clips = []
        worker.organize_unreferenced_legacy(self.manager, self.project, self.nas, self.records,
                                            self.binding_id, self.binding_database, lambda: False)
        self.cleanup()
        backup = Path(self.records['clip-one']['legacyBackupDirectory'])
        self.assertEqual((backup / '.smbdeleteAAA123').read_bytes(), b'pending delete')
        self.assertFalse(old.parent.exists())


if __name__ == '__main__':
    unittest.main()
