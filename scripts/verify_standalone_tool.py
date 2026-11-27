#!/usr/bin/env python3
"""Standalone-deployment acceptance for the PRODUCTION publishing tool.

The signed-in-repository ``dist/dev-tool/ordina-publisher-<v>.pyz`` is copied
into a directory containing no source tree and run with a minimal environment:
no ORDINA_DEV_LAB, no lab sign-key shortcut, no repository checkout on the
import path. It must still

  * answer ``hello`` / ``doctor`` and find the pinned signing tools,
  * validate a real ZIP package (``inspect``),
  * reject a forged credential from a mocked Security API,
  * reject an untrusted ``sign_update`` signature against the pinned key.

A mocked Security API returns a forged credential to the production parser;
the real macOS Keychain is never read or changed. The only ``sign_update`` reachable is a recording stub, so no
production release key is ever used.

    python3 scripts/verify_standalone_tool.py
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
import build_dev_tool                                        # noqa: E402

CHECKS = ('archive_isolated', 'archive_integrity', 'hello', 'doctor',
          'tools_root', 'inspect', 'credential_read', 'file_credential_refused',
          'untrusted_signing_rejected', 'lab_backend_refused', 'no_production_key')
REPORT = ROOT / 'docs' / 'validation' / 'standalone-tool.json'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def minimal_env(home, deployment, extra=None):
    """No inherited ORDINA_* variables, no source tree on any path."""
    values = {
        'HOME': str(home),
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'NO_PROXY': 'localhost,127.0.0.1',
        'ORDINA_DEV_REPO': str(home / 'work-repo'),
        'ORDINA_DEV_CLIENT_VERSION': '1.10.0',
        'PYTHONUNBUFFERED': '1',
        'STUB_LOG': str(deployment / 'sign-calls.jsonl'),
    }
    values.update(extra or {})
    return values


def run_tool(archive, arguments, environment, timeout=90):
    # HOME does not isolate macOS Keychain. Intercept only security queries
    # inside this subprocess; never inspect or change the user's keychain.
    launcher = """import os, runpy, subprocess, sys
real = subprocess.run
def isolated(args, *a, **kw):
    if args and args[0] == '/usr/bin/security':
        raw = os.environ.get('ORDINA_STUB_CREDENTIAL')
        return subprocess.CompletedProcess(args, 0 if raw else 44, (raw or '') + ('\\n' if raw else ''), '')
    return real(args, *a, **kw)
subprocess.run = isolated
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run(['/usr/bin/python3', '-c', launcher, str(archive), *arguments],
                            env=environment, capture_output=True, text=True,
                            timeout=timeout)
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except ValueError:
        payload = {'raw': result.stdout[-400:]}
    return result.returncode, payload, result.stderr[-400:]


def make_zip(target):
    meta = {
        'id': 'standalone-demo', 'name': 'Standalone Demo', 'version': '1.0.0',
        'summary': 'Built outside the source tree', 'symbol': 'note.text',
        'minClientVersion': '1.10.0', 'protocol': 2,
        'capabilities': ['resolve.utilityScript'],
        'supportDirectoryName': 'Standalone Demo',
        'files': {'standalone-demo.py': {'role': 'utilityScript',
                                         'installName': 'standalone-demo.py'}},
    }
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('plugin.json', json.dumps(meta))
        archive.writestr('standalone-demo.py', 'VERSION = "1.0.0"\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', type=Path, default=REPORT)
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()

    version = build_dev_tool.read_version()
    source_archive = ROOT / 'dist' / 'dev-tool' / f'ordina-publisher-{version}.pyz'
    require(source_archive.is_file(),
            f'缺少生产工具包：{source_archive}（先运行 build_dev_tool.py --version {version} --sign）')
    archive_bytes = source_archive.read_bytes()
    manifest = json.loads((ROOT / 'dev-tool' / 'manifest.source.json').read_text())
    entry = next(item for item in manifest['versions'] if item['version'] == version)

    workspace = Path(tempfile.mkdtemp(prefix='ordina-standalone-'))
    home = workspace / 'home'
    deployment = workspace / 'deployment'
    home.mkdir(parents=True)
    deployment.mkdir()
    archive = deployment / source_archive.name
    archive.write_bytes(archive_bytes)
    (home / 'work-repo' / 'plugins').mkdir(parents=True)
    (home / 'work-repo' / 'plugins' / 'registry.source.json').write_text(
        json.dumps({'format': 2, 'plugins': {}}) + '\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q'], cwd=home / 'work-repo', check=True,
                   capture_output=True)
    subprocess.run(['git', '-C', str(home / 'work-repo'), 'add', '.'], check=True,
                   capture_output=True)
    subprocess.run(['git', '-C', str(home / 'work-repo'), '-c', 'user.email=lab@invalid',
                    '-c', 'user.name=lab', 'commit', '-q', '-m', 'isolated'],
                   check=True, capture_output=True)

    # The official signing binary is pre-seeded at the DEFAULT per-user tools
    # location. No ORDINA_SPARKLE_TOOLS override is used for the main run, so
    # this proves sparkle_tools resolves its path without a source tree or a
    # lab variable.
    tools_bin = (home / 'Library' / 'Application Support' / 'ordina' / 'Publisher'
                 / 'tools' / 'sparkle-2.10.1' / 'bin')
    tools_bin.mkdir(parents=True)
    sign_stub = tools_bin / 'sign_update'
    sign_stub.write_text('''#!/usr/bin/python3
import json, os, sys
arguments = sys.argv[1:]
log = os.environ.get('STUB_LOG')
if log:
    with open(log, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(arguments) + '\\n')
if '--verify' not in arguments:
    print('stub-signature')
sys.exit(0)
''', encoding='utf-8')
    sign_stub.chmod(0o755)

    checks, details = {}, {}

    def check(name, operation):
        try:
            details[name] = operation() or {}
            checks[name] = 'pass'
            print(f'{name:28s} pass')
        except Exception as error:              # noqa: BLE001
            checks[name] = f'fail: {error}'
            print(f'{name:28s} FAIL  {error}')

    env = minimal_env(home, deployment)

    try:
        def archive_isolated():
            names = sorted(zipfile.ZipFile(archive).namelist())
            forbidden = {'dev_lab_backend.py', 'lab_signing.py'}
            require(not (set(names) & forbidden),
                    '生产工具包含实验室模块：' + str(set(names) & forbidden))
            require(not any(name.startswith('test_') or name.startswith('verify_')
                            for name in names), '生产工具包含测试模块')
            require(list(deployment.iterdir()) == [archive],
                    '部署目录里还有其它文件（源码树？）')
            return {'members': len(names), 'directory': str(deployment)}
        check('archive_isolated', archive_isolated)

        def archive_integrity():
            require(hashlib.sha256(archive_bytes).hexdigest() == entry['sha256'],
                    '生产工具包与签名清单哈希不一致')
            require(len(archive_bytes) == entry['size'], '工具包大小与清单不一致')
            with zipfile.ZipFile(archive) as zipped:
                require(zipped.read('VERSION').decode().strip()
                        == f'ordina-publisher {version}', 'VERSION 与清单版本不一致')
                entry_point = zipped.read('__main__.py').decode()
                require(f'TOOL_VERSION = {version!r}' in entry_point,
                        '入口脚本没有固定版本')
                for name in zipped.namelist():
                    if not name.endswith('.py'):
                        continue
                    text = zipped.read(name).decode('utf-8')
                    for marker in ('BEGIN PRIVATE KEY', 'ghp_', 'gho_',
                                   '-----BEGIN RSA', '-----BEGIN EC'):
                        require(marker not in text,
                                f'{name} 含私钥/令牌材料：{marker}')
            # A lab variable name appearing in a source file is not by itself a
            # bypass: what matters is that the lab signing shortcut cannot be
            # selected. lab_signing is absent from the production archive, so a
            # forced lab environment must fail closed with an ImportError.
            probe = ('import sys, json; sys.path.insert(0, %r);'
                     'import feed_signing as f;'
                     'print(json.dumps({"mode": f.signing_mode()}))' % str(archive))
            forced = dict(env, ORDINA_DEV_LAB='1',
                          ORDINA_DEV_SIGN_KEY=str(workspace / 'not-a-key'))
            result = subprocess.run(['/usr/bin/python3', '-c', probe], env=forced,
                                    capture_output=True, text=True, timeout=30)
            require(result.returncode != 0,
                    '生产包在强制 lab 环境下仍可选中实验室签名密钥')
            require('lab_signing' in result.stderr, result.stderr[-200:])
            return {'sha256': entry['sha256'][:16], 'version': version,
                    'labSigningUnreachable': True}
        check('archive_integrity', archive_integrity)

        def hello():
            status, payload, stderr = run_tool(archive, ['hello'], env)
            require(status == 0 and payload.get('ok'), (payload, stderr))
            data = payload['data']
            require(data['version'] == version, data)
            require(data['protocol'] == 1 and data['lab'] is False, data)
            require(data['backend'] == 'production', data)
            require(data['minClientVersion'] == '1.10.0', data)
            return {'version': data['version'], 'python': data['python']}
        check('hello', hello)

        def doctor():
            status, payload, stderr = run_tool(archive, ['doctor'], env)
            require(status == 0 and payload.get('ok'), (payload, stderr))
            data = payload['data']
            require(data['signing'] == 'keychain', data)
            found = {item['id']: item['ok'] for item in data['requirements']}
            require(found['python'] and found['signing'] and found['repository'],
                    found)
            require(data['credential']['code'] in ('auth.missing', 'auth.signature',
                                                   None) or
                    data['credential']['valid'] is True, data['credential'])
            return {'signing': data['signing'], 'requirements': found}
        check('doctor', doctor)

        def tools_root():
            script = (
                'import os, sys, json; sys.path.insert(0, %r);'
                'import sparkle_tools as s;'
                'print(json.dumps({"root": str(s.tools_root()),'
                '"bin": str(s.tools_bin()),'
                '"has_sign_update": (s.tools_bin()/"sign_update").is_file(),'
                '"override": os.environ.get("ORDINA_SPARKLE_TOOLS")}))' % str(archive)
            )
            result = subprocess.run(['/usr/bin/python3', '-c', script], env=env,
                                    capture_output=True, text=True, timeout=30)
            require(result.returncode == 0, result.stderr)
            value = json.loads(result.stdout.strip())
            require(value['has_sign_update'], value)
            require(str(ROOT) not in value['root'], '工具目录仍指向源码仓库：' + value['root'])
            require(str(Path(value['root']).resolve()).startswith(
                        str(home.resolve())), value)
            require(value['override'] is None, '意外依赖 ORDINA_SPARKLE_TOOLS')
            # An explicit override must win, and changing ORDINA_DEV_REPO must
            # not move the tools directory.
            override = workspace / 'explicit-tools'
            (override / 'bin').mkdir(parents=True)
            (override / 'bin' / 'sign_update').write_bytes(sign_stub.read_bytes())
            moved = dict(env, ORDINA_SPARKLE_TOOLS=str(override),
                         ORDINA_DEV_REPO=str(workspace / 'somewhere-else'))
            result = subprocess.run(['/usr/bin/python3', '-c', script],
                                    env=moved, capture_output=True, text=True,
                                    timeout=30)
            value2 = json.loads(result.stdout.strip())
            require(value2['root'] == str(override.resolve()), value2)
            require(value2['has_sign_update'], value2)
            return {'default': value['root'], 'override': value2['root']}
        check('tools_root', tools_root)

        def inspect():
            package = make_zip(workspace / 'package.zip')
            status, payload, stderr = run_tool(
                archive, ['inspect', '--zip', str(package)], env)
            require(status == 0 and payload.get('ok'), (payload, stderr))
            data = payload['data']
            require(data['action'] == 'new', data)
            require(data['compatibility']['ok'] is True, data)
            require(data['package']['fileCount'] == 2, data)
            require(data['repository']['slug']
                    == 'BrOKenAG/ordina-plugin-standalone-demo', data)
            # Inspecting an untrusted archive must not need any repository at
            # all: this is the strongest form of "outside the source tree".
            bare = {key: value for key, value in env.items()
                    if key != 'ORDINA_DEV_REPO'}
            status2, payload2, stderr2 = run_tool(
                archive, ['inspect', '--zip', str(package)], bare)
            require(status2 == 0 and payload2.get('ok'), (payload2, stderr2))
            return {'action': data['action'], 'files': data['package']['fileCount'],
                    'withoutRepository': True}
        check('inspect', inspect)

        def credential_read():
            # Exercise the real production parser with an isolated Security API
            # response. A forged credential must never be accepted.
            raw = json.dumps({'payload': base64.b64encode(b'{}').decode(),
                              'signature': 'stub-signature'})
            status, payload, stderr = run_tool(
                archive, ['credential-status'], dict(env, ORDINA_STUB_CREDENTIAL=raw))
            require(status == 0 and payload.get('ok'), (payload, stderr))
            value = payload['data']
            require(value['valid'] is False and value['code'] == 'auth.signature', value)
            return {'source': 'mock-security', 'forgedRejected': True,
                    'actualKeychainRead': False}
        check('credential_read', credential_read)

        def file_credential_refused():
            path = workspace / 'credential.json'
            path.write_text('{}', encoding='utf-8')
            path.chmod(0o600)
            status, payload, stderr = run_tool(
                archive, ['credential-status', '--credentials', str(path)], env)
            require(status == 0 and payload.get('ok'), (payload, stderr))
            value = payload['data']
            require(value['valid'] is False and value['code'] == 'auth.lab', value)
            return {'code': value['code']}
        check('file_credential_refused', file_credential_refused)

        def official_signing():
            log = deployment / 'sign-calls.jsonl'
            if log.exists():
                log.unlink()
            script = (
                'import sys, json; sys.path.insert(0, %r);'
                'import feed_signing as f;'
                'data, signature = f.sign_document({"a": 1});'
                'print(json.dumps({"mode": f.signing_mode(),'
                '"signature": signature, "bytes": len(data)}))' % str(archive)
            )
            result = subprocess.run(['/usr/bin/python3', '-c', script], env=env,
                                    capture_output=True, text=True, timeout=30)
            require(result.returncode != 0 and '官方公钥' in result.stderr,
                    'untrusted signature was accepted: ' + result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            require(any('--account' in call and 'ordina-sparkle' in call
                        and '-p' in call for call in calls), calls)
            require(any('--verify' in call for call in calls), calls)
            require(not any('--ed-key-file' in call or 'lab' in ' '.join(call)
                            for call in calls), calls)
            return {'mode': 'mock-keychain', 'forgedRejected': True, 'calls': len(calls), 'productionSigningVerified': False}
        check('untrusted_signing_rejected', official_signing)

        def lab_backend_refused():
            status, payload, stderr = run_tool(
                archive, ['status', '--backend', 'lab'], env)
            require(status != 0, '生产工具接受了隔离后端')
            require(payload.get('error', {}).get('code') == 'backend.lab', payload)
            return {'code': payload['error']['code']}
        check('lab_backend_refused', lab_backend_refused)

        def no_production_key():
            # The only sign_update is the recording stub; the test never sets
            # the lab signing key and never reads the ordina-sparkle Keychain
            # item. A keychain-mode run is the only signing path available.
            require('ORDINA_DEV_SIGN_KEY' not in env, '意外设置了实验室签名密钥')
            require('ORDINA_DEV_LAB' not in env, '意外启用了 lab 模式')
            require(sign_stub.is_file(), '签名替身缺失')
            real = Path('/usr/bin/security')
            require(real.is_file(), '')
            return {'labSignKey': False, 'labMode': False,
                    'signingPath': 'sign_update --account ordina-sparkle (stub)'}
        check('no_production_key', no_production_key)

    finally:
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)

    passed = all(value == 'pass' for value in checks.values())
    report = {
        'format': 1, 'passed': passed, 'toolVersion': version,
        'archiveSha256': hashlib.sha256(archive_bytes).hexdigest(),
        'archiveSize': len(archive_bytes), 'checks': checks, 'details': details,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(f'\n报告：{args.report}')
    if not passed:
        sys.exit('独立部署验收未通过：' + ', '.join(
            name for name, value in checks.items() if value != 'pass'))


if __name__ == '__main__':
    main()
