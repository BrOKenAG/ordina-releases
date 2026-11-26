#!/usr/bin/env python3
"""End-to-end acceptance for the versioned plugin protocol.

The client engine is compiled ONCE into a small snapshot binary that links the
same PluginProtocol / PluginCatalog / PluginInstaller sources as the app. Every
scenario below then talks to a local signed registry; a brand-new plugin id is
published by changing only that registry, never by rebuilding the client.

    python3 scripts/verify_plugin_protocol.py
    python3 scripts/verify_plugin_protocol.py --harness /tmp/ordina-plugin-harness

Nothing outside a temporary HOME and a loopback HTTP server is touched.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plugin_protocol as pp                     # noqa: E402
from feed_signing import envelope_bytes          # noqa: E402
from publish_plugin import build_bundle          # noqa: E402

SOURCES = [
    'Ordina/PluginProtocol.swift',
    'Ordina/PluginEnvironment.swift',
    'Ordina/PluginUpdates.swift',
    'Ordina/PluginInstaller.swift',
    'Ordina/PluginCatalog.swift',
    'Tests/PluginClientHarness.swift',
]
CHECKS = (
    'discovery', 'install_new_plugin', 'withdrawn_plugin', 'update_new_plugin', 'uninstall_new_plugin',
    'offline_cache', 'registry_unavailable', 'bad_signature', 'corrupt_asset',
    'incompatible_client', 'interrupted_install', 'regression_minutes',
    'regression_multicam', 'regression_yueliu',
)


class LabServer:
    """Loopback HTTP server whose content can be swapped without restarting."""

    def __init__(self):
        self.routes = {}
        self.overrides = {}
        lab = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path in lab.overrides:
                    status, body = lab.overrides[path]
                elif path in lab.routes:
                    status, body = 200, lab.routes[path]
                else:
                    status, body = 404, b'not found'
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LabFeed:
    """Build a signed registry and per-plugin manifests around a LabServer."""

    def __init__(self, server: LabServer):
        self.server = server
        self.entries = {}
        self.assets = {}
        self.files = {}
        self.paths = {}

    def publish(self, plugin_id, bundle, descriptor_meta, version=None, tag=None):
        if isinstance(bundle, Path):
            data, ver, files = build_bundle(bundle, expected_id=plugin_id)
        else:
            data, ver, files = bundle
        version = version or ver
        meta = dict(descriptor_meta, kind='bundle', version=version)
        descriptor = pp.descriptor(plugin_id, meta, version=version, files=files)
        asset_url = self.server.url(
            f'/BrOKenAG/ordina-plugin-{plugin_id}/releases/download/'
            f'{tag or "plugin-%s-v%s" % (plugin_id, version)}/{plugin_id}.json')
        entry = {
            'version': version, 'url': asset_url,
            'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'kind': 'bundle', 'descriptor': descriptor, 'files': files,
        }
        pp.validate_manifest_entry(plugin_id, entry)
        self.server.routes[f'/ordina-plugin-{plugin_id}/manifest-v2.json'] = envelope_bytes(
            {'format': 1, 'plugins': {plugin_id: entry}})
        self.server.routes[urllib.parse.urlparse(asset_url).path] = data
        self.entries[plugin_id] = {
            'repository': f'BrOKenAG/ordina-plugin-{plugin_id}',
            'feed': self.server.url(f'/ordina-plugin-{plugin_id}/manifest-v2.json'),
            'downloadPrefix': f'/BrOKenAG/ordina-plugin-{plugin_id}/releases/download/',
            'kind': 'bundle',
            'name': descriptor['name'], 'summary': descriptor['summary'],
            'symbol': descriptor['symbol'], 'version': version,
            'minClientVersion': descriptor['minClientVersion'],
            'protocol': 2, 'capabilities': descriptor['capabilities'],
        }
        self.assets[plugin_id] = data
        self.files[plugin_id] = files
        self.paths[plugin_id] = urllib.parse.urlparse(asset_url).path
        return entry

    def publish_script(self, plugin_id, data, version, meta):
        descriptor = pp.descriptor(
            plugin_id, dict(meta, kind='script', version=version), version=version,
            files=[{'path': f'{plugin_id}.py', 'sha256': hashlib.sha256(data).hexdigest(),
                    'size': len(data), 'role': 'utilityScript',
                    'installName': meta['installName']}])
        asset_url = self.server.url(
            f'/BrOKenAG/ordina-plugin-{plugin_id}/releases/download/'
            f'plugin-{plugin_id}-v{version}/{plugin_id}.py')
        entry = {
            'version': version, 'url': asset_url,
            'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'descriptor': descriptor,
            'files': [{'path': f'{plugin_id}.py',
                       'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                       'role': 'utilityScript', 'installName': meta['installName']}],
        }
        pp.validate_manifest_entry(plugin_id, entry)
        self.server.routes[f'/ordina-plugin-{plugin_id}/manifest-v2.json'] = envelope_bytes(
            {'format': 1, 'plugins': {plugin_id: entry}})
        self.server.routes[urllib.parse.urlparse(asset_url).path] = data
        self.paths[plugin_id] = urllib.parse.urlparse(asset_url).path
        self.assets[plugin_id] = data
        self.entries[plugin_id] = {
            'repository': f'BrOKenAG/ordina-plugin-{plugin_id}',
            'feed': self.server.url(f'/ordina-plugin-{plugin_id}/manifest-v2.json'),
            'downloadPrefix': f'/BrOKenAG/ordina-plugin-{plugin_id}/releases/download/',
            'kind': 'script', 'name': descriptor['name'],
            'summary': descriptor['summary'], 'symbol': descriptor['symbol'],
            'version': version, 'minClientVersion': descriptor['minClientVersion'],
            'protocol': 2, 'capabilities': descriptor['capabilities'],
        }
        return entry

    def sign(self, tamper=False, withdrawn=()):
        payload = {'format': 2, 'plugins': self.entries}
        if withdrawn:
            payload['withdrawn'] = list(withdrawn)
        data = envelope_bytes(payload)
        if tamper:
            envelope = json.loads(data)
            raw = bytearray(base64.b64decode(envelope['payload']))
            raw[10] = raw[10] ^ 0xFF
            envelope['payload'] = base64.b64encode(bytes(raw)).decode()
            data = json.dumps(envelope).encode()
        self.server.routes['/registry.json'] = data
        return data


def compile_harness(destination):
    # ORDINA_LAB compiles the isolated-registry hook that a release build
    # does not contain; the harness is the only consumer.
    command = ['/usr/bin/xcrun', 'swiftc', '-O', '-D', 'ORDINA_LAB']
    command += [str(ROOT / source) for source in SOURCES]
    command += ['-o', str(destination)]
    environment = dict(os.environ)
    environment.setdefault('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def run(harness, home, port, *arguments, timeout=90):
    environment = dict(os.environ)
    environment.update({
        'HOME': str(home),
        'ORDINA_PLUGIN_LAB': '1',
        'ORDINA_PLUGIN_REGISTRY': f'http://127.0.0.1:{port}/registry.json',
        'ORDINA_CLIENT_VERSION': '1.9.6',
        'NO_PROXY': 'localhost,127.0.0.1',
    })
    result = subprocess.run([str(harness), *arguments], env=environment,
                            capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
    except (ValueError, IndexError):
        payload = {'ok': False, 'error': (result.stdout + result.stderr).strip()}
    return result.returncode, payload


def item(payload, plugin_id):
    for entry in payload.get('items', []):
        if entry.get('id') == plugin_id:
            return entry
    return None


def write_bundle(directory, plugin_id, version, body='v1', extra=None, min_client='1.9.6'):
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        f'{plugin_id}.py': {'role': 'utilityScript', 'installName': f'{plugin_id}.py'},
        'host.py': {'role': 'support'},
        'host.command': {'role': 'nativeMessagingHost', 'executable': True},
        'extension/manifest.json': {'role': 'extension'},
        f'chrome/com.{plugin_id}.bridge.json': {
            'role': 'nativeMessagingManifest',
            'installName': f'com.{plugin_id}.bridge.json'},
    }
    meta = {
        'id': plugin_id, 'name': 'Demo Notes', 'version': version,
        'summary': f'A plugin compiled clients have never seen ({body})',
        'symbol': 'note.text', 'minClientVersion': min_client, 'protocol': 2,
        'capabilities': ['resolve.utilityScript', 'resolve.support',
                         'chrome.nativeMessagingHost', 'chrome.unpackedExtension'],
        'supportDirectoryName': 'Demo Notes',
        'nativeMessagingHost': 'host.command', 'files': files,
    }
    (directory / 'plugin.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    (directory / f'{plugin_id}.py').write_text(
        f'PLUGIN_VERSION = "v{version}"\n# {body}\n', encoding='utf-8')
    (directory / 'host.py').write_text(f'# host {body}\n', encoding='utf-8')
    (directory / 'host.command').write_text('#!/bin/sh\nexec python3 host.py\n', encoding='utf-8')
    (directory / 'extension').mkdir(exist_ok=True)
    (directory / 'extension/manifest.json').write_text(
        json.dumps({'manifest_version': 3, 'name': f'Demo {plugin_id}', 'version': version}),
        encoding='utf-8')
    (directory / 'extension/popup.html').write_text(f'<html>{body}</html>', encoding='utf-8')
    (directory / 'chrome').mkdir(exist_ok=True)
    (directory / f'chrome/com.{plugin_id}.bridge.json').write_text(json.dumps({
        'name': f'com.{plugin_id}.bridge', 'description': f'Demo {plugin_id}',
        'type': 'stdio', 'allowed_origins': ['chrome-extension://' + 'a' * 32 + '/'],
    }), encoding='utf-8')
    if extra:
        extra(directory)
    return meta


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def configure_lab_signing(workspace):
    import lab_signing
    seed = os.urandom(32)
    key = workspace / 'lab-signing.key'
    key.write_bytes(base64.b64encode(seed))
    key.chmod(0o600)
    os.environ['ORDINA_DEV_LAB'] = '1'
    os.environ['ORDINA_DEV_SIGN_KEY'] = str(key)
    os.environ['ORDINA_PLUGIN_PUBLIC_KEY'] = base64.b64encode(
        lab_signing.public_key(seed)).decode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--harness', type=Path,
                        help='复用已编译的客户端快照（默认现场编译一次）')
    parser.add_argument('--report', type=Path,
                        default=ROOT / 'docs' / 'validation' / 'plugin-protocol.json')
    parser.add_argument('--keep', action='store_true', help='保留临时目录用于排查')
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix='ordina-plugin-lab-'))
    configure_lab_signing(workspace)
    harness = args.harness or (workspace / 'ordina-plugin-harness')
    if not Path(harness).is_file():
        compile_harness(harness)
    snapshot_digest = hashlib.sha256(Path(harness).read_bytes()).hexdigest()

    server = LabServer()
    feed = LabFeed(server)
    port = server.port
    home = workspace / 'home'
    home.mkdir()

    utility = lambda name: home / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility' / name
    support = lambda name: home / 'Library/Application Support' / name
    chrome = lambda name: home / 'Library/Application Support/Google/Chrome/NativeMessagingHosts' / name
    state = lambda plugin_id: home / 'Library/Application Support/ordina/Plugins' / plugin_id / 'state.json'

    def publish_demo(version, body, minimum='1.9.6'):
        directory = workspace / f'plugin-demo-{version}-{uuid.uuid4().hex[:6]}'
        meta = write_bundle(directory, 'demo-notes', version, body, min_client=minimum)
        feed.publish('demo-notes', directory, meta, version=version)
        feed.sign()

    def publish_catalog():
        minutes_dir = ROOT / 'Integrations/OptionalPlugins/Minutes Roughcut'
        minutes_meta = json.loads((minutes_dir / 'plugin.json').read_text(encoding='utf-8'))
        feed.publish('minutes', minutes_dir, minutes_meta, version=minutes_meta['version'])
        multicam = (ROOT / 'Integrations/OptionalPlugins/Multicam Tools.py').read_bytes()
        feed.publish_script('multicam', multicam, '2.1', {
            'installName': 'Multicam Tools.py', 'name': 'Multicam Tools',
            'summary': '批量命名机位，创建多机位片段', 'symbol': 'video',
            'minClientVersion': '1.9.6', 'capabilities': ['resolve.utilityScript'],
        })
        yueliu = (ROOT / 'Integrations/OptionalPlugins/Import YueLiu.py').read_bytes()
        feed.publish_script('yueliu', yueliu, '1.0.0', {
            'installName': 'Import YueLiu.py', 'name': 'Import YueLiu',
            'summary': '导入阅流审片意见', 'symbol': 'text.bubble',
            'minClientVersion': '1.9.6', 'capabilities': ['resolve.utilityScript'],
            'legacyNames': ['导入阅流.py'],
        })

    checks = {}
    details = {}

    def check(name, operation):
        try:
            details[name] = operation() or {}
            checks[name] = 'pass'
            print(f'PASS {name}', flush=True)
        except Exception as error:                       # noqa: BLE001
            checks[name] = 'fail'
            details[name] = {'error': str(error)}
            print(f'FAIL {name}: {error}', flush=True)

    try:
        publish_demo('1.0.0', 'v1')
        publish_catalog()
        feed.sign()

        def discovery():
            code, payload = run(harness, home, port, 'list')
            require(code == 0 and payload.get('ok'), f'list failed: {payload}')
            ids = {entry['id'] for entry in payload['items']}
            require({'demo-notes', 'minutes', 'multicam', 'yueliu'} <= ids,
                    f'missing plugins: {ids}')
            demo = item(payload, 'demo-notes')
            require(demo['name'] == 'Demo Notes', 'descriptor name not shown')
            require(demo['symbol'] == 'note.text', 'descriptor symbol not shown')
            require(len(demo['capabilities']) == 4, 'capabilities not shown')
            require(demo['status'] == 'notInstalled', f'unexpected status {demo["status"]}')
            return {'items': sorted(ids)}
        check('discovery', discovery)

        def install_new_plugin():
            code, payload = run(harness, home, port, 'install', 'demo-notes')
            require(code == 0 and payload.get('ok'), f'install failed: {payload}')
            require(utility('demo-notes.py').is_file(), 'utility script missing')
            require('v1.0.0' in utility('demo-notes.py').read_text(), 'wrong script body')
            host = support('Demo Notes/host.command')
            require(host.is_file(), 'native messaging host missing')
            require(stat.S_IMODE(host.stat().st_mode) == 0o700, 'host not executable')
            require(support('Demo Notes/extension/manifest.json').is_file(),
                    'extension missing')
            manifest_path = chrome('com.demo-notes.bridge.json')
            require(manifest_path.is_file(), 'chrome manifest missing')
            manifest = json.loads(manifest_path.read_text())
            require(manifest['path'] == str(host), f'wrong chrome host path {manifest["path"]}')
            recorded = json.loads(state('demo-notes').read_text())
            require(recorded['version'] == '1.0.0', 'state version wrong')
            require(len(recorded['files']) == 6, f'state files {len(recorded["files"])}')
            code, payload = run(harness, home, port, 'list')
            entry = item(payload, 'demo-notes')
            require(entry['status'] == 'current:1.0.0', f'status {entry["status"]}')
            return {'installedFiles': len(recorded['files'])}
        check('install_new_plugin', install_new_plugin)

        def withdrawn_plugin():
            saved = feed.entries.pop('demo-notes')
            feed.sign(withdrawn=['demo-notes'])
            try:
                code, payload = run(harness, home, port, 'list')
                current = item(payload, 'demo-notes')
                require(code == 0 and current['installed'] and current['withdrawn'],
                        f'installed withdrawn status missing: {payload}')
                require(not current['canInstall'], 'withdrawn plugin can be installed')
                fresh = workspace / 'home-withdrawn'
                fresh.mkdir()
                _, payload = run(harness, fresh, port, 'list')
                require(item(payload, 'demo-notes') is None, 'withdrawn plugin was discoverable')
                code, _ = run(harness, fresh, port, 'install', 'demo-notes')
                require(code != 0, 'withdrawn plugin was installable')
                code, _ = run(harness, home, port, 'uninstall', 'demo-notes')
                require(code == 0, 'withdrawn plugin could not be uninstalled')
            finally:
                feed.entries['demo-notes'] = saved
                feed.sign()
            code, _ = run(harness, home, port, 'install', 'demo-notes')
            require(code == 0, 'republished plugin could not be installed')
        check('withdrawn_plugin', withdrawn_plugin)

        def update_new_plugin():
            publish_demo('1.1.0', 'v2')
            code, payload = run(harness, home, port, 'list')
            entry = item(payload, 'demo-notes')
            require(entry['status'] == 'update:1.0.0->1.1.0', f'status {entry["status"]}')
            user_file = support('Demo Notes/user.txt')
            user_file.write_text('keep me', encoding='utf-8')
            code, payload = run(harness, home, port, 'update', 'demo-notes')
            require(code == 0 and payload.get('ok'), f'update failed: {payload}')
            require('v1.1.0' in utility('demo-notes.py').read_text(), 'script not updated')
            require(user_file.read_text() == 'keep me', 'user data lost on update')
            recorded = json.loads(state('demo-notes').read_text())
            require(recorded['version'] == '1.1.0', 'state not updated')
            backups = list((home / 'Library/Application Support/ordina/Plugins/demo-notes/backups').glob('*/*'))
            require(any('host v1' in path.read_text() for path in backups if path.is_file()),
                    'previous file not backed up')
            return {'backups': len(backups)}
        check('update_new_plugin', update_new_plugin)

        def uninstall_new_plugin():
            code, payload = run(harness, home, port, 'uninstall', 'demo-notes')
            require(code == 0 and payload.get('ok'), f'uninstall failed: {payload}')
            require(not utility('demo-notes.py').exists(), 'utility script left behind')
            require(not support('Demo Notes/host.py').exists(), 'support file left behind')
            require(not support('Demo Notes/extension').exists(), 'extension left behind')
            require(not chrome('com.demo-notes.bridge.json').exists(), 'chrome manifest left behind')
            require(support('Demo Notes/user.txt').is_file(), 'user data removed on uninstall')
            require(not state('demo-notes').exists(), 'state left behind')
            code, payload = run(harness, home, port, 'list')
            require(item(payload, 'demo-notes')['status'] == 'notInstalled', 'status not reset')
            return {'userDataKept': True}
        check('uninstall_new_plugin', uninstall_new_plugin)

        def offline_cache():
            code, payload = run(harness, home, port, 'install', 'demo-notes')
            require(code == 0, f'reinstall failed: {payload}')
            server.overrides['/registry.json'] = (503, b'unavailable')
            try:
                code, payload = run(harness, home, port, 'list')
            finally:
                server.overrides.pop('/registry.json', None)
            require(code == 0 and payload.get('ok'), 'cached registry unusable')
            require(payload.get('registryError') in (None, ''), 'cache should not report an error')
            require(item(payload, 'demo-notes')['status'] == 'current:1.1.0',
                    'installed plugin missing while offline')
            return {'cached': True}
        check('offline_cache', offline_cache)

        def registry_unavailable():
            fresh = workspace / 'home-offline'
            fresh.mkdir()
            server.overrides['/registry.json'] = (503, b'unavailable')
            try:
                code, payload = run(harness, fresh, port, 'list')
            finally:
                server.overrides.pop('/registry.json', None)
            require(code == 0, 'list should still exit zero')
            require('无法获取' in str(payload.get('registryError')), f'error {payload}')
            return {'error': payload['registryError']}
        check('registry_unavailable', registry_unavailable)

        def bad_signature():
            fresh = workspace / 'home-badsig'
            fresh.mkdir()
            feed.sign(tamper=True)
            try:
                code, payload = run(harness, fresh, port, 'list')
            finally:
                feed.sign()
            require('校验失败' in str(payload.get('registryError')), f'error {payload}')
            return {'error': payload['registryError']}
        check('bad_signature', bad_signature)

        def corrupt_asset():
            fresh = workspace / 'home-corrupt'
            fresh.mkdir()
            original = feed.assets['demo-notes']
            tampered = bytearray(original)
            tampered[len(tampered) // 2] ^= 0xFF
            server.overrides[feed.paths['demo-notes']] = (200, bytes(tampered))
            try:
                code, payload = run(harness, fresh, port, 'install', 'demo-notes')
            finally:
                server.overrides.pop(feed.paths['demo-notes'], None)
            require(code != 0 and not payload.get('ok'), 'corrupt asset accepted')
            require('校验' in str(payload.get('error')), f'error {payload}')
            require(not (fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                         'Fusion/Scripts/Utility/demo-notes.py').exists(), 'corrupt install wrote files')
            return {'error': payload['error']}
        check('corrupt_asset', corrupt_asset)

        def incompatible_client():
            publish_demo('2.0.0', 'v3', minimum='2.0.0')
            try:
                fresh = workspace / 'home-incompatible'
                fresh.mkdir()
                code, payload = run(harness, fresh, port, 'list')
                entry = item(payload, 'demo-notes')
                require(entry['status'] == 'incompatible:2.0.0', f'status {entry["status"]}')
                code, payload = run(harness, fresh, port, 'install', 'demo-notes')
                require(code != 0, 'incompatible plugin installed')
                require('需要 ordina' in str(payload.get('error')), f'error {payload}')
                return {'required': '2.0.0'}
            finally:
                publish_demo('1.1.0', 'v2')
        check('incompatible_client', incompatible_client)

        def interrupted_install():
            fresh = workspace / 'home-interrupted'
            fresh.mkdir()
            blocker = fresh / 'Library/Application Support/Demo Notes/host.py'
            blocker.mkdir(parents=True)
            code, payload = run(harness, fresh, port, 'install', 'demo-notes')
            require(code != 0, 'blocked install reported success')
            require(not (fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                         'Fusion/Scripts/Utility/demo-notes.py').exists(),
                    'partial install left the utility script behind')
            require(not (fresh / 'Library/Application Support/ordina/Plugins/demo-notes/state.json').exists(),
                    'failed install recorded state')
            blocker.rmdir()
            code, payload = run(harness, fresh, port, 'install', 'demo-notes')
            require(code == 0 and payload.get('ok'), f'retry failed: {payload}')
            require((fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                     'Fusion/Scripts/Utility/demo-notes.py').is_file(), 'retry did not install')
            return {'rolledBack': True, 'retry': 'ok'}
        check('interrupted_install', interrupted_install)

        def regression_minutes():
            fresh = workspace / 'home-minutes'
            fresh.mkdir()
            legacy_support = fresh / 'Library/Application Support/Minutes Roughcut'
            legacy_support.mkdir(parents=True)
            (legacy_support / 'installed.json').write_text(json.dumps(
                {'id': 'minutes', 'version': '0.2.0'}), encoding='utf-8')
            (legacy_support / 'user-note.txt').write_text('keep', encoding='utf-8')
            code, payload = run(harness, fresh, port, 'list')
            entry = item(payload, 'minutes')
            require(entry['status'] == 'update:0.2.0->0.3.0', f'status {entry["status"]}')
            code, payload = run(harness, fresh, port, 'install', 'minutes')
            require(code == 0 and payload.get('ok'), f'install failed: {payload}')
            utility_dir = fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility'
            require((utility_dir / 'Minutes Roughcut.py').is_file(), 'resolve script missing')
            require((legacy_support / 'host.command').is_file(), 'host.command missing')
            require(stat.S_IMODE((legacy_support / 'host.command').stat().st_mode) == 0o700,
                    'host.command not executable')
            require((legacy_support / 'extension/manifest.json').is_file(), 'extension missing')
            manifest = json.loads((fresh / 'Library/Application Support/Google/Chrome/'
                                   'NativeMessagingHosts/com.minutesroughcut.bridge.json').read_text())
            require(manifest['path'] == str(legacy_support / 'host.command'),
                    f'wrong chrome path {manifest["path"]}')
            require((legacy_support / 'user-note.txt').read_text() == 'keep', 'user data lost')
            require(json.loads((legacy_support / 'installed.json').read_text())['version'] == '0.2.0',
                    'legacy marker should be preserved until the next publish')
            recorded = json.loads((fresh / 'Library/Application Support/ordina/Plugins/minutes/'
                                   'state.json').read_text())
            require(recorded['version'] == '0.3.0', 'state version wrong')
            code, payload = run(harness, fresh, port, 'list')
            require(item(payload, 'minutes')['status'] == 'current:0.3.0', 'minutes not current')
            return {'files': len(recorded['files']), 'migrated': True}
        check('regression_minutes', regression_minutes)

        def regression_multicam():
            fresh = workspace / 'home-multicam'
            fresh.mkdir()
            code, payload = run(harness, fresh, port, 'install', 'multicam')
            require(code == 0 and payload.get('ok'), f'install failed: {payload}')
            target = (fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                      'Fusion/Scripts/Utility/Multicam Tools.py')
            require(target.is_file(), 'multicam script missing')
            require(target.read_bytes() == feed.assets['multicam'], 'script bytes changed')
            recorded = json.loads((fresh / 'Library/Application Support/ordina/Plugins/multicam/'
                                   'state.json').read_text())
            require(recorded['version'] == '2.1', 'state version wrong')
            return {'files': len(recorded['files'])}
        check('regression_multicam', regression_multicam)

        def regression_yueliu():
            fresh = workspace / 'home-yueliu'
            fresh.mkdir()
            utility_dir = fresh / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility'
            utility_dir.mkdir(parents=True)
            legacy = utility_dir / '导入阅流.py'
            legacy.write_text('# legacy user script\n', encoding='utf-8')
            code, payload = run(harness, fresh, port, 'list')
            entry = item(payload, 'yueliu')
            require(entry['status'].startswith('unmanaged'), f'status {entry["status"]}')
            code, payload = run(harness, fresh, port, 'install', 'yueliu')
            require(code == 0 and payload.get('ok'), f'install failed: {payload}')
            require((utility_dir / 'Import YueLiu.py').is_file(), 'renamed script missing')
            require(not legacy.exists(), 'legacy entry left behind')
            backups = list((fresh / 'Library/Application Support/ordina/Plugins/yueliu/'
                            'backups').glob('*/*'))
            require(any('legacy user script' in path.read_text() for path in backups),
                    'legacy script not backed up')
            return {'migrated': True, 'backups': len(backups)}
        check('regression_yueliu', regression_yueliu)

    finally:
        server.stop()
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)

    passed = all(value == 'pass' for value in checks.values())
    report = {
        'format': 1,
        'passed': passed,
        'clientVersion': '1.9.6',
        'compiledOnce': True,
        'harnessSha256': snapshot_digest,
        'checks': checks,
        'details': details,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    width = max(len(name) for name in checks)
    for name in CHECKS:
        print(f'{name.ljust(width)}  {checks.get(name, "missing")}')
    print(f'\n报告：{args.report}')
    if not passed:
        sys.exit('插件协议验收未通过')


if __name__ == '__main__':
    main()
