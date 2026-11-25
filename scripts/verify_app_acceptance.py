#!/usr/bin/env python3
"""Verify the isolated real-App acceptance environment end to end.

This does not launch the App (that needs a real Xcode Debug build), but it does
exercise every resource the App will read: the loopback Registry, the signed
developer-tool channel, both tool Release assets, the plugin Registry, a plugin
manifest and its asset. Every signed document is verified against the isolated
lab key, every asset is verified against its declared SHA-256, and every URL is
asserted to point at 127.0.0.1 so no production resource can be reached.

The port is fixed by environment.json because the generated signed manifests
contain absolute loopback URLs. If start-registry.sh is already running, this
script reuses that server instead of failing on a busy port, so it can also be
run in the middle of a manual acceptance run (plugin versions will have
advanced past the initial 1.0.0, which is expected).

    python3 scripts/verify_app_acceptance.py
"""
import argparse
import base64
import hashlib
import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lab_signing                                            # noqa: E402
from app_acceptance_server import AcceptanceServer             # noqa: E402

CHECKS = ('environment_isolation', 'tool_manifest', 'tool_assets', 'registry',
          'plugin_manifest', 'plugin_asset', 'no_production_urls')
REPORT = ROOT / 'docs' / 'validation' / 'app-acceptance-server.json'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def decoded(raw, key, label):
    obj = json.loads(raw)
    payload = base64.b64decode(obj['payload'], validate=True)
    signature = base64.b64decode(obj['signature'], validate=True)
    require(lab_signing.verify(key, payload, signature),
            f'{label}：隔离签名校验失败')
    return json.loads(payload)


def listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT / 'dist' / 'app-acceptance')
    parser.add_argument('--report', type=Path, default=REPORT)
    args = parser.parse_args()

    root = args.root.resolve()
    require((root / 'registry' / 'routes.json').is_file(),
            '请先运行 python3 scripts/prepare_app_acceptance.py')
    environment = json.loads((root / 'environment.json').read_text(encoding='utf-8'))
    port = int(environment['port'])
    checks = {}
    details = {}

    def check(name, function):
        try:
            details[name] = function()
            checks[name] = 'pass'
            print(f'[pass] {name}')
        except Exception as error:                                # noqa: BLE001
            checks[name] = 'fail'
            details[name] = {'error': str(error)}
            print(f'[fail] {name}: {error}')

    seed = lab_signing.read_seed(str(root / 'credentials' / 'lab-signing.key'))
    key = lab_signing.public_key(seed)

    # Reuse start-registry.sh when it is already running; otherwise start a
    # throwaway server for this check only.
    server = None
    thread = None
    reused = listening(port)
    if not reused:
        server = AcceptanceServer(root, port)
        thread = threading.Thread(target=server.httpd.serve_forever, daemon=True)
        thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=10) as response:
            return response.status, response.read()

    def signed(path):
        status, raw = get(path)
        require(status == 200, f'{path} 返回 {status}')
        return decoded(raw, key, path)

    prefix = f'http://127.0.0.1:{port}/'
    collected_urls = []

    try:
        def environment_isolation():
            summary = json.loads((root / 'environment.json').read_text(encoding='utf-8'))
            require(summary.get('requiredBuildMode') == 'Debug',
                    '验收环境未声明 Debug 构建要求')
            require(summary.get('productionResourcesTouched') is False,
                    '验收环境声称触碰了生产资源')
            require('Debug' in summary.get('launchApp', ''),
                    '默认启动 App 不是 Debug 产物')
            env = (root / 'env.sh').read_text(encoding='utf-8')
            for marker in ('ORDINA_PLUGIN_LAB=1', 'ORDINA_DEV_LAB=1',
                           'ORDINA_DEV_BACKEND=lab', f'HOME="{root / "home"}"'):
                require(marker in env, f'env.sh 缺少 {marker}')
            home = root / 'home'
            require(home.is_dir(), '隔离 HOME 不存在')
            packages = sorted(path.name for path in (root / 'packages').glob('*.zip'))
            require(packages == ['brand-new-plugin.zip', 'corrupt.zip',
                                'existing-plugin-update.zip', 'malicious.zip',
                                'zip-bomb.zip'], packages)
            tools = sorted(path.name for path in (root / 'tools').glob('*.pyz'))
            require(tools == ['ordina-publisher-1.0.0.pyz',
                              'ordina-publisher-1.1.0.pyz'], tools)
            require((root / 'tools' / tools[0]).read_bytes() !=
                    (root / 'tools' / tools[1]).read_bytes(),
                    '两个工具版本内容相同')
            return {'requiredBuildMode': 'Debug', 'packages': packages,
                    'toolVersions': ['1.0.0', '1.1.0'], 'isolatedHome': str(home)}

        def tool_manifest():
            document = signed('/dev-tool/manifest.json')
            collected_urls.extend(item['url'] for item in document['versions'])
            versions = [item['version'] for item in document['versions']]
            require(versions == ['1.0.0', '1.1.0'], versions)
            require(all(item['protocol'] == 1 for item in document['versions']),
                    '工具协议版本不一致')
            require(all(item['minClientVersion'] == '1.10.0'
                        for item in document['versions']),
                    'minClientVersion 不是 1.10.0')
            return {'versions': versions, 'channel': document['channel']}

        def tool_assets():
            document = signed('/dev-tool/manifest.json')
            sizes = {}
            for item in document['versions']:
                route = '/' + item['url'].split(prefix, 1)[1]
                status, raw = get(route)
                require(status == 200, f'{route} 返回 {status}')
                require(hashlib.sha256(raw).hexdigest() == item['sha256'],
                        f"{item['version']} 资产 SHA-256 不一致")
                require(len(raw) == item['size'],
                        f"{item['version']} 资产大小不一致")
                sizes[item['version']] = len(raw)
            return {'sizes': sizes, 'hashAlgorithm': 'sha256'}

        def registry():
            document = signed('/registry.json')
            require(document.get('format') == 2, 'Registry 不是 format 2')
            plugins = list(document.get('plugins', {}))
            require('minutes-lab' in plugins, f'初始插件缺失：{plugins}')
            for item in document['plugins'].values():
                collected_urls.append(item['feed'])
                collected_urls.append(item.get('downloadPrefix', ''))
            return {'plugins': plugins, 'format': 2}

        def plugin_manifest():
            document = signed('/ordina-plugin-minutes-lab/manifest-v2.json')
            entries = list(document.get('plugins', {}).values())
            require(len(entries) == 1, f'插件清单条目数异常：{len(entries)}')
            entry = entries[0]
            collected_urls.append(entry['url'])
            # A manual acceptance run publishes updates, so the version moves
            # past the initial 1.0.0. Only require a well-formed version; the
            # asset URL and its SHA-256 are checked separately.
            require(bool(entry.get('version')), '插件清单缺少版本')
            require(entry['url'].startswith(prefix),
                    f"插件资产 URL 不在隔离服务：{entry['url']}")
            return {'version': entry['version'], 'url': entry['url']}

        def plugin_asset():
            document = signed('/ordina-plugin-minutes-lab/manifest-v2.json')
            entry = list(document['plugins'].values())[0]
            route = entry['url'].split(f'127.0.0.1:{port}', 1)[1]
            status, raw = get(route)
            require(status == 200, f'{route} 返回 {status}')
            require(hashlib.sha256(raw).hexdigest() == entry['sha256'],
                    '插件资产 SHA-256 不一致')
            return {'size': len(raw), 'hash': 'ok'}

        def no_production_urls():
            absolute = 0
            for url in collected_urls:
                if not url:
                    continue
                require('brokenag.github.io' not in url, f'出现生产 Pages URL：{url}')
                require('github.com' not in url, f'出现生产 GitHub URL：{url}')
                if url.startswith('/'):
                    # Registry downloadPrefix is intentionally a loopback-relative
                    # path; the server resolves it against 127.0.0.1.
                    continue
                require(url.startswith(prefix), f'出现非隔离绝对 URL：{url}')
                absolute += 1
            for name in ('env.sh', 'launch.sh', 'README.md', 'environment.json'):
                text = (root / name).read_text(encoding='utf-8')
                require('brokenag.github.io' not in text,
                        f'{name} 含生产 Pages 地址')
            return {'checkedUrls': len([u for u in collected_urls if u]),
                    'absoluteLoopbackUrls': absolute, 'allLoopback': True}

        for name, function in (
            ('environment_isolation', environment_isolation),
            ('tool_manifest', tool_manifest),
            ('tool_assets', tool_assets),
            ('registry', registry),
            ('plugin_manifest', plugin_manifest),
            ('plugin_asset', plugin_asset),
            ('no_production_urls', no_production_urls),
        ):
            check(name, function)
    finally:
        if server is not None:
            server.httpd.shutdown()
            server.httpd.server_close()
            thread.join(timeout=5)

    passed = all(value == 'pass' for value in checks.values())
    report = {
        'format': 1, 'passed': passed, 'root': str(root), 'port': port,
        'reusedRunningServer': reused,
        'checks': checks, 'details': details,
        'trustChain': 'isolated-lab-key',
        'productionResourcesTouched': False,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(f'\n报告：{args.report}')
    if not passed:
        sys.exit('隔离验收环境未通过：' + ', '.join(
            name for name, value in checks.items() if value != 'pass'))
    print('ALL OK')


if __name__ == '__main__':
    main()
