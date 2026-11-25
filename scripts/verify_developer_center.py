#!/usr/bin/env python3
"""End-to-end acceptance for the developer centre and the publishing tool.

Compiles the client's real developer-centre sources once, then drives a real
publishing tool inside a loopback lab:

    tool manifest -> install -> inspect -> local test -> preview -> publish
    -> client discovers the new plugin -> update -> interrupted publish
    -> concurrent publish blocked -> resume -> tool update (blocked while a
    transaction is open) -> publish with the new tool -> rollback -> tool rollback

Nothing outside a temporary HOME, a temporary lab repository copy and a
loopback HTTP server is touched.

    python3 scripts/verify_developer_center.py
    python3 scripts/verify_developer_center.py --keep
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dev_tool                                    # noqa: E402
import lab_signing                                       # noqa: E402
import plugin_protocol as pp                             # noqa: E402
from feed_signing import canonical, decode_envelope, envelope_bytes  # noqa: E402
from verify_plugin_protocol import LabServer             # noqa: E402

CLIENT_SOURCES = [
    'Ordina/PluginProtocol.swift',
    'Ordina/PluginEnvironment.swift',
    'Ordina/PluginUpdates.swift',
    'Ordina/PluginInstaller.swift',
    'Ordina/DeveloperBridge.swift',
    'Ordina/DevPublisherTool.swift',
    'Ordina/DevToolManager.swift',
    'Ordina/DeveloperCenter.swift',
    'Tests/DeveloperCenterHarness.swift',
]
PLUGIN_SOURCES = [
    'Ordina/PluginProtocol.swift',
    'Ordina/PluginEnvironment.swift',
    'Ordina/PluginUpdates.swift',
    'Ordina/PluginInstaller.swift',
    'Ordina/PluginCatalog.swift',
    'Tests/PluginClientHarness.swift',
]
CHECKS = (
    'tool_absent', 'tool_manifest', 'tool_install', 'tool_integrity',
    'inspect_new', 'local_test', 'local_uninstall', 'preview',
    'preview_stale', 'malicious_zip', 'zip_bomb', 'version_downgrade',
    'publish_new', 'client_discovers_new_plugin', 'publish_update',
    'interrupted_publish', 'concurrent_blocked', 'pending_blocks_tool_update',
    'resume_publish', 'tool_update', 'publish_after_tool_update',
    'rollback_release', 'tool_rollback', 'unsigned_credential',
    'wrong_scope', 'records_preserved',
)
CLIENT_VERSION = '1.10.0'
PLUGIN_ID = 'demo-lab-notes'


class Failure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def compile_harness(sources, destination, extra=()):
    command = ['/usr/bin/xcrun', 'swiftc', '-O', '-D', 'ORDINA_LAB']
    command += [str(ROOT / source) for source in sources]
    command += list(extra) + ['-o', str(destination)]
    environment = dict(os.environ)
    environment.setdefault('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def write_plugin(directory, plugin_id, version, body):
    directory.mkdir(parents=True, exist_ok=True)
    meta = {
        'id': plugin_id, 'name': 'Demo Lab Notes', 'version': version,
        'summary': 'A plugin compiled clients have never seen', 'symbol': 'note.text',
        'minClientVersion': CLIENT_VERSION, 'protocol': 2,
        'capabilities': ['resolve.utilityScript', 'resolve.support',
                         'chrome.unpackedExtension'],
        'supportDirectoryName': 'Demo Lab Notes',
        'files': {
            f'{plugin_id}.py': {'role': 'utilityScript', 'installName': f'{plugin_id}.py'},
            'support/notes.txt': {'role': 'support'},
            'extension/manifest.json': {'role': 'extension'},
        },
    }
    (directory / 'plugin.json').write_text(json.dumps(meta), encoding='utf-8')
    (directory / f'{plugin_id}.py').write_text(
        f'PLUGIN_VERSION = "v{version}"\n# {body}\n', encoding='utf-8')
    (directory / 'support').mkdir(exist_ok=True)
    (directory / 'support' / 'notes.txt').write_text(f'{body} {version}\n', encoding='utf-8')
    (directory / 'extension').mkdir(exist_ok=True)
    (directory / 'extension' / 'manifest.json').write_text(
        json.dumps({'manifest_version': 3, 'name': 'Demo', 'version': version}),
        encoding='utf-8')
    return meta


def zip_plugin(source, target, mutate=None):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(source))
    if mutate is not None:
        mutate(target)
    return target


def credential_document(scopes, repositories=('BrOKenAG/ordina-plugin-*',),
                        expires_in=14 * 24 * 3600, developer='BrOKenAG'):
    now = int(time.time())
    return {'format': 1, 'developer': developer, 'keyId': 'ordina-feed-1',
            'issuedAt': now - 60, 'notBefore': now - 60,
            'expires': now + expires_in, 'scopes': list(scopes),
            'repositories': list(repositories)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--report', type=Path,
                        default=ROOT / 'docs' / 'validation' / 'developer-center.json')
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix='ordina-dev-center-'))
    home = workspace / 'home'
    lab_repo = workspace / 'repo'
    lab_root = workspace / 'labroot'
    home.mkdir(parents=True)
    (lab_repo / 'plugins').mkdir(parents=True)
    (lab_repo / 'plugins' / 'registry.source.json').write_text(
        json.dumps({'format': 2, 'plugins': {}}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    lab_root.mkdir(parents=True)

    # A throwaway signing key keeps the lab hermetic: no developer Keychain,
    # no network, and the client verifies with the matching public key only.
    lab_seed = hashlib.sha256(b'ordina developer centre acceptance').digest()
    sign_key = workspace / 'lab-signing.key'
    sign_key.write_bytes(base64.b64encode(lab_seed) + b'\n')
    sign_key.chmod(0o600)
    lab_public_key = base64.b64encode(lab_signing.public_key(lab_seed)).decode('ascii')

    harness = workspace / 'developer-center-harness'
    plugin_harness = workspace / 'plugin-client-harness'
    compile_harness(CLIENT_SOURCES, harness)
    compile_harness(PLUGIN_SOURCES, plugin_harness)
    harness_digest = hashlib.sha256(harness.read_bytes()).hexdigest()

    server = LabServer()
    port = server.port

    # --- developer tool releases (independent channel) -------------------
    tools = {}
    for version, marker in (('1.0.0', 'first'), ('1.1.0', 'second')):
        data = build_dev_tool.build(version, lab=True)
        if marker == 'second':
            # A different build must have a different digest: the marker is
            # written into the packaged VERSION file's neighbour module.
            require(data != tools['1.0.0']['data'], 'tool builds are identical')
        asset = f'ordina-publisher-{version}.pyz'
        path = f'/BrOKenAG/ordina-releases/releases/download/dev-tool-v{version}/{asset}'
        server.routes[path] = data
        tools[version] = {'data': data, 'asset': asset, 'path': path,
                          'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}

    def publish_tool_manifest(versions):
        entries = []
        for version in versions:
            item = tools[version]
            entries.append({
                'version': version, 'protocol': 1, 'minClientVersion': CLIENT_VERSION,
                'asset': item['asset'], 'sha256': item['sha256'], 'size': item['size'],
                'tag': f'dev-tool-v{version}',
                'url': f'http://127.0.0.1:{port}{item["path"]}',
                'notes': f'{version} 测试版本',
            })
        document = {'format': 1, 'tool': 'ordina-publisher', 'channel': 'stable',
                    'versions': entries}
        server.routes['/dev-tool/manifest.json'] = envelope_bytes(document)

    # Both releases exist in the tool channel from the start; the client simply
    # has not installed 1.1.0 yet, which is what the update flow exercises.
    publish_tool_manifest(['1.0.0', '1.1.0'])

    # --- credentials -----------------------------------------------------
    def lab_envelope(document):
        """Credentials are signed by the same key the tool verifies with.

        In a lab that key is the throwaway seed handed to the tool through
        ORDINA_DEV_SIGN_KEY, so the acceptance run never needs the developer's
        Keychain. The tool manifest keeps the pinned production key because the
        client verifies it against the key compiled into the app.
        """
        data = canonical(document)
        signature = base64.b64encode(lab_signing.signature(lab_seed, data)).decode('ascii')
        return {'payload': base64.b64encode(data).decode('ascii'), 'signature': signature}

    def write_credential(name, scopes):
        path = workspace / name
        payload = lab_envelope(credential_document(scopes))
        path.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
        path.chmod(0o600)
        return path

    full = write_credential('credential.json',
                            ['plugin.publish', 'plugin.localTest', 'plugin.rollback',
                             'repository.create', 'tool.install'])
    limited = write_credential('credential-limited.json', ['tool.install'])
    tampered = write_credential('credential-tampered.json', ['plugin.publish'])
    raw = json.loads(tampered.read_text(encoding='utf-8'))
    raw['signature'] = ('A' * 8) + raw['signature'][8:]
    tampered.write_text(json.dumps(raw), encoding='utf-8')

    base_env = {
        'HOME': str(home),
        'ORDINA_DEV_SIGN_KEY': str(sign_key),
        'ORDINA_CLIENT_VERSION': CLIENT_VERSION,
        'ORDINA_DEV_LAB': '1',
        'ORDINA_DEV_REPO': str(lab_repo),
        'ORDINA_DEV_MANIFEST': f'http://127.0.0.1:{port}/dev-tool/manifest.json',
        'ORDINA_DEV_BACKEND': 'lab',
        'ORDINA_DEV_LAB_ROOT': str(lab_root),
        'ORDINA_DEV_CREDENTIALS': str(full),
        'ORDINA_DEV_LAB_WRITE_SOURCE': '1',
        'ORDINA_DEV_JOURNAL_DIR': str(home / 'Library/Application Support/ordina/Developer/journals'),
        'ORDINA_CLIENT_VERSION': CLIENT_VERSION,
        'NO_PROXY': 'localhost,127.0.0.1',
    }

    def run(*arguments, env=None, timeout=300, binary=None):
        environment = dict(os.environ)
        environment.update(base_env)
        environment.update(env or {})
        result = subprocess.run([str(binary or harness), *arguments], env=environment,
                                capture_output=True, text=True, timeout=timeout)
        payload = {}
        if result.stdout.strip():
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except ValueError:
                payload = {'ok': False, 'error': result.stdout[-500:]}
        if not payload:
            payload = {'ok': False, 'error': (result.stderr or result.stdout)[-500:]}
        return result.returncode, payload

    def expect_failure(code_prefix, *arguments, env=None):
        status, payload = run(*arguments, env=env)
        code = str(payload.get('code') or '')
        require(status != 0 and code.startswith(code_prefix),
                f'expected {code_prefix}, got {code or payload}')
        return payload

    checks, details = {}, {}

    def check(name, operation):
        try:
            details[name] = operation() or {}
            checks[name] = 'pass'
            print(f'{name:32s} pass')
        except Exception as error:               # noqa: BLE001
            checks[name] = f'fail: {error}'
            print(f'{name:32s} FAIL  {error}')

    try:
        # 1. a normal user has no tool and no pending transaction
        def tool_absent():
            status, payload = run('status')
            require(payload.get('ok'), payload)
            require(payload.get('installed') in (None,), 'tool unexpectedly installed')
            require(payload.get('pending') == 0, 'unexpected pending transaction')
            require(payload.get('journals') == [], 'unexpected journals')
            return {'installed': None}
        check('tool_absent', tool_absent)

        # 2. the signed manifest lists both tool releases
        def tool_manifest():
            status, payload = run('tool-check')
            require(payload.get('ok'), payload)
            versions = {item['version']: item for item in payload['versions']}
            require(set(versions) == {'1.0.0', '1.1.0'}, str(versions))
            require(all(item['compatible'] for item in versions.values()), 'incompatible entry')
            return {'versions': sorted(versions)}
        check('tool_manifest', tool_manifest)

        # 3. installing the tool verifies signature, digest and health
        def tool_install():
            status, payload = run('tool-install', '1.0.0')
            require(payload.get('ok'), payload)
            require(payload.get('installed') == '1.0.0', payload)
            return {'installed': payload['installed']}
        check('tool_install', tool_install)

        def tool_integrity():
            status, payload = run('status')
            require(payload.get('ok') and payload.get('integrity') is True, payload)
            require(payload.get('hello') == '1.0.0', payload)
            require(payload.get('protocol') == 1, payload)
            return {'integrity': True, 'protocol': payload.get('protocol')}
        check('tool_integrity', tool_integrity)

        # package fixtures
        source = workspace / 'src-1.0.0' / PLUGIN_ID
        write_plugin(source, PLUGIN_ID, '1.0.0', 'first')
        package_v1 = zip_plugin(source, workspace / f'{PLUGIN_ID}-1.0.0.zip')

        def inspect_new():
            status, payload = run('inspect', str(package_v1))
            require(payload.get('ok'), payload)
            require(payload.get('action') == 'new', payload)
            require(payload.get('compatible') is True, payload)
            require(payload.get('protocol') == 2, payload)
            require(payload.get('fileCount') == 4, payload)
            require(payload.get('repository') == f'BrOKenAG/ordina-plugin-{PLUGIN_ID}',
                    payload)
            return {'action': 'new', 'files': payload['fileCount']}
        check('inspect_new', inspect_new)

        # 4. local test installs into this HOME only
        def local_test():
            status, payload = run('local-test', str(package_v1))
            require(payload.get('ok'), payload)
            require(payload.get('installed') == '1.0.0', payload)
            utility = (home / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/'
                              f'Fusion/Scripts/Utility/{PLUGIN_ID}.py')
            require(utility.is_file(), 'utility script not installed')
            state = (home / 'Library/Application Support/ordina/Plugins'
                     f'/{PLUGIN_ID}/state.json')
            require(state.is_file(), 'install state missing')
            registry = lab_repo / 'plugins' / 'registry.source.json'
            require(json.loads(registry.read_text())['plugins'] == {},
                    'local test touched the registry source')
            return {'installed': '1.0.0', 'registryUntouched': True}
        check('local_test', local_test)

        def local_uninstall():
            status, payload = run('local-uninstall', PLUGIN_ID)
            require(payload.get('ok'), payload)
            require(payload.get('uninstalled') is True, payload)
            return {'uninstalled': True}
        check('local_uninstall', local_uninstall)

        # 5. preview: new plugin, repository must be created
        def preview():
            status, payload = run('preview', str(package_v1), '--notes', '首次发布')
            require(payload.get('ok'), payload)
            require(payload.get('blockers') == [], payload)
            require(len(payload.get('fingerprint', '')) == 64, payload)
            require(payload.get('repositoryExists') is False, payload)
            require('create' in payload.get('steps', []), payload)
            require(payload.get('action') == 'new', payload)
            return {'fingerprint': payload['fingerprint'][:12],
                    'checks': len(payload['checks'])}
        check('preview', preview)

        # 6. a stale preview can never be executed
        def preview_stale():
            payload = expect_failure('preview.stale', 'publish', str(package_v1),
                                     '--notes', 'x', '--fingerprint', '0' * 64,
                                     '--create-repository')
            return {'code': payload['code']}
        check('preview_stale', preview_stale)

        # 7. malicious archives are refused before anything is written
        def malicious_zip():
            bad = workspace / 'evil.zip'
            with zipfile.ZipFile(bad, 'w') as archive:
                archive.writestr('../evil.py', 'print(1)')
            payload = expect_failure('package.', 'inspect', str(bad))
            return {'code': payload['code']}
        check('malicious_zip', malicious_zip)

        def zip_bomb():
            bomb = workspace / 'bomb.zip'
            with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('plugin.json', '{}')
                archive.writestr('zeros.py', 'A' * 900_000, compress_type=zipfile.ZIP_DEFLATED)
            payload = expect_failure('package.', 'inspect', str(bomb))
            return {'code': payload['code']}
        check('zip_bomb', zip_bomb)

        # 8. publish the new plugin through the real six-stage transaction
        def publish_new():
            status, preview_payload = run('preview', str(package_v1), '--notes', '首次发布')
            require(preview_payload.get('ok'), preview_payload)
            status, payload = run('publish', str(package_v1), '--notes', '首次发布',
                                  '--fingerprint', preview_payload['fingerprint'],
                                  '--create-repository')
            require(payload.get('ok'), payload)
            require(payload.get('stages') == ['preflight', 'asset', 'gate', 'feeds',
                                              'push', 'postflight'], payload)
            journal = Path(payload['journal'])
            require(journal.is_file(), 'journal missing')
            document = json.loads(journal.read_text())
            require(document['status'] == 'complete', document['status'])
            require(set(document['repositories']) ==
                    {f'BrOKenAG/ordina-plugin-{PLUGIN_ID}', 'BrOKenAG/ordina-releases'},
                    list(document['repositories']))
            source = json.loads((lab_repo / 'plugins' / 'registry.source.json').read_text())
            require(source['plugins'][PLUGIN_ID]['version'] == '1.0.0', source)
            return {'journal': journal.name, 'stages': len(payload['stages'])}
        check('publish_new', publish_new)

        # 9. a client that has never seen the plugin discovers and installs it.
        #    The published bundle is rehosted on loopback because build_feeds
        #    targets GitHub URLs; the bytes are the ones the tool produced.
        def lab_blob(repo, name):
            """Read one file from the isolated repository store.

            The lab backend keeps a JSON map of {path: base64 content} per
            repository, exactly like the GitHub tree it stands in for.
            """
            directory = lab_root / 'lab' / 'repos' / repo
            path = directory / name
            source = path if path.is_file() else directory / 'state.json'
            require(source.is_file(), f'lab blob missing: {repo}/{name}')
            store = json.loads(source.read_text(encoding='utf-8'))
            require(name in store, f'lab blob has no {name}')
            return base64.b64decode(store[name])

        def client_discovers():
            repo = f'BrOKenAG/ordina-plugin-{PLUGIN_ID}'
            document, _ = decode_envelope(lab_blob(repo, 'manifest-v2.json'))
            entry = json.loads(json.dumps(document['plugins'][PLUGIN_ID]))
            candidates = sorted((lab_root / 'lab' / 'assets' / repo).rglob('*.json'))
            require(candidates, 'published asset missing')
            data = candidates[0].read_bytes()
            require(hashlib.sha256(data).hexdigest() == entry['sha256'], 'asset hash mismatch')
            asset_url = (f'http://127.0.0.1:{port}/{repo}/releases/download/'
                         f'plugin-{PLUGIN_ID}-v1.0.0/{PLUGIN_ID}.json')
            entry['url'] = asset_url
            server.routes[f'/{repo}/releases/download/plugin-{PLUGIN_ID}-v1.0.0/'
                          f'{PLUGIN_ID}.json'] = data
            server.routes[f'/ordina-plugin-{PLUGIN_ID}/manifest-v2.json'] = json.dumps(
                lab_envelope({'format': 1, 'plugins': {PLUGIN_ID: entry}}),
                separators=(',', ':')).encode('utf-8')
            descriptor = entry['descriptor']
            server.routes['/registry.json'] = json.dumps(lab_envelope({
                'format': 2,
                'plugins': {PLUGIN_ID: {
                    'repository': repo,
                    'feed': f'http://127.0.0.1:{port}/ordina-plugin-{PLUGIN_ID}/manifest-v2.json',
                    'downloadPrefix': f'/{repo}/releases/download/',
                    'kind': 'bundle', 'name': descriptor['name'],
                    'summary': descriptor['summary'], 'symbol': descriptor['symbol'],
                    'version': descriptor['version'],
                    'minClientVersion': descriptor['minClientVersion'],
                    'protocol': 2, 'capabilities': descriptor['capabilities'],
                }},
            }), separators=(',', ':')).encode('utf-8')
            plugin_env = {
                'ORDINA_PLUGIN_LAB': '1',
                'ORDINA_PLUGIN_REGISTRY': f'http://127.0.0.1:{port}/registry.json',
                'ORDINA_PLUGIN_PUBLIC_KEY': lab_public_key,
                'ORDINA_CLIENT_VERSION': CLIENT_VERSION,
            }
            status, listing = run('list', env=plugin_env, binary=plugin_harness)
            require(listing.get('ok'), listing)
            item = [entry for entry in listing['items'] if entry['id'] == PLUGIN_ID]
            require(item, listing)
            require(item[0]['status'] == 'notInstalled', item[0])
            require(item[0]['installed'] is False, item[0])
            status, installed = run('install', PLUGIN_ID, env=plugin_env, binary=plugin_harness)
            require(installed.get('ok'), installed)
            require(installed.get('version') == '1.0.0', installed)
            status, listing = run('list', env=plugin_env, binary=plugin_harness)
            item = [entry for entry in listing['items'] if entry['id'] == PLUGIN_ID][0]
            require(item['status'] == 'current:1.0.0', item)
            status, removed = run('uninstall', PLUGIN_ID, env=plugin_env, binary=plugin_harness)
            require(removed.get('ok'), removed)
            return {'discovered': True, 'installed': '1.0.0', 'uninstalled': True}
        check('client_discovers_new_plugin', client_discovers)

        # 10. update the same plugin
        source_110 = workspace / 'src-1.1.0' / PLUGIN_ID
        write_plugin(source_110, PLUGIN_ID, '1.1.0', 'second')
        package_v110 = zip_plugin(source_110, workspace / f'{PLUGIN_ID}-1.1.0.zip')

        def publish_update():
            status, payload = run('inspect', str(package_v110))
            require(payload.get('ok'), payload)
            require(payload.get('action') == 'update', payload)
            require(payload.get('previous') == '1.0.0', payload)
            status, preview_payload = run('preview', str(package_v110), '--notes', '第二版')
            require(preview_payload.get('ok'), preview_payload)
            status, result = run('publish', str(package_v110), '--notes', '第二版',
                                 '--fingerprint', preview_payload['fingerprint'])
            require(result.get('ok'), result)
            source = json.loads((lab_repo / 'plugins' / 'registry.source.json').read_text())
            require(source['plugins'][PLUGIN_ID]['version'] == '1.1.0', source)
            return {'previous': '1.0.0', 'version': '1.1.0'}
        check('publish_update', publish_update)

        # 11. an interrupted transaction stays resumable
        source_120 = workspace / 'src-1.2.0' / PLUGIN_ID
        write_plugin(source_120, PLUGIN_ID, '1.2.0', 'third')
        package_v120 = zip_plugin(source_120, workspace / f'{PLUGIN_ID}-1.2.0.zip')
        pending_journal = {}

        def interrupted_publish():
            status, preview_payload = run('preview', str(package_v120), '--notes', '第三版')
            require(preview_payload.get('ok'), preview_payload)
            payload = expect_failure('publish.failed', 'publish', str(package_v120),
                                     '--notes', '第三版',
                                     '--fingerprint', preview_payload['fingerprint'],
                                     env={'ORDINA_DEV_LAB_FAIL_AFTER': '1'})
            require(payload.get('stage') == 'feeds', payload)
            journal = Path(payload['journal'])
            pending_journal['path'] = journal
            document = json.loads(journal.read_text())
            require(document['status'] == 'publishing', document['status'])
            require(len(document['repositories']) == 2, document['repositories'])
            status, listing = run('status')
            require(listing.get('pending') == 1, listing)
            return {'status': 'publishing', 'journal': journal.name}
        check('interrupted_publish', interrupted_publish)

        def concurrent_blocked():
            # Another publisher holds the release lock. The lock file lives in
            # the isolated root, exactly where a second process would find it.
            lock = lab_root / 'lab' / 'lock'
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('another-publisher', encoding='utf-8')
            try:
                payload = expect_failure('publish.failed', 'publish',
                                         str(package_v120), '--notes', '第三版并发')
            finally:
                lock.unlink()
            require('lock' in payload.get('error', ''), payload)
            return {'code': payload['code'], 'blocked': True}
        check('concurrent_blocked', concurrent_blocked)

        # 12. a tool update must wait for the open transaction
        def pending_blocks_tool_update():
            publish_tool_manifest(['1.0.0', '1.1.0'])
            payload = expect_failure('harness', 'tool-install', '1.1.0')
            require('未完成' in payload.get('error', ''), payload)
            return {'blocked': True}
        check('pending_blocks_tool_update', pending_blocks_tool_update)

        def resume_publish():
            status, payload = run('resume', str(pending_journal['path']))
            require(payload.get('ok'), payload)
            document = json.loads(Path(pending_journal['path']).read_text())
            require(document['status'] == 'complete', document['status'])
            source = json.loads((lab_repo / 'plugins' / 'registry.source.json').read_text())
            require(source['plugins'][PLUGIN_ID]['version'] == '1.2.0', source)
            return {'status': 'complete', 'version': '1.2.0'}
        check('resume_publish', resume_publish)

        # 13. the tool channel updates independently, keeping state
        def tool_update():
            before = run('status')[1]
            require(before.get('installed') == '1.0.0', before)
            status, payload = run('tool-install', '1.1.0')
            require(payload.get('ok'), payload)
            require(payload.get('installed') == '1.1.0', payload)
            after = run('status')[1]
            require(after.get('hello') == '1.1.0', after)
            require(len(after.get('journals', [])) == len(before.get('journals', [])),
                    'journals lost during tool update')
            require(after.get('records') == before.get('records'),
                    'records lost during tool update')
            require(after.get('integrity') is True, after)
            return {'from': '1.0.0', 'to': '1.1.0', 'journals': len(after.get('journals', []))}
        check('tool_update', tool_update)

        def publish_after_tool_update():
            source_130 = workspace / 'src-1.3.0' / PLUGIN_ID
            write_plugin(source_130, PLUGIN_ID, '1.3.0', 'fourth')
            package = zip_plugin(source_130, workspace / f'{PLUGIN_ID}-1.3.0.zip')
            status, preview_payload = run('preview', str(package), '--notes', '第四版')
            require(preview_payload.get('ok'), preview_payload)
            status, payload = run('publish', str(package), '--notes', '第四版',
                                  '--fingerprint', preview_payload['fingerprint'])
            require(payload.get('ok'), payload)
            return {'version': payload.get('version')}
        check('publish_after_tool_update', publish_after_tool_update)

        # 14. roll back the newest release through the journal
        def rollback_release():
            status, listing = run('journal-list')
            target = None
            for item in listing['journals']:
                if item['version'] == '1.3.0' and item['rollbackable']:
                    target = item['journal']
                    break
            require(target, listing)
            status, payload = run('rollback', target, '--reason', '验收回滚')
            require(payload.get('ok'), payload)
            require(payload.get('status') == 'rolled_back', payload)
            document = json.loads(Path(target).read_text())
            require(document['status'] == 'rolled_back', document['status'])
            # the served registry is restored to the bytes before the release
            registry, _ = decode_envelope(lab_blob('BrOKenAG/ordina-releases',
                                                   'registry.json'))
            require(registry['plugins'][PLUGIN_ID]['version'] == '1.2.0',
                    registry['plugins'][PLUGIN_ID])
            return {'status': 'rolled_back', 'registryVersion': '1.2.0'}
        check('rollback_release', rollback_release)

        def tool_rollback():
            status, payload = run('tool-rollback')
            require(payload.get('ok'), payload)
            require(payload.get('installed') == '1.0.0', payload)
            status, after = run('status')
            require(after.get('hello') == '1.0.0', after)
            require(after.get('records') == run('status')[1].get('records'), 'records lost')
            return {'installed': '1.0.0'}
        check('tool_rollback', tool_rollback)

        # 15. authorisation is enforced by the tool, not by the UI
        def unsigned_credential():
            payload = expect_failure('auth.', 'preview', str(package_v1), '--notes', 'x',
                                     env={'ORDINA_DEV_CREDENTIALS': str(tampered)})
            return {'code': payload['code']}
        check('unsigned_credential', unsigned_credential)

        def wrong_scope():
            payload = expect_failure('auth.scope', 'preview', str(package_v1), '--notes', 'x',
                                     env={'ORDINA_DEV_CREDENTIALS': str(limited)})
            return {'code': payload['code']}
        check('wrong_scope', wrong_scope)

        def version_downgrade():
            source_090 = workspace / 'src-0.9.0' / PLUGIN_ID
            write_plugin(source_090, PLUGIN_ID, '0.9.0', 'old')
            package = zip_plugin(source_090, workspace / f'{PLUGIN_ID}-0.9.0.zip')
            status, payload = run('inspect', str(package))
            require(payload.get('ok'), payload)
            require(payload.get('action') == 'downgrade', payload)
            expect_failure('release.invalid', 'publish', str(package), '--notes', '降级')
            return {'action': 'downgrade'}
        check('version_downgrade', version_downgrade)

        def records_preserved():
            status, payload = run('journal-list')
            kinds = {item['status'] for item in payload['journals']}
            require('complete' in kinds and 'rolled_back' in kinds, kinds)
            status, history = run('status')
            require(history.get('records') >= 4, history)
            return {'records': history.get('records'), 'journals': len(payload['journals'])}
        check('records_preserved', records_preserved)

    finally:
        server.stop()
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)

    passed = all(value == 'pass' for value in checks.values())
    report = {
        'format': 1,
        'passed': passed,
        'clientVersion': CLIENT_VERSION,
        'compiledOnce': True,
        'harnessSha256': harness_digest,
        'toolVersion': '1.1.0',
        'toolProtocol': 1,
        'checks': checks,
        'details': details,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    width = max(len(name) for name in CHECKS)
    print()
    for name in CHECKS:
        print(f'{name.ljust(width)}  {checks.get(name, "missing")}')
    print(f'\n报告：{args.report}')
    if not passed:
        missing = [name for name in CHECKS if checks.get(name) != 'pass']
        sys.exit('开发者中心验收未通过：' + ', '.join(missing))


if __name__ == '__main__':
    main()
