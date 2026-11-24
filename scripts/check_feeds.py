#!/usr/bin/env python3
"""Pre-publish and post-publish verification of every signed feed.

The plugin list, feed addresses and allowed download prefixes all come from the
signed registry.json -- the same source the client uses. Nothing is hardcoded
per plugin here.

    python3 scripts/check_feeds.py --preflight   # local feeds, before publishing
    python3 scripts/check_feeds.py              # local feeds + referenced assets
    python3 scripts/check_feeds.py --remote     # what clients actually see

--preflight never downloads assets (they may not be uploaded yet); it verifies
signatures, registry structure, and every URL against the client-side rules.
"""
import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from sparkle_tools import KEYCHAIN_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
PAGES = 'https://brokenag.github.io/ordina-releases'
SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
CURL = '/usr/bin/curl'
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
ID_RE = re.compile(r'^[a-z][a-z0-9-]{0,31}$')


def curl(url, dest, timeout):
    result = subprocess.run(
        [CURL, '-sSL', '--max-time', str(timeout), '-w', '%{http_code}', '-o', str(dest), url],
        capture_output=True, text=True)
    return result.stdout.strip() or '000'


def sign_tool():
    try:
        from sparkle_tools import ensure_tools
        tool = Path(ensure_tools()) / 'sign_update'
        return tool if tool.is_file() else None
    except Exception:
        return None


def verify(tool, target, signature=None):
    if tool is None:
        return None
    args = [str(tool), '--account', KEYCHAIN_ACCOUNT, '--verify', str(target)]
    if signature:
        args.append(signature)
    return subprocess.run(args, capture_output=True, text=True).returncode == 0


def status(ok):
    return '跳过' if ok is None else ('OK  ' if ok else 'FAIL')


def valid_prefix(value):
    return (isinstance(value, str) and value.startswith('/BrOKenAG/')
            and value.endswith('/releases/download/')
            and '..' not in value and '//' not in value)


def valid_feed(value):
    try:
        u = urlparse(value)
        return (u.scheme == 'https' and u.hostname == 'brokenag.github.io'
                and u.port is None and not u.username and not u.password
                and not u.query and not u.fragment
                and u.path.startswith('/ordina-plugin-')
                and u.path.endswith('/manifest.json'))
    except ValueError:
        return False


def entry_url_error(entry, prefix):
    """Mirror PluginUpdates.verifiedEntry's URL rules."""
    if not valid_prefix(prefix):
        return 'registry 下发的下载地址前缀不合法'
    u = urlparse(entry.get('url', ''))
    if (u.scheme != 'https' or u.hostname != 'github.com' or u.port is not None
            or u.username or u.password or u.query or u.fragment):
        return '资产地址不是 github.com 的 HTTPS 直链'
    if not u.path.startswith(prefix):
        return '资产地址不在 registry 允许的前缀内'
    extension = 'json' if entry.get('kind') == 'bundle' else 'py'
    if not u.path.endswith('.' + extension):
        return f'资产扩展名应为 .{extension}'
    if not VERSION_RE.fullmatch(entry.get('version', '')):
        return '版本号格式无效'
    if not SHA_RE.fullmatch(entry.get('sha256', '')):
        return 'SHA-256 格式无效'
    return None


def check_envelope(label, raw, tool, tmp, index):
    """Return (rows, document) after verifying the envelope signature."""
    try:
        obj = json.loads(raw)
        payload = tmp / f'{index}-payload.json'
        payload.write_bytes(base64.b64decode(obj['payload'], validate=True))
        ok = verify(tool, payload, obj.get('signature', ''))
        rows = [(f'{label} 签名', status(ok), '' if ok else '签名无效')]
        if ok is not True:
            return rows, None
        return rows, json.loads(payload.read_bytes())
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        return [(f'{label} 格式', 'FAIL', str(error))], None


def check_registry(label, raw, tool, tmp, index):
    rows, document = check_envelope(label, raw, tool, tmp, index)
    if document is None:
        return rows, {}
    if document.get('format') != 2:
        rows.append((f'{label} 格式', 'FAIL', 'format 应为 2'))
        return rows, {}
    descriptors = {}
    for name, descriptor in sorted(document.get('plugins', {}).items()):
        problems = []
        if not ID_RE.fullmatch(name):
            problems.append('插件标识不合法')
        if not valid_feed(descriptor.get('feed', '')):
            problems.append('feed 地址不合法')
        if not valid_prefix(descriptor.get('downloadPrefix', '')):
            problems.append('downloadPrefix 不合法')
        if not str(descriptor.get('repository', '')).startswith('BrOKenAG/ordina-plugin-'):
            problems.append('仓库不属于本项目')
        if descriptor.get('kind') not in (None, 'bundle', 'script'):
            problems.append('kind 不合法')
        if problems:
            rows.append((f'{label} {name}', 'FAIL', '；'.join(problems)))
        else:
            descriptors[name] = descriptor
            rows.append((f'{label} {name}', 'OK  ', descriptor['feed']))
    return rows, descriptors


def check_plugins(label, raw, descriptors, tool, tmp, timeout, download_assets):
    rows, document = check_envelope(label, raw, tool, tmp, label)
    if document is None:
        return rows
    if document.get('format') != 1:
        rows.append((f'{label} 格式', 'FAIL', 'format 应为 1'))
        return rows
    for name, entry in sorted(document.get('plugins', {}).items()):
        descriptor = descriptors.get(name)
        if descriptor is None:
            rows.append((f'插件 {name}', 'FAIL', 'registry 中未登记'))
            continue
        prefix = '/BrOKenAG/ordina-releases/releases/download/' if label == '兼容清单' else descriptor['downloadPrefix']
        problem = entry_url_error(entry, prefix)
        if problem:
            rows.append((f'插件 {name} v{entry.get("version", "?")}', 'FAIL', problem))
            continue
        if not download_assets:
            rows.append((f'插件 {name} v{entry["version"]}', 'OK  ', '地址规则通过（预演模式）'))
            continue
        target = tmp / f'asset-{label}-{name}'
        code = curl(entry['url'], target, timeout)
        if code != '200':
            rows.append((f'插件 {name} v{entry["version"]}', 'FAIL', f'HTTP {code}'))
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            rows.append((f'插件 {name} v{entry["version"]}', 'FAIL', 'SHA-256 不匹配'))
            continue
        rows.append((f'插件 {name} v{entry["version"]}', 'OK  ', f'{target.stat().st_size:,} 字节'))
    return rows


def check_appcast(path, tool, tmp, latest_only, timeout, download_assets):
    rows = [('appcast 签名', status(verify(tool, path)), '')]
    items = ET.parse(path).findall('./channel/item')
    if latest_only:
        items = items[:1]
    for item in items:
        version = item.findtext(SPARKLE + 'version') or '?'
        short = item.findtext(SPARKLE + 'shortVersionString') or ''
        label = f'appcast {version} {short}'.strip()
        enclosure = item.find('enclosure')
        if enclosure is None:
            rows.append((label, 'FAIL', '缺少 enclosure'))
            continue
        url = enclosure.get('url', '')
        if not url.startswith('https://github.com/BrOKenAG/ordina-releases/releases/download/'):
            rows.append((label, 'FAIL', '下载地址不在项目 Release 下'))
            continue
        if not download_assets:
            rows.append((label, 'OK  ', '地址规则通过（预演模式）'))
            continue
        target = tmp / f'ordina-{version}.dmg'
        code = curl(url, target, timeout)
        if code != '200':
            rows.append((label, 'FAIL', f'HTTP {code} {url}'))
            continue
        size = target.stat().st_size
        expected = enclosure.get('length')
        if expected and str(size) != expected:
            rows.append((label, 'FAIL', f'大小 {size:,} 与清单 {int(expected):,} 不符'))
            continue
        ok = verify(tool, target, enclosure.get(SPARKLE + 'edSignature'))
        rows.append((label, status(ok), f'{size:,} 字节' if ok is not False else '签名不匹配'))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--remote', action='store_true', help='从 GitHub Pages 读取线上清单')
    parser.add_argument('--preflight', action='store_true', help='发布前本地检查，不下载资产')
    parser.add_argument('--latest', action='store_true', help='只检查 appcast 最新一条')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--repo-path', action='append', default=[], metavar='NAME=PATH',
                        help='本地插件克隆路径，preflight 时一并检查')
    args = parser.parse_args()
    download_assets = not args.preflight
    tool = sign_tool()
    if tool is None:
        sys.exit('找不到签名验证工具，检查中止。')

    with tempfile.TemporaryDirectory(prefix='ordina-feed-check-') as temporary:
        tmp = Path(temporary)
        stamp = int(time.time())
        if args.remote:
            files = {}
            for label, name, url in (
                ('appcast', 'appcast.xml', f'{PAGES}/appcast.xml'),
                ('registry', 'registry.json', f'{PAGES}/registry.json'),
                ('兼容清单', 'plugins.json', f'{PAGES}/plugins/manifest.json'),
            ):
                dest = tmp / name
                code = curl(f'{url}?t={stamp}', dest, args.timeout)
                if code != '200':
                    sys.exit(f'无法获取 {url}（HTTP {code}）')
                files[label] = dest.read_bytes()
            print(f'来源：{PAGES}（线上）\n')
        else:
            paths = {
                'appcast': ROOT / 'appcast.xml',
                'registry': ROOT / 'registry.json',
                '兼容清单': ROOT / 'plugins' / 'manifest.json',
            }
            for label, path in paths.items():
                if not path.is_file():
                    sys.exit(f'缺少 {path}；先运行 python3 scripts/build_feeds.py --local')
            files = {label: path.read_bytes() for label, path in paths.items()}
            print('来源：本地清单' + ('（预演，不下载资产）\n' if args.preflight else '\n'))

        appcast_path = tmp / 'appcast.xml'
        appcast_path.write_bytes(files['appcast'])
        rows = check_appcast(appcast_path, tool, tmp, args.latest,
                             args.timeout, download_assets)
        registry_rows, descriptors = check_registry('registry', files['registry'], tool, tmp, 'registry')
        rows += registry_rows
        rows += check_plugins('兼容清单', files['兼容清单'], descriptors, tool, tmp,
                              args.timeout, download_assets)

        for name, descriptor in sorted(descriptors.items()):
            if args.remote:
                url = descriptor['feed'] + f'?t={stamp}'
                dest = tmp / f'{name}-manifest.json'
                code = curl(url, dest, args.timeout)
                if code != '200':
                    rows.append((f'{name} 清单', 'FAIL', f'HTTP {code} {url}'))
                    continue
                raw = dest.read_bytes()
            else:
                path = None
                for item in args.repo_path:
                    key, _, value = item.partition('=')
                    if key == name:
                        path = Path(value).expanduser().resolve() / 'manifest.json'
                if path is None or not path.is_file():
                    if args.preflight:
                        continue  # optional in preflight; --remote always covers it
                    rows.append((f'{name} 清单', 'FAIL', '缺少 --repo-path'))
                    continue
                raw = path.read_bytes()
            rows += check_plugins(name, raw, descriptors, tool, tmp, args.timeout, download_assets)

    width = max(len(row[0]) for row in rows)
    failures = sum(1 for row in rows if row[1] == 'FAIL')
    for label, state, detail in rows:
        print(f'{label.ljust(width)}  {state}  {detail}'.rstrip())
    print()
    if failures:
        sys.exit(f'{failures} 项检查未通过')
    print(f'全部 {len(rows)} 项检查通过')


if __name__ == '__main__':
    main()
