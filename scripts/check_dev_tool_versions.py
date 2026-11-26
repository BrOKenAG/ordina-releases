#!/usr/bin/env python3
"""Verify one coherent version across the developer-tool release channel.

Preserve historical versions and verify the latest candidate, its archive,
signed manifest and release instructions without reading a private key.

    python3 scripts/check_dev_tool_versions.py
"""
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dev_tool                                        # noqa: E402
import dev_publish                                           # noqa: E402

REPORT = ROOT / 'docs' / 'validation' / 'tool-versions.json'
LAB_MODULES = set(build_dev_tool.LAB_MODULES)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def current_entry(entries, version):
    def parsed(value):
        require(isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+){1,3}', value),
                '工具版本格式无效')
        parts = [int(part) for part in value.split('.')]
        return tuple(parts + [0] * (4 - len(parts)))
    require(isinstance(entries, list) and entries, '工具清单没有版本')
    versions = [parsed(item.get('version')) for item in entries]
    require(len(set(versions)) == len(versions), '工具清单存在重复版本')
    wanted = parsed(version)
    require(wanted == max(versions), '当前候选版本不是最新版本')
    matches = [item for item in entries if item['version'] == version]
    require(len(matches) == 1, '找不到唯一的当前候选版本')
    return matches[0]


def main():
    version = build_dev_tool.read_version()
    checks, details = {}, {}

    def check(name, operation):
        try:
            details[name] = operation() or {}
            checks[name] = 'pass'
            print(f'{name:30s} pass')
        except Exception as error:              # noqa: BLE001
            checks[name] = f'fail: {error}'
            print(f'{name:30s} FAIL  {error}')

    def declared_version():
        require(dev_publish.TOOL_VERSION == version,
                f'dev_publish.TOOL_VERSION={dev_publish.TOOL_VERSION} 与构建版本 {version} 不一致')
        require(dev_publish.PROTOCOL == 1, '协议版本变化未同步清单')
        return {'version': version, 'protocol': dev_publish.PROTOCOL}
    check('declared_version', declared_version)

    source = json.loads((ROOT / 'dev-tool' / 'manifest.source.json').read_text())
    entries = source['versions']

    def manifest_version():
        entry = current_entry(entries, version)
        require(entry['version'] == version, entry)
        require(entry['protocol'] == 1 and entry['minClientVersion'] == '1.10.0', entry)
        require(entry['asset'] == f'ordina-publisher-{version}.pyz', entry)
        require(entry['tag'] == f'dev-tool-v{version}', entry)
        require(build_dev_tool.ASSET_TAG.format(version=version) == entry['tag'],
                'ASSET_TAG 与清单 tag 不一致')
        require(entry['url'].endswith(f'/dev-tool-v{version}/ordina-publisher-{version}.pyz'),
                entry['url'])
        require(re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), entry['sha256'])
        return {'version': entry['version'], 'tag': entry['tag']}
    check('manifest_version', manifest_version)

    def signed_manifest():
        raw = (ROOT / 'dev-tool' / 'manifest.json').read_bytes()
        envelope = json.loads(raw)
        payload = json.loads(base64.b64decode(envelope['payload'], validate=True))
        require(payload == source, '签名清单内容与 manifest.source.json 不一致')
        import ed25519_verify
        from feed_signing import PRODUCTION_PUBLIC_KEY
        verified = ed25519_verify.verify(PRODUCTION_PUBLIC_KEY,
                                          base64.b64decode(envelope['payload'], validate=True),
                                          base64.b64decode(envelope['signature'], validate=True))
        require(verified, '签名清单未通过官方公钥验签')
        return {'payloadMatches': True, 'signatureVerified': verified}
    check('signed_manifest', signed_manifest)

    def archive_version():
        archive = ROOT / 'dist' / 'dev-tool' / f'ordina-publisher-{version}.pyz'
        require(archive.is_file(), f'缺少生产工具包 {archive}')
        data = archive.read_bytes()
        entry = current_entry(entries, version)
        require(hashlib.sha256(data).hexdigest() == entry['sha256'],
                '工具包 SHA-256 与清单不一致')
        require(len(data) == entry['size'], '工具包大小与清单不一致')
        with zipfile.ZipFile(archive) as zipped:
            require(zipped.read('VERSION').decode().strip()
                    == f'ordina-publisher {version}', 'VERSION 文件版本不一致')
            require(f'TOOL_VERSION = {version!r}' in zipped.read('__main__.py').decode(),
                    '入口脚本版本不一致')
            names = set(zipped.namelist())
        require(not (names & LAB_MODULES), '生产工具含实验室模块')
        require(not any(name.startswith(('test_', 'verify_')) for name in names),
                '生产工具含测试模块')
        known = {item['asset']: item for item in entries}
        stray = []
        for path in (ROOT / 'dist' / 'dev-tool').glob('*.pyz'):
            if path.name not in known:
                stray.append(path.name)
            else:
                require(hashlib.sha256(path.read_bytes()).hexdigest() == known[path.name]['sha256'],
                        f'历史资产摘要不一致：{path.name}')
        require(not stray, '发布目录存在未知版本工具包：' + str(stray))
        return {'sha256': entry['sha256'][:16], 'size': len(data),
                'strayArchives': stray}
    check('archive_version', archive_version)

    def no_secret_material():
        archive = ROOT / 'dist' / 'dev-tool' / f'ordina-publisher-{version}.pyz'
        with zipfile.ZipFile(archive) as zipped:
            modules = [name for name in zipped.namelist() if name.endswith('.py')]
            for name in modules:
                text = zipped.read(name).decode('utf-8')
                for marker in ('BEGIN PRIVATE KEY', 'ghp_', 'gho_',
                               '-----BEGIN RSA', '-----BEGIN EC'):
                    require(marker not in text, f'{name} 含密钥材料 {marker}')
        return {'scannedModules': len(modules)}
    check('no_secret_material', no_secret_material)

    def release_commands():
        text = (ROOT / 'docs' / 'releases.md').read_text(encoding='utf-8')
        declared = set(re.findall(r'build_dev_tool\.py --version ([0-9.]+)', text))
        declared |= set(re.findall(r'dev-tool-v([0-9.]+)', text))
        declared |= set(re.findall(r'ordina-publisher-([0-9.]+)\.pyz', text))
        require(declared == {version},
                f'上线命令/标签中的版本不一致：{sorted(declared)} != {version}')
        return {'versions': sorted(declared)}
    check('release_commands', release_commands)

    def acceptance_reports():
        developer = json.loads((ROOT / 'docs' / 'validation' /
                                'developer-center.json').read_text())
        require(developer.get('candidateToolVersion') == version, developer)
        require(developer.get('productionSigningKeyUsed') is False,
                '验收报告声称使用了生产签名密钥')
        require(developer.get('trustChain') == 'isolated-lab-key', developer)
        require(developer.get('labToolVersions') == ['1.0.0', '1.1.0'], developer)
        standalone = ROOT / 'docs' / 'validation' / 'standalone-tool.json'
        if standalone.is_file():
            value = json.loads(standalone.read_text())
            require(value.get('toolVersion') == version, value)
            require(value.get('passed') is True, '独立部署验收未通过')
        return {'candidateToolVersion': version,
                'labToolVersions': developer['labToolVersions']}
    check('acceptance_reports', acceptance_reports)

    passed = all(value == 'pass' for value in checks.values())
    report = {
        'format': 1, 'passed': passed, 'productionToolVersion': None,
        'candidateToolVersion': version,
        'checks': checks, 'details': details,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    print(f'\n报告：{REPORT}')
    if not passed:
        sys.exit('工具版本一致性检查未通过：' + ', '.join(
            name for name, value in checks.items() if value != 'pass'))


if __name__ == '__main__':
    main()
