from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Ordina'))
import archive_service as service


class ServiceTests(unittest.TestCase):
    def test_default_scan_interval_is_five_seconds(self):
        self.assertTrue(hasattr(service, 'scan_interval_seconds'))
        self.assertEqual(service.scan_interval_seconds({}), 5)
        self.assertEqual(service.scan_interval_seconds({'scanIntervalSeconds': 900}), 900)
        for value in [0, -1, True, '300', 1000000, 300.5]:
            self.assertEqual(service.scan_interval_seconds({'scanIntervalSeconds': value}), 5)

    def test_custom_interval_persists_without_starting_disabled_service(self):
        self.assertTrue(hasattr(service, 'set_scan_interval'))
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            before = {'automatic': False, 'enabled': False, 'projectRoots': ['/external/project'], 'instance': 'same'}
            service.write_json(config, before)
            with patch.object(service.subprocess, 'Popen') as launch:
                service.set_scan_interval('12', path=config)
            self.assertEqual(service.read_config(config), dict(before, scanIntervalSeconds=12))
            launch.assert_not_called()
            self.assertEqual(service.scan_interval_seconds(service.read_config(config)), 12)

    def test_invalid_interval_keeps_existing_settings(self):
        self.assertTrue(hasattr(service, 'set_scan_interval'))
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            service.write_json(config, {'scanIntervalSeconds': 600, 'automatic': True})
            before = config.read_bytes()
            for value in ['', '0', '-1', '1.5', '86401', 'NaN', True]:
                with self.assertRaisesRegex(ValueError, '1.*86400'):
                    service.set_scan_interval(value, path=config)
                self.assertEqual(config.read_bytes(), before)

    def test_deployment_includes_plan_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            resources = base / 'resources'
            resources.mkdir()
            names = ['archive_service.py', 'archive_worker.py', 'archive_plan.py', 'media_archive.py', 'resolve_bridge.py']
            for name in names:
                (resources / name).write_text('# ' + name)
            config = base / 'deployed' / 'config.json'
            with patch.object(service.subprocess, 'Popen') as launch:
                launch.return_value.pid = 12345
                service.configure_service(resources, path=config)
            self.assertEqual((config.parent / 'archive_plan.py').read_bytes(),
                             (resources / 'archive_plan.py').read_bytes())

    def test_off_setting_preserves_associations(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            service.write_json(config, {'automatic': True, 'enabled': True, 'projectRoots': ['/nas/project']})
            result = service.configure_service(Path(temporary), automatic=False, path=config)
            self.assertFalse(result['enabled'])
            self.assertFalse(result['automatic'])
            self.assertEqual(result['projectRoots'], ['/nas/project'])

    def test_status_from_old_instance_cannot_overwrite_current_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            service.write_json(config, {'instance': 'new'})
            service.write_service_status(config, 'old', {'status': 'old status'})
            self.assertFalse(config.with_name('status.json').exists())

    def test_service_continues_after_launcher_exits_and_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'archive_service.py'
            script.write_bytes(Path(service.__file__).read_bytes())
            worker = root / 'archive_worker.py'
            worker.write_text("import json,sys,time\nfrom pathlib import Path\nconfig=Path(sys.argv[2])\ninstance=sys.argv[3]\nconfig.with_name('worker-started').write_text('started')\nwhile True:\n value=json.loads(config.read_text())\n if not value.get('enabled') or value.get('instance')!=instance: break\n time.sleep(0.05)\n")
            config = root / 'config.json'
            service.write_json(config, {'enabled': True, 'instance': 'test-instance'})
            pid_path = root / 'supervisor.pid'
            launcher_code = "import subprocess,sys\nfrom pathlib import Path\nprocess=subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2],'test-instance'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nPath(sys.argv[3]).write_text(str(process.pid))\n"
            launcher = subprocess.run([sys.executable, '-c', launcher_code, str(script), str(config), str(pid_path)], timeout=5)
            self.assertEqual(launcher.returncode, 0)
            pid = int(pid_path.read_text())
            try:
                deadline = time.monotonic() + 5
                while not (root / 'worker-started').exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((root / 'worker-started').exists())
                self.assertTrue(service.process_alive(pid))
                service.pause_service(config)
                deadline = time.monotonic() + 5
                while service.read_config(root / 'status.json').get('running') is not False and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(service.read_config(root / 'status.json')['running'])
            finally:
                service.pause_service(config)
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
