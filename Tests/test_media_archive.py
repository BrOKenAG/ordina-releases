import importlib.util
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("media_archive", Path(__file__).resolve().parents[1] / "Ordina/media_archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "local" / "素材.mov"
        self.source.parent.mkdir()
        self.source.write_bytes(b"original media" * 500)
        self.nas = self.base / "nas"
        self.nas.mkdir()

    def test_audio_duration_boundary(self):
        for duration, expected in [(1, "音效"), (60, "音效"), (60.001, "音乐"), (180, "音乐")]:
            self.assertEqual(archive.classify({"Type": "Audio"}, duration), expected)
        self.assertEqual(archive.classify({"Type": "Video + Audio"}, 180), "视频")
        self.assertEqual(archive.classify({"Type": "Still"}, None), "图片")
        self.assertEqual(archive.classify({"Type": "静帧"}, None), "图片")
        self.assertEqual(archive.classify({"Type": "音频"}, 127), "音乐")
        self.assertEqual(archive.classify({"Type": "音频"}, 60), "音效")
        self.assertEqual(archive.classify({"Type": "视频+音频"}, None), "视频")
        with self.assertRaises(archive.ArchiveError):
            archive.classify({"Type": "Audio"}, None)

    def test_actual_audio_duration_drives_classification(self):
        path = self.base / 'audio.wav'
        for frames, expected in [(480000, '音效'), (480001, '音乐')]:
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(1)
                stream.setframerate(8000)
                stream.writeframes(bytes(frames))
            duration = archive.audio_duration(path, {})
            self.assertEqual(archive.classify({'Type': 'Audio'}, duration), expected)

    def test_network_mounts_are_detected(self):
        roots = archive.parse_network_mounts('//user@server/share on /Volumes/NAS (smbfs, nodev)\nserver:/data on /Volumes/Other NAS (nfs, nodev)\n/dev/disk1 on /Volumes/SSD (apfs, local)')
        self.assertEqual(roots, [Path('/Volumes/NAS'), Path('/Volumes/Other NAS')])
        self.assertTrue(archive.is_within(Path('/Volumes/NAS/a.mov'), roots))
        self.assertFalse(archive.is_within(Path('/Volumes/NAS-other/a.mov'), roots))

    def test_only_startup_disk_files_are_eligible(self):
        # Synthetic mount-table paths; no user directory is read.
        mounts = archive.parse_mounts('/dev/disk1 on / (apfs, local)\n/dev/disk2 on /Volumes/SSD (apfs, local)\nserver:/share on /example/ordina-editor/NAS (nfs, nodev)')
        self.assertTrue(archive.is_local_source(Path('/example/ordina-editor/Downloads/a.mov'), mounts))
        self.assertFalse(archive.is_local_source(Path('/Volumes/SSD/a.mov'), mounts))
        self.assertFalse(archive.is_local_source(Path('/example/ordina-editor/NAS/a.mov'), mounts))
        self.assertFalse(archive.is_local_source(Path('/Volumes/unmounted/a.mov'), mounts))
        self.assertFalse(archive.is_local_source(Path('/example/ordina-editor/a.mov'), []))
        with patch.object(archive, 'startup_devices', return_value={-1}):
            self.assertFalse(archive.is_local_source(self.source, [(Path('/'), 'apfs')]))
        external = self.base / 'external'
        external.mkdir()
        alias = external / 'alias.mov'
        alias.symlink_to(self.source)
        self.assertFalse(archive.is_local_source(alias, [(Path('/'), 'apfs'), (external, 'apfs')]))

    def test_verified_copy_preserves_original_and_is_idempotent(self):
        result = archive.verified_copy(self.source, self.nas, "视频")
        self.assertEqual(Path(result["destination"]).read_bytes(), self.source.read_bytes())
        self.assertTrue(self.source.exists())
        self.assertEqual(Path(result["destination"]).name, self.source.name)
        self.assertEqual(Path(result['destination']).parent, self.nas.resolve() / '3_素材/视频/归档')
        again = archive.verified_copy(self.source, self.nas, "视频")
        self.assertEqual(result, again)
        self.assertEqual(len(list(self.nas.rglob("*.mov"))), 1)

    def test_same_name_different_content_does_not_overwrite(self):
        first = archive.verified_copy(self.source, self.nas, "视频")
        self.source.write_bytes(b"different")
        second = archive.verified_copy(self.source, self.nas, "视频")
        self.assertNotEqual(first["destination"], second["destination"])
        self.assertTrue(Path(first["destination"]).exists())
        self.assertEqual(Path(second['destination']).parent.name, '同名素材 2')
        self.assertEqual(Path(second['destination']).name, self.source.name)

    def test_identical_content_different_names_preserve_both_names(self):
        first = archive.verified_copy(self.source, self.nas, '视频')
        another = self.source.with_name('另一个名字.mov')
        another.write_bytes(self.source.read_bytes())
        second = archive.verified_copy(another, self.nas, '视频')
        self.assertNotEqual(first['destination'], second['destination'])
        self.assertEqual(Path(second['destination']).name, another.name)
        self.assertTrue(Path(first['destination']).is_file())

    def test_existing_modified_file_is_not_overwritten(self):
        first = archive.verified_copy(self.source, self.nas, "视频")
        Path(first["destination"]).write_bytes(b"broken")
        second = archive.verified_copy(self.source, self.nas, '视频')
        self.assertNotEqual(first['destination'], second['destination'])
        self.assertEqual(Path(first['destination']).read_bytes(), b'broken')
        self.assertEqual(Path(second['destination']).read_bytes(), self.source.read_bytes())

    def test_cancel_leaves_original_and_no_published_file(self):
        with self.assertRaises(archive.ArchiveError):
            archive.verified_copy(self.source, self.nas, "视频", cancelled=lambda: True)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(self.nas.rglob("*.mov")), [])

    def test_source_change_during_copy_is_rejected(self):
        def progress(*arguments):
            self.source.write_bytes(b"changed while copying")
        with self.assertRaises(archive.ArchiveError):
            archive.verified_copy(self.source, self.nas, "视频", progress=progress)
        self.assertEqual(list(self.nas.rglob("*.mov")), [])

    def test_missing_nas_is_not_recreated(self):
        with self.assertRaises(archive.ArchiveError):
            archive.verified_copy(self.source, self.nas / "offline", "视频")
        self.assertFalse((self.nas / "offline").exists())

    def test_insufficient_space_is_rejected(self):
        with patch.object(archive.shutil, "disk_usage", return_value=type("Space", (), {"free": 0})()):
            with self.assertRaises(archive.ArchiveError):
                archive.verified_copy(self.source, self.nas, "视频")

    def test_existing_verified_copy_can_resume_with_no_free_space(self):
        first = archive.verified_copy(self.source, self.nas, '视频')
        with patch.object(archive.shutil, 'disk_usage', return_value=type('Space', (), {'free': 0})()):
            self.assertEqual(archive.verified_copy(self.source, self.nas, '视频'), first)

    def test_symlink_category_cannot_escape_project(self):
        (self.nas / "3_素材").symlink_to(self.source.parent, target_is_directory=True)
        with self.assertRaises(archive.ArchiveError):
            archive.verified_copy(self.source, self.nas, "视频")


if __name__ == "__main__":
    unittest.main()
