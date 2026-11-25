"""Regression tests for the standalone publishing tool build.

The tool is an independent, signed artefact: its digest must be reproducible,
its version must be its own (not the version in the source tree), and a
production build must not contain the isolated lab backend or the lab signer.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dev_tool as bdt


def names(data):
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


class BuildTests(unittest.TestCase):
    def test_production_archive_rejects_lab_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tool.pyz'
            path.write_bytes(bdt.build('1.0.0'))
            env = dict(os.environ, ORDINA_DEV_LAB='1')
            result = subprocess.run([sys.executable, str(path), 'hello'], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('lab', result.stderr.lower())

    def test_build_is_deterministic(self):
        first = bdt.build('1.0.0')
        second = bdt.build('1.0.0')
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first).hexdigest(),
                         hashlib.sha256(second).hexdigest())

    def test_version_changes_the_digest(self):
        self.assertNotEqual(bdt.build('1.0.0'), bdt.build('1.1.0'))

    def test_production_build_excludes_lab_modules(self):
        archive = names(bdt.build('1.0.0'))
        self.assertNotIn('dev_lab_backend.py', archive)
        self.assertNotIn('lab_signing.py', archive)

    def test_lab_build_includes_lab_modules(self):
        archive = names(bdt.build('1.0.0', lab=True))
        self.assertIn('dev_lab_backend.py', archive)
        self.assertIn('lab_signing.py', archive)

    def test_every_engine_module_is_bundled(self):
        archive = names(bdt.build('1.0.0'))
        for module in bdt.MODULES:
            self.assertIn(module, archive)

    def test_entry_point_pins_its_own_version(self):
        data = bdt.build('9.9.9')
        entry = zipfile.ZipFile(io.BytesIO(data)).read('__main__.py').decode()
        self.assertIn("dev_publish.TOOL_VERSION = '9.9.9'", entry)
        self.assertIn('sys.exit(dev_publish.main())', entry)

    def test_version_file_matches_the_requested_version(self):
        data = bdt.build('9.9.9')
        text = zipfile.ZipFile(io.BytesIO(data)).read('VERSION').decode()
        self.assertEqual(text.strip(), 'ordina-publisher 9.9.9')

    def test_entry_metadata_declares_digest_and_size(self):
        data = bdt.build('1.0.0')
        item = bdt.entry('1.0.0', data)
        self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(item['size'], len(data))
        self.assertEqual(item['protocol'], bdt.PROTOCOL if hasattr(bdt, 'PROTOCOL') else 1)
        self.assertEqual(item['asset'], 'ordina-publisher-1.0.0.pyz')
        self.assertEqual(item['tag'], 'dev-tool-v1.0.0')

    def test_document_sorts_versions_numerically(self):
        entries = [bdt.entry(version, bdt.build(version))
                   for version in ('1.10.0', '1.2.0', '1.0.0')]
        ordered = [item['version'] for item in bdt.document(entries)['versions']]
        self.assertEqual(ordered, ['1.0.0', '1.2.0', '1.10.0'])

    def test_committed_source_manifest_matches_the_source_tree(self):
        source = json.loads(bdt.MANIFEST_SOURCE.read_text(encoding='utf-8'))
        self.assertEqual(source['tool'], bdt.TOOL)
        archived = {item['version']: item for item in source['versions']}
        self.assertIn(bdt.read_version(), archived)
        current = archived[bdt.read_version()]
        data = bdt.build(bdt.read_version())
        self.assertEqual(current['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(current['size'], len(data))

    def test_signed_manifest_envelope_is_present(self):
        manifest = ROOT / 'dev-tool' / 'manifest.json'
        self.assertTrue(manifest.is_file(), '缺少签名清单')
        envelope = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertIn('payload', envelope)
        self.assertIn('signature', envelope)


if __name__ == '__main__':
    unittest.main()
