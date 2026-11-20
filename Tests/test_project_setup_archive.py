import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

script_path = Path(__file__).resolve().parents[1] / 'Integrations/Project Setup.py'
if not script_path.exists():
    script_path = Path(__file__).resolve().parents[1] / 'Project Setup.py'
if not script_path.exists():
    script_path = Path.home() / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Project Setup.py'
spec = importlib.util.spec_from_file_location('project_setup', script_path)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupArchiveTests(unittest.TestCase):
    def test_archive_entry_opens_local_panel_without_launching_app(self):
        controller = object.__new__(setup.StartWorkApp)
        controller.show_archive_status = Mock()
        controller.set_status = Mock()
        with patch('subprocess.run') as launch, patch('subprocess.Popen') as spawn:
            controller.open_media_archive()
        controller.show_archive_status.assert_called_once_with()
        launch.assert_not_called()
        spawn.assert_not_called()
        controller.set_status.assert_not_called()

    def test_newer_installed_component_precedes_old_desktop_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            old = home / 'Desktop/Ordo.app'
            new = home / 'Applications/ordina.app'
            for app, version in ((old, '14'), (new, '18')):
                (app / 'Contents').mkdir(parents=True)
                (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                    'CFBundleIdentifier': 'com.example.MagicTools.Youth', 'CFBundleVersion': version}))
            with patch.object(setup.Path, 'home', return_value=home):
                candidates = setup.archive_app_candidates()
            self.assertLess(candidates.index(new), candidates.index(old))

    def test_automatic_entry_does_not_open_creator(self):
        controller = object.__new__(setup.StartWorkApp)
        controller.refresh_archive_setting = Mock()
        service = Mock()
        resources = Path('/runtime')
        with patch.object(setup, 'archive_runtime', return_value=(service, resources)), patch('subprocess.run') as launch:
            controller.open_media_archive(automatic=True)
            service.configure_service.assert_called_once_with(resources)
            launch.assert_not_called()

    def test_project_setup_checkbox_controls_background_service(self):
        controller = object.__new__(setup.StartWorkApp)
        controller.window = Mock()
        controller.window.GetItems.return_value = {'AutoArchive': type('Checkbox', (), {'Checked': False})()}
        controller.archive_status = Mock()
        service = Mock()
        resources = Path('/runtime')
        with patch.object(setup, 'archive_runtime', return_value=(service, resources)):
            controller.change_auto_archive()
            service.configure_service.assert_called_once_with(resources, automatic=False)
