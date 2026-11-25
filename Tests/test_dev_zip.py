"""Regression tests for the untrusted-ZIP adapter.

Every case here is an input the adapter must refuse *before* a single byte is
written, or a layout it must normalise deterministically.
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dev_zip


META = {'id': 'demo-plugin', 'name': 'Demo', 'version': '1.0.0',
        'files': {'demo-plugin.py': {'role': 'utilityScript',
                                     'installName': 'demo-plugin.py'},
                  'support/notes.txt': {'role': 'support'}}}
FILES = [('plugin.json', b'{}'), ('demo-plugin.py', b'print(1)\n'),
         ('support/notes.txt', b'notes\n')]


def archive(entries, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression) as handle:
        for name, payload in entries:
            handle.writestr(name, payload)
    return buffer.getvalue()


def package(root='demo-plugin', files=None):
    files = files or FILES
    return archive([(f'{root}/{name}' if root else name, data)
                    for name, data in files])


class FakeInfo:
    """A ZipInfo stand-in so mode and encryption bits can be exercised."""

    def __init__(self, filename='plugin.json', mode=0o100644, flags=0,
                 size=2, compressed=2, directory=False):
        self.filename = filename
        self.external_attr = mode << 16
        self.flag_bits = flags
        self.file_size = size
        self.compress_size = compressed
        self._directory = directory

    def is_dir(self):
        return self._directory


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dev-zip-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, data, name='package.zip'):
        path = self.root / name
        path.write_bytes(data)
        return path

    def assertRejected(self, data, code):
        path = self.write(data)
        with self.assertRaises(dev_zip.PackageError) as caught:
            dev_zip.read_archive(path)
        self.assertEqual(caught.exception.code, code,
                         f'{code} 期望被拒绝，实际为 {caught.exception.code}')
        return caught.exception

    def test_strips_single_top_level_folder(self):
        path = self.write(package())
        entries = dev_zip.read_archive(path)
        self.assertEqual(sorted(entries), ['demo-plugin.py', 'plugin.json',
                                           'support/notes.txt'])
        flat = self.write(package(root=None), 'flat.zip')
        self.assertEqual(sorted(dev_zip.read_archive(flat)), sorted(entries))

    def test_keeps_ambiguous_root_untouched(self):
        both = archive([('plugin.json', b'{}'), ('other/plugin.json', b'{}')])
        entries = dev_zip.read_archive(self.write(both))
        self.assertEqual(sorted(entries), ['other/plugin.json', 'plugin.json'])

    def test_rejects_parent_traversal(self):
        self.assertRejected(archive([('../evil.py', b'x')]), 'package.entry')
        self.assertRejected(archive([('plugin.json', b'{}'),
                                     ('a/../../evil.py', b'x')]), 'package.entry')

    def test_rejects_absolute_and_windows_paths(self):
        self.assertRejected(archive([('/etc/passwd', b'x')]), 'package.entry')
        self.assertRejected(archive([('plugin.json', b'{}'),
                                     ('support\\notes.txt', b'x')]),
                            'package.entry')
        self.assertRejected(archive([('C:/windows/system32/x.dll', b'x')]),
                            'package.entry')

    def test_rejects_control_characters_and_dotfiles(self):
        # zipfile itself truncates a NUL when writing, so the NUL case is
        # exercised on the entry normaliser where the attacker-supplied name
        # actually arrives.
        for raw in ('sup\x00port/x.txt', 'line\nbreak.py', 'tab\tname.py'):
            with self.assertRaises(dev_zip.PackageError) as caught:
                dev_zip._entry_name(FakeInfo(filename=raw))
            self.assertEqual(caught.exception.code, 'package.entry')
        self.assertRejected(archive([('plugin.json', b'{}'),
                                     ('.hidden', b'x')]), 'package.entry')

    def test_rejects_overlong_path_and_deep_nesting(self):
        self.assertRejected(archive([('plugin.json', b'{}'),
                                     ('a' * 201, b'x')]), 'package.entry')
        self.assertRejected(archive([('plugin.json', b'{}'),
                                     ('a/b/c/d/e.py', b'x')]), 'package.entry')

    def test_rejects_duplicate_paths(self):
        duplicate = archive([('plugin.json', b'{}'), ('demo-plugin.py', b'a'),
                             ('demo-plugin.py', b'b')])
        self.assertRejected(duplicate, 'package.duplicate')

    def test_rejects_case_and_unicode_collisions(self):
        # Both shapes are refused; which check fires first depends on the path
        # rules, but neither may ever reach the engine.
        for entries in ([('plugin.json', b'{}'), ('Demo.py', b'a'),
                         ('demo.py', b'b')],
                        [('plugin.json', b'{}'), ('caf\u00e9.py', b'a'),
                         ('cafe\u0301.py', b'b')]):
            with self.assertRaises(dev_zip.PackageError) as caught:
                dev_zip.read_archive(self.write(archive(entries)))
            self.assertTrue(caught.exception.code.startswith('package.'))

    def test_rejects_oversized_archive(self):
        self.assertRejected(b'0' * (dev_zip.MAX_ARCHIVE + 1), 'package.size')

    def test_rejects_too_many_entries(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as handle:
            for index in range(dev_zip.MAX_ENTRIES + 1):
                handle.writestr(f'file{index}.txt', b'x')
        self.assertRejected(buffer.getvalue(), 'package.count')

    def test_rejects_oversized_file_and_total(self):
        single = archive([('plugin.json', b'{}'),
                          ('big.py', b'A' * (dev_zip.MAX_FILE + 1))],
                         zipfile.ZIP_STORED)
        self.assertRejected(single, 'package.file')
        spread = archive([('plugin.json', b'{}')] +
                         [(f'part{index}.py', b'A' * 900_000) for index in range(10)],
                         zipfile.ZIP_STORED)
        self.assertRejected(spread, 'package.size')

    def test_rejects_compression_bomb(self):
        bomb = archive([('plugin.json', b'{}'),
                        ('zeros.py', b'\0' * 900_000)], zipfile.ZIP_DEFLATED)
        self.assertRejected(bomb, 'package.bomb')

    def test_requires_plugin_json_at_the_root(self):
        self.assertRejected(archive([('nested/a.py', b'x')]), 'package.manifest')

    def test_rejects_empty_and_broken_archives(self):
        self.assertRejected(b'not a zip at all', 'package.format')
        self.assertRejected(archive([]), 'package.empty')

    def test_rejects_symlinks_specials_and_encryption(self):
        cases = ((0o120777, 0, 'package.symlink'),
                 (0o020644, 0, 'package.special'),
                 (0o100644, 0x1, 'package.encrypted'))
        for mode, flags, code in cases:
            with self.assertRaises(dev_zip.PackageError) as caught:
                dev_zip._check_mode(FakeInfo(mode=mode, flags=flags), 'plugin.json')
            self.assertEqual(caught.exception.code, code)

    def test_accepts_regular_files_and_directories(self):
        dev_zip._check_mode(FakeInfo(mode=0o100644), 'plugin.json')
        dev_zip._check_mode(FakeInfo(mode=0, directory=True), 'support')

    def test_extraction_never_escapes_the_quarantine_directory(self):
        path = self.write(package())
        target = self.root / 'out'
        dev_zip.extract(path, target)
        for item in target.rglob('*'):
            self.assertTrue(str(item.resolve()).startswith(str(target.resolve())))
        self.assertEqual((target / 'support' / 'notes.txt').read_bytes(), b'notes\n')

    def test_extraction_refuses_to_overwrite(self):
        path = self.write(package())
        target = self.root / 'out'
        target.mkdir()
        (target / 'plugin.json').write_text('existing')
        with self.assertRaises(dev_zip.PackageError) as caught:
            dev_zip.extract(path, target)
        self.assertEqual(caught.exception.code, 'package.duplicate')
        self.assertEqual((target / 'plugin.json').read_text(), 'existing')

    def test_inventory_reports_roles_from_meta(self):
        path = self.write(package())
        extracted = dev_zip.extract(path, self.root / 'out')
        inventory = dev_zip.inventory(extracted, META)
        roles = {item['path']: item['role'] for item in inventory}
        self.assertEqual(roles['demo-plugin.py'], 'utilityScript')
        self.assertEqual(roles['support/notes.txt'], 'support')
        self.assertEqual(roles['plugin.json'], 'metadata')


if __name__ == '__main__':
    unittest.main()
