import base64
import importlib.util
from pathlib import Path
import tempfile
import plistlib
import re
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('prepare_update', Path(__file__).resolve().parents[1] / 'scripts/prepare_update.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class AppcastValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.feed = Path(self.temporary.name) / 'appcast.xml'
        self.prefix = 'https://github.com/BrOKenAG/ordina-releases/releases/download/v1.9.1-build.29/'
        self.name = 'ordina-1.9.1-build.29.dmg'
        signature = base64.b64encode(bytes(64)).decode()
        self.xml = f'''<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item>
        <sparkle:version>29</sparkle:version><sparkle:shortVersionString>1.9.1</sparkle:shortVersionString>
        <sparkle:minimumSystemVersion>26.0</sparkle:minimumSystemVersion>
        <enclosure url="{self.prefix}{self.name}" sparkle:edSignature="{signature}" length="123" />
        </item></channel></rss>'''

    def validate(self, text=None):
        self.feed.write_text(text or self.xml)
        return release.validate_appcast(self.feed, '29', '1.9.1', self.name, self.prefix, 123)

    def test_signed_archive_metadata_matches_the_built_app(self):
        self.validate()

    def test_incorrect_release_version_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('>1.9.1<', '>1.9.0<'))

    def test_unsigned_archive_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('sparkle:edSignature=', 'unused='))

    def test_insecure_or_unexpected_download_is_rejected(self):
        for prefix in ('http://github.com/', 'https://other.example/'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.validate(self.xml.replace('https://github.com/', prefix))

    def test_wrong_download_size_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('length="123"', 'length="124"'))

    def test_minimum_os_cannot_be_omitted(self):
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('<sparkle:minimumSystemVersion>26.0</sparkle:minimumSystemVersion>', ''))

    def test_duplicate_build_is_rejected(self):
        item = self.xml.split('<item>')[1].split('</item>')[0]
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('</channel>', '<item>' + item + '</item></channel>'))

    def test_reusing_output_keeps_old_download_urls(self):
        root = Path(self.temporary.name)
        output = root / 'updates'
        output.mkdir()
        old_name = 'ordina-1.9.1-build.28.dmg'
        (output / old_name).write_bytes(b'old archive')
        old_url = self.prefix.replace('build.29/', 'build.28/') + old_name
        old_xml = self.xml.replace('>29<', '>28<').replace(self.prefix + self.name, old_url)
        (output / 'appcast.xml').write_text(old_xml)
        app = root / 'ordina.app/Contents'
        app.mkdir(parents=True)
        (app / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleVersion': '29', 'CFBundleShortVersionString': '1.9.1'
        }))
        archive = root / self.name
        archive.write_bytes(bytes(123))
        tools = types.ModuleType('sparkle_tools')
        tools.ensure_tools = lambda: root / 'tools'
        tools.KEYCHAIN_ACCOUNT = 'test'

        def generate(command, **kwargs):
            if Path(command[0]).name == 'generate_appcast':
                directory = Path(command[-1])
                feed = directory / 'appcast.xml'
                tree = ET.parse(feed)
                channel = tree.find('channel')
                prefix = command[command.index('--download-url-prefix') + 1]
                # Sparkle rewrites links for every archive present in its input.
                for candidate in directory.glob('*.dmg'):
                    build = re.search(r'build\.([0-9]+)', candidate.name).group(1)
                    item = next((item for item in channel.findall('item')
                                 if item.findtext(release.SPARKLE + 'version') == build), None)
                    if item is None:
                        item = ET.fromstring(self.xml).find('./channel/item')
                        channel.append(item)
                    item.find('enclosure').set('url', prefix + candidate.name)
                tree.write(feed)
            return subprocess.CompletedProcess(command, 0)

        arguments = ['prepare_update', '--app', str(app.parent), '--archive', str(archive), '--output', str(output)]
        with patch.dict(sys.modules, sparkle_tools=tools), patch.object(sys, 'argv', arguments), \
             patch.object(release.subprocess, 'run', side_effect=generate):
            release.main()
        items = ET.parse(output / 'appcast.xml').findall('./channel/item')
        old_item = next(item for item in items if item.findtext(release.SPARKLE + 'version') == '28')
        self.assertEqual(old_item.find('enclosure').get('url'), old_url)


if __name__ == '__main__':
    unittest.main()
