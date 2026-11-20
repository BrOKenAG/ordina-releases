import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Ordina'))
import archive_plan as plan
import archive_worker as worker
from test_archive_worker import FakeClip, FakeManager, FakeProject


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.downloads = self.base / 'Downloads'
        self.downloads.mkdir()
        self.source = self.downloads / 'sample.mov'
        self.source.write_bytes(b'original media')
        self.project = FakeProject()
        self.project.GetName = lambda: 'Project'
        self.clip = FakeClip(self.source)
        self.project.clips = [self.clip]
        self.manager = FakeManager(self.project)
        self.resolve = type('Resolve', (), {'GetProjectManager': lambda _: self.manager})()
        self.request = {'projectRoots': [str(self.root)]}
        (self.root / '.magic-resolve.json').write_text(json.dumps({
            'projectID': self.project.identity, 'database': self.manager.database}))
        self.addCleanup(patch.stopall)
        patch.object(plan, 'read_mounts', return_value=[(Path('/'), 'apfs')]).start()
        patch.object(Path, 'home', return_value=self.base).start()

    def run_action(self, action, **request):
        return plan.run(self.resolve, dict(self.request, action=action, **request))

    def scan(self):
        return self.run_action('archive_scan')

    def update(self, **policy):
        return self.run_action('storage_update', **policy)

    def execute(self, preview, **options):
        return self.run_action('archive_execute', snapshot=preview['snapshot'],
                               approvedIDs=[self.clip.identity], **options)

    def location(self, path, kind='raw', identity='raw-one', logical='camera'):
        return {'id': identity, 'kind': kind, 'logicalID': logical, 'path': str(path)}

    def test_local_project_scan_is_read_only_and_json_serializable(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        preview = self.scan()
        self.assertEqual(preview['projectRoot'], str(self.root))
        self.assertEqual(preview['projectID'], self.project.identity)
        self.assertEqual(preview['database'], self.manager.database)
        self.assertEqual(preview['projectName'], 'Project')
        item = preview['items'][0]
        self.assertEqual(item['classification'], 'pending')
        self.assertEqual(item['destination'], str(self.root / '3_素材/视频/归档/sample.mov'))
        self.assertEqual(item['bytes'], self.source.stat().st_size)
        self.assertTrue(item['signature'])
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))
        self.assertEqual(self.project.calls, [])
        json.dumps(preview)

    def test_background_reuses_unchanged_preview_and_detects_relink(self):
        cache = {}
        first = plan.scan_if_changed(self.resolve, self.request, cache)
        self.assertEqual(first['items'][0]['classification'], 'pending')
        with patch.object(plan, 'scan', side_effect=AssertionError('unchanged references triggered full scan')):
            self.assertIsNone(plan.scan_if_changed(self.resolve, self.request, cache))
        new_path = self.root / 'new.mov'
        new_path.write_bytes(b'new media')
        self.clip.path = str(new_path)
        preview = plan.scan_if_changed(self.resolve, self.request, cache)
        self.assertEqual(preview['items'][0]['classification'], 'archived')

    def test_online_nas_is_recognized_without_registering_a_location(self):
        nas = self.base / 'nas'
        nas.mkdir()
        network_file = nas / 'shared.mov'
        network_file.write_bytes(b'media')
        self.clip.path = str(network_file)
        with patch.object(plan, 'read_mounts', return_value=[(Path('/'), 'apfs'), (nas, 'smbfs')]):
            item = self.scan()['items'][0]
        self.assertEqual((item['classification'], item['method']), ('shared', 'keep'))

    def test_automatic_archive_waits_for_stable_playhead_and_only_processes_pending(self):
        external = self.base / 'raw.mov'
        external.write_bytes(b'raw footage')
        external_clip = FakeClip(external, 'external')
        self.project.clips.append(external_clip)
        timeline = type('Timeline', (), {'GetUniqueId': lambda _: 'timeline',
                                         'GetCurrentTimecode': lambda _: '00:00:00:00'})()
        self.project.GetCurrentTimeline = lambda: timeline
        self.project.IsRenderingInProgress = lambda: False
        auto = worker.AutomaticArchive()
        scan = self.scan()
        with patch.object(worker.time, 'monotonic', return_value=10):
            self.assertIsNone(auto.advance(self.resolve, scan, self.base, lambda: False))
            self.assertIsNone(auto.advance(self.resolve, scan, self.base, lambda: True))
        self.assertEqual(self.project.calls, [])
        with patch.object(worker.time, 'monotonic', return_value=15), patch.object(worker, 'emit'):
            result = auto.advance(self.resolve, scan, self.base, lambda: True)
        self.assertEqual(result['actualResult'], 'relinked')
        self.assertEqual(external_clip.path, str(external))
        self.assertTrue(self.source.is_file())

    def test_background_periodically_rechecks_offline_files(self):
        cache = {}
        with patch.object(plan.time, 'monotonic', return_value=10):
            plan.scan_if_changed(self.resolve, self.request, cache)
        self.source.unlink()
        with patch.object(plan.time, 'monotonic', return_value=70):
            preview = plan.scan_if_changed(self.resolve, self.request, cache)
        self.assertEqual(preview['items'][0]['classification'], 'offline')

    def test_large_collision_preview_uses_metadata_only(self):
        with self.source.open('r+b') as handle:
            handle.truncate(2 * 1024 * 1024)
        destination = self.root / '3_素材/视频/归档' / self.source.name
        destination.parent.mkdir(parents=True)
        with destination.open('wb') as handle:
            handle.truncate(self.source.stat().st_size)
        self.project.clips.append(FakeClip(self.source, 'duplicate-reference'))
        with patch.object(plan, 'digest', side_effect=AssertionError('preview read large media')):
            items = self.scan()['items']
        self.assertEqual(items[0]['destination'], items[1]['destination'])
        self.assertIn('同名素材 2', items[0]['destination'])
        self.assertTrue(all(item['method'] == 'copy' for item in items))

    def test_audio_uses_resolve_duration_instead_of_opening_the_file(self):
        self.clip.GetClipProperty = lambda key=None: (str(self.source) if key == 'File Path' else {
            'File Path': str(self.source), 'Type': '音频', 'Duration': '00:02:32:34', 'FPS': 25.0})
        item = self.scan()['items'][0]
        self.assertEqual(item['category'], '音乐')
        self.assertEqual(item['method'], 'copy')

    def test_readonly_scan_bounds_shared_parent_probes_without_skipping_items(self):
        media = self.base / 'external-media'
        media.mkdir()
        self.project.clips = []
        for index in range(279):
            source = media / ('video-%d.mov' % index)
            source.write_bytes(b'fixture')
            self.project.clips.append(FakeClip(source, 'clip-%d' % index))
        probes = []
        resolutions = []
        original_probe, original_resolve = Path.is_symlink, Path.resolve
        def probe(path):
            probes.append(path)
            return original_probe(path)
        def resolved(path, *args, **kwargs):
            resolutions.append(path)
            return original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'is_symlink', probe), patch.object(Path, 'resolve', resolved), \
             patch.object(plan, 'digest', side_effect=AssertionError('no collision must not hash media')):
            preview = self.scan()
        self.assertEqual(len(preview['items']), 279)
        self.assertTrue(all(item['method'] == 'copy' for item in preview['items']))
        self.assertTrue(all(item['classification'] == 'unknown' for item in preview['items']))
        self.assertLessEqual(probes.count(media), 2)
        self.assertLess(len(resolutions), 40)

    def test_readonly_path_cache_has_fixed_capacity_and_matches_strict_preview(self):
        cache = plan.ScanPaths(limit=4)
        for index in range(12):
            directory = self.base / ('directory-%d' % index)
            directory.mkdir()
            cache.absolute(str(directory))
            cache.available(directory, [(Path('/'), 'apfs')])
            self.assertLessEqual(len(cache.symlinks), 4)
            self.assertLessEqual(len(cache.directories), 4)
        self.assertEqual(self.scan(), plan.scan(self.resolve, self.request, cache_reads=False))

    def test_scan_parent_cache_expires_before_next_scan(self):
        preview = self.scan()
        self.assertEqual(preview['items'][0]['method'], 'copy')
        moved = self.base / 'moved-downloads'
        self.downloads.rename(moved)
        self.downloads.symlink_to(moved, target_is_directory=True)
        second = self.scan()
        self.assertEqual(second['items'][0]['method'], 'none')
        self.assertIn('符号链接', second['items'][0]['warning'])

    def test_execute_probes_parents_for_each_selected_item(self):
        second_source = self.downloads / 'second.mov'
        second_source.write_bytes(b'second')
        self.project.clips.append(FakeClip(second_source, 'clip-two'))
        preview = self.scan()
        original_probe = Path.is_symlink
        probes = []
        def probe(path):
            probes.append(path)
            return original_probe(path)
        with patch.object(Path, 'is_symlink', probe):
            result = self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two'], preview=preview)
        self.assertEqual([item['status'] for item in result['results']], ['archived', 'archived'])
        self.assertGreaterEqual(probes.count(self.downloads), 5)

    def test_registered_raw_shared_trusted_never_copy_even_if_approved(self):
        external = self.base / 'external'
        external.mkdir()
        self.clip.path = str(external / 'sample.mov')
        Path(self.clip.path).write_bytes(b'raw')
        for kind in ('raw', 'shared', 'trusted'):
            with self.subTest(kind=kind):
                self.update(locations=[self.location(external, kind)])
                preview = self.scan()
                self.assertEqual(preview['items'][0]['classification'], kind)
                self.assertEqual(preview['items'][0]['method'], 'keep')
                self.execute(preview)
                self.assertEqual(self.project.calls, [])
                self.assertFalse((self.root / '3_素材').exists())

    def test_registered_temp_is_pending_and_keep_decision_is_respected(self):
        self.update(locations=[self.location(self.downloads, 'temp')],
                    decisions={self.clip.identity: 'keep'})
        preview = self.scan()
        self.assertEqual(preview['items'][0]['classification'], 'pending')
        self.assertEqual(preview['items'][0]['method'], 'keep')
        self.execute(preview)
        self.assertEqual(self.project.calls, [])
        stored = json.loads((self.root / '.ordo-storage.json').read_text())
        self.assertEqual(stored['version'], 1)

    def test_unknown_requires_separate_explicit_approval(self):
        self.source = self.source.rename(self.base / 'unknown.mov')
        self.clip.path = str(self.source)
        preview = self.scan()
        self.assertEqual(preview['items'][0]['classification'], 'unknown')
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.root / '3_素材').exists())
        self.execute(preview, approvedUnknownIDs=[self.clip.identity])
        self.assertTrue(self.source.is_file())
        self.assertEqual(Path(self.clip.path).read_bytes(), self.source.read_bytes())

    def test_unchecked_ids_are_excluded(self):
        self.execute(self.scan(), uncheckedIDs=[self.clip.identity])
        self.assertEqual(self.project.calls, [])
        self.assertFalse((self.root / '3_素材').exists())

    def test_swift_selected_ids_and_full_preview_contract_reports_actual_result(self):
        preview = self.scan()
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity], preview=preview)
        actual = result['results'][0]
        self.assertEqual(actual['status'], 'archived')
        self.assertEqual(actual['actualResult'], 'relinked')
        self.assertEqual(actual['actualSource'], self.clip.path)
        self.assertTrue(actual['originalRetained'])
        self.assertFalse(actual['relinkFailed'])
        self.assertEqual(result['retainedOriginals'], [str(self.source)])

    def test_swift_contract_failed_relink_reports_retained_copy(self):
        self.project.relink = False
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity], preview=self.scan())
        actual = result['results'][0]
        self.assertEqual(actual['actualResult'], 'copy_retained')
        self.assertTrue(actual['originalRetained'])
        self.assertTrue(actual['relinkFailed'])
        self.assertEqual(actual['actualSource'], str(self.source))
        self.assertEqual(Path(actual['destination']).read_bytes(), self.source.read_bytes())

    def test_unknown_swift_contract_requires_unknown_approval(self):
        self.source = self.source.rename(self.base / 'unknown.mov')
        self.clip.path = str(self.source)
        preview = self.scan()
        with self.assertRaises(plan.ArchiveError):
            self.run_action('archive_execute', selectedIDs=[self.clip.identity], preview=preview)
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity], preview=preview,
                                 approvedUnknownIDs=[self.clip.identity])
        self.assertEqual(result['results'][0]['actualResult'], 'relinked')

    def test_unselected_name_collision_does_not_block_selected_destination(self):
        other = self.base / 'Desktop' / self.source.name
        other.parent.mkdir()
        other.write_bytes(b'different content')
        self.project.clips.append(FakeClip(other, 'clip-two'))
        preview = self.scan()
        destination = preview['items'][1]['destination']
        self.assertIn('同名素材 2', destination)
        result = self.run_action('archive_execute', selectedIDs=['clip-two'], preview=preview)
        self.assertEqual(result['results'][0]['status'], 'archived')
        self.assertEqual(Path(destination).read_bytes(), other.read_bytes())
        self.assertEqual(self.clip.path, str(self.source))

    def test_batch_same_name_different_content_uses_approved_paths(self):
        other = self.base / 'Desktop' / self.source.name
        other.parent.mkdir()
        other.write_bytes(b'different content')
        another_clip = FakeClip(other, 'clip-two')
        self.project.clips.append(another_clip)
        preview = self.scan()
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two'], preview=preview)
        self.assertEqual([item['status'] for item in result['results']], ['archived', 'archived'])
        self.assertEqual(Path(self.clip.path).read_bytes(), self.source.read_bytes())
        self.assertEqual(Path(another_clip.path).read_bytes(), other.read_bytes())
        self.assertNotEqual(self.clip.path, another_clip.path)

    def test_batch_identical_references_reuse_only_verified_content(self):
        another_clip = FakeClip(self.source, 'clip-two')
        self.project.clips.append(another_clip)
        preview = self.scan()
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two'], preview=preview)
        self.assertEqual([item['status'] for item in result['results']], ['archived', 'archived'])
        self.assertEqual(self.clip.path, another_clip.path)
        self.assertTrue(self.source.exists())

    def test_batch_numbered_destination_reuse_allows_collision_warning_transition(self):
        other = self.base / 'Desktop' / self.source.name
        other.parent.mkdir()
        other.write_bytes(b'different content')
        repeated = self.downloads / 'another' / self.source.name
        repeated.parent.mkdir()
        repeated.write_bytes(other.read_bytes())
        second = FakeClip(other, 'clip-two')
        third = FakeClip(repeated, 'clip-three')
        self.project.clips.extend([second, third])
        preview = self.scan()
        self.assertEqual(preview['items'][1]['destination'], preview['items'][2]['destination'])
        self.assertIn('分流', preview['items'][2]['warning'])
        result = self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two', 'clip-three'], preview=preview)
        self.assertEqual([item['status'] for item in result['results']], ['archived'] * 3)
        self.assertEqual(second.path, third.path)
        self.assertNotEqual(self.clip.path, second.path)
        self.assertEqual(Path(second.path).read_bytes(), other.read_bytes())
        self.assertTrue(all(path.exists() for path in (self.source, other, repeated)))

    def test_batch_reuse_rejects_copy_changed_after_first_relink(self):
        second = FakeClip(self.source, 'clip-two')
        self.project.clips.append(second)
        preview = self.scan()
        original_relink = self.project.RelinkClips
        def changed_after_relink(clips, parent):
            result = original_relink(clips, parent)
            Path(clips[0].path).write_bytes(b'tampered copy')
            return result
        with patch.object(self.project, 'RelinkClips', side_effect=changed_after_relink):
            result = self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two'], preview=preview)
        self.assertEqual(result['results'][1]['status'], 'error')
        self.assertEqual(second.path, str(self.source))
        self.assertEqual(len(self.project.calls), 1)
        self.assertEqual(Path(preview['items'][0]['destination']).read_bytes(), b'tampered copy')
        self.assertTrue(self.source.exists())

    def test_unrelated_stale_selected_item_rejects_entire_batch_before_copy(self):
        other = self.downloads / 'second.mov'
        other.write_bytes(b'media')
        self.project.clips.append(FakeClip(other, 'clip-two'))
        preview = self.scan()
        other.write_bytes(b'changed')
        with self.assertRaises(plan.ArchiveError):
            self.run_action('archive_execute', selectedIDs=[self.clip.identity, 'clip-two'], preview=preview)
        self.assertFalse((self.root / '3_素材').exists())
        self.assertEqual(self.project.calls, [])

    def test_relink_returns_success_without_confirmed_path_reports_failure(self):
        self.project.RelinkClips = lambda clips, parent: True
        result = self.execute(self.scan())
        self.assertEqual(result['results'][0]['actualResult'], 'copy_retained')
        self.assertTrue(result['results'][0]['relinkFailed'])
        self.assertTrue(self.source.exists())

    def test_relink_api_exception_reports_retained_copy_per_item(self):
        self.project.RelinkClips = lambda clips, parent: (_ for _ in ()).throw(RuntimeError('Resolve disconnected'))
        result = self.execute(self.scan())
        actual = result['results'][0]
        self.assertEqual(actual['actualResult'], 'copy_retained')
        self.assertTrue(actual['relinkFailed'])
        self.assertTrue(actual['originalRetained'])
        self.assertEqual(actual['warning'], 'Resolve disconnected')
        self.assertTrue(Path(actual['destination']).exists())

    def test_successful_retry_clears_pending_relink_warning(self):
        self.project.relink = False
        self.execute(self.scan())
        self.project.relink = True
        result = self.execute(self.scan())
        self.assertEqual(result['results'][0]['warning'], '')

    def test_unknown_default_archive_decision_is_not_explicit_approval(self):
        self.source = self.source.rename(self.base / 'unknown.mov')
        self.clip.path = str(self.source)
        self.update(decisions={self.clip.identity: 'archive'})
        preview = self.scan()
        with self.assertRaises(plan.ArchiveError):
            self.run_action('archive_execute', selectedIDs=[self.clip.identity], preview=preview)
        self.assertEqual(self.project.calls, [])
        self.assertFalse((self.root / '3_素材').exists())

    def test_decisions_update_preserves_disconnected_registered_location(self):
        external = self.base / 'disk'
        external.mkdir()
        self.update(locations=[self.location(external)])
        external.rmdir()
        result = self.update(decisions={self.clip.identity: 'keep'})
        self.assertEqual(result['locations'][0]['path'], str(external))
        self.assertFalse(external.exists())

    def test_remove_location_preserves_files_other_locations_and_decisions(self):
        raw = self.base / 'originals'
        raw.mkdir()
        original = raw / 'untouched.mov'
        original.write_bytes(b'original')
        self.update(locations=[self.location(raw), self.location(self.downloads, 'temp', 'temp-one')],
                    decisions={self.clip.identity: 'keep'})
        result = self.update(removeLocation='raw-one')
        self.assertEqual([item['id'] for item in result['locations']], ['temp-one'])
        self.assertEqual(result['decisions'], {self.clip.identity: 'keep'})
        self.assertEqual(original.read_bytes(), b'original')
        with self.assertRaises(plan.ArchiveError):
            self.update(removeLocation='raw-one')

    def test_new_missing_storage_location_is_rejected(self):
        missing = self.base / 'missing'
        with self.assertRaises(plan.ArchiveError):
            self.update(locations=[self.location(missing)])
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / '.ordo-storage.json').exists())

    def test_duplicate_bindings_require_explicit_identity_matched_root(self):
        duplicate = self.base / 'duplicate'
        duplicate.mkdir()
        (duplicate / '.magic-resolve.json').write_bytes((self.root / '.magic-resolve.json').read_bytes())
        self.request['projectRoots'].append(str(duplicate))
        with self.assertRaises(plan.ArchiveError):
            self.scan()
        result = self.run_action('archive_scan', projectRoot=str(self.root))
        self.assertEqual(result['projectRoot'], str(self.root))

    def test_root_replaced_after_preview_is_rejected(self):
        preview = self.scan()
        moved = self.root.with_name('moved')
        self.root.rename(moved)
        self.root.mkdir()
        (self.root / '.magic-resolve.json').write_bytes((moved / '.magic-resolve.json').read_bytes())
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.root / '3_素材').exists())

    def test_changed_clip_path_rejects_preview(self):
        preview = self.scan()
        alternate = self.downloads / 'alternate.mov'
        alternate.write_bytes(self.source.read_bytes())
        self.clip.path = str(alternate)
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.root / '3_素材').exists())

    def test_unsafe_approved_destination_cannot_escape_current_verified_copy(self):
        destination = self.downloads / 'escape.mov'
        with self.assertRaises(plan.ArchiveError):
            plan.verified_copy(self.source, self.root, '视频', expected_destination=str(destination))
        self.assertFalse(destination.exists())

    def test_source_change_between_preflight_and_verified_copy_is_rejected(self):
        original_copy = plan.verified_copy
        def changed(*args, **kwargs):
            self.source.write_bytes(b'replaced before copy')
            return original_copy(*args, **kwargs)
        preview = self.scan()
        with patch.object(plan, 'verified_copy', side_effect=changed):
            result = self.execute(preview)
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertFalse((self.root / '3_素材').exists())
        self.assertEqual(self.project.calls, [])

    def test_approved_destination_changed_during_copy_is_not_rerouted(self):
        original_copy = plan.verified_copy
        preview = self.scan()
        destination = Path(preview['items'][0]['destination'])
        def changed(*args, **kwargs):
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b'unapproved media')
            return original_copy(*args, **kwargs)
        with patch.object(plan, 'verified_copy', side_effect=changed):
            result = self.execute(preview)
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertEqual(destination.read_bytes(), b'unapproved media')
        self.assertEqual(len(list(self.root.rglob('*.mov'))), 1)
        self.assertEqual(self.project.calls, [])

    def test_archived_project_media_and_history_are_not_reorganized(self):
        legacy = self.root / '3_素材/视频' / ('a' * 64 + '_' + 'b' * 16) / 'sample.mov'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(self.source.read_bytes())
        self.clip.path = str(legacy)
        history = {self.clip.identity: {'destination': str(legacy), 'status': 'relinked'}}
        (self.root / '.magic-archive-history.json').write_text(json.dumps(history))
        preview = self.scan()
        self.assertEqual(preview['items'][0]['classification'], 'archived')
        self.execute(preview)
        self.assertTrue(legacy.exists())
        self.assertEqual(self.project.calls, [])
        self.assertEqual(json.loads((self.root / '.magic-archive-history.json').read_text()), history)

    def test_symlink_escape_introduced_after_preview_never_writes_external(self):
        preview = self.scan()
        (self.root / '3_素材').symlink_to(self.downloads, target_is_directory=True)
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.downloads / '视频').exists())
        self.assertEqual(self.project.calls, [])

    def test_disconnected_location_and_missing_mount_are_offline(self):
        missing = self.base / 'unplugged'
        (self.root / '.ordo-storage.json').write_text(json.dumps({
            'version': 1, 'locations': [self.location(missing)], 'decisions': {}}))
        self.clip.path = str(missing / 'clip.mov')
        preview = self.scan()
        self.assertEqual(preview['items'][0]['classification'], 'offline')
        self.assertFalse(preview['locations'][0]['online'])
        self.execute(preview)
        self.assertFalse(missing.exists())
        self.clip.path = '/Volumes/Unplugged/clip.mov'
        with patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(self.scan()['items'][0]['classification'], 'offline')
        self.assertEqual(self.project.calls, [])

    def test_same_logical_id_maps_relative_paths_without_content_equality(self):
        raw = self.base / 'raw'
        mirror = self.base / 'mirror'
        raw.mkdir()
        mirror.mkdir()
        first = raw / 'same.mov'
        first.write_bytes(b'original')
        (mirror / first.name).write_bytes(b'completely different')
        self.clip.path = str(first)
        self.update(locations=[self.location(raw), self.location(mirror, identity='raw-two')])
        preview = self.scan()
        item = preview['items'][0]
        self.assertEqual(item['relativePath'], first.name)
        self.assertEqual(item['logicalID'], 'camera')
        self.assertEqual(item['correspondences'][0]['path'], str(mirror / first.name))
        self.assertIs(item['correspondences'][0]['verified'], False)
        self.execute(preview)
        self.assertEqual(self.clip.path, str(first))
        self.assertEqual(self.project.calls, [])

    def test_many_external_clips_suggest_parent_without_registration(self):
        external = self.base / 'disk'
        external.mkdir()
        for index in range(3):
            source = external / ('clip%d.mov' % index)
            source.write_bytes(b'media')
            self.project.clips.append(FakeClip(source, 'ext%d' % index))
        with patch.object(plan, 'read_mounts', return_value=[(Path('/'), 'apfs'), (external, 'apfs')]):
            preview = self.scan()
        self.assertTrue(any(s['path'] == str(external) for s in preview['suggestions']))
        self.assertEqual(preview['locations'], [])
        self.assertFalse((self.root / '.ordo-storage.json').exists())

    def test_source_changes_after_preview_reject_entire_request(self):
        preview = self.scan()
        self.source.write_bytes(b'changed')
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertEqual(self.project.calls, [])
        self.assertFalse((self.root / '3_素材').exists())

    def test_destination_changes_after_preview_are_rejected_without_overwrite(self):
        preview = self.scan()
        destination = Path(preview['items'][0]['destination'])
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'other media')
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertEqual(destination.read_bytes(), b'other media')
        self.assertEqual(self.project.calls, [])

    def test_database_or_project_switch_rejects_preview(self):
        preview = self.scan()
        self.manager.database = dict(self.manager.database, DbName='other')
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.manager.database = preview['database']
        self.manager.project = FakeProject('different')
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.root / '3_素材').exists())

    def test_project_switch_during_copy_preserves_source_and_skips_relink(self):
        preview = self.scan()
        original_copy = plan.verified_copy
        def switched(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            self.manager.project = FakeProject('switched')
            return result
        with patch.object(plan, 'verified_copy', side_effect=switched):
            result = self.execute(preview)
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertTrue(self.source.is_file())
        self.assertEqual(self.project.calls, [])

    def test_failed_relink_keeps_verified_copy_and_history_for_retry(self):
        self.project.relink = False
        result = self.execute(self.scan())
        self.assertEqual(result['results'][0]['status'], 'error')
        history = json.loads((self.root / '.magic-archive-history.json').read_text())
        record = history[self.clip.identity]
        self.assertEqual(record['status'], 'copied')
        self.assertEqual(Path(record['destination']).read_bytes(), self.source.read_bytes())
        self.assertEqual(self.clip.path, str(self.source))
        self.project.relink = True
        self.execute(self.scan())
        self.assertTrue(self.source.is_file())
        self.assertEqual(self.clip.path, record['destination'])

    def test_policy_change_invalidates_preview(self):
        preview = self.scan()
        self.update(decisions={self.clip.identity: 'keep'})
        with self.assertRaises(plan.ArchiveError):
            self.execute(preview)
        self.assertFalse((self.root / '3_素材').exists())

    def test_storage_rejects_broad_relative_traversal_and_symlink_roots(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.downloads, target_is_directory=True)
        for path in ('/', str(self.base), 'relative/path', str(self.downloads / '..' / 'Downloads'), str(alias)):
            with self.subTest(path=path), self.assertRaises(plan.ArchiveError):
                self.update(locations=[self.location(path)])
        self.assertFalse((self.root / '.ordo-storage.json').exists())

    def test_missing_project_is_never_recreated(self):
        (self.root / '.magic-resolve.json').unlink()
        self.root.rmdir()
        for action in ('archive_scan', 'archive_execute', 'storage_update'):
            with self.subTest(action=action), self.assertRaises(plan.ArchiveError):
                self.run_action(action)
        self.assertFalse(self.root.exists())

    def test_explicit_project_root_cannot_bypass_binding(self):
        other = self.base / 'wrong'
        other.mkdir()
        (other / '.magic-resolve.json').write_text(json.dumps({
            'projectID': 'other', 'projectName': 'Project', 'database': self.manager.database}))
        with self.assertRaises(plan.ArchiveError):
            self.run_action('archive_scan', projectRoot=str(other))


if __name__ == '__main__':
    unittest.main()
