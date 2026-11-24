#!/usr/bin/env python3
"""Check that every asset referenced by the published feeds is available.

Reads appcast.xml and plugins/manifest.json, verifies the feed signature,
downloads each referenced asset, and compares the recorded length or SHA-256.
A broken reference here means clients are advertised an update they cannot
download, which is what happens when a release is deleted by hand.

    python3 scripts/check_feeds.py            # local feeds, before publishing
    python3 scripts/check_feeds.py --remote   # what clients actually see
"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from sparkle_tools import KEYCHAIN_ACCOUNT, ensure_tools

ROOT = Path(__file__).resolve().parents[1]
PAGES = 'https://brokenag.github.io/ordina-releases'
# Every plugin publishes its own feed; the combined feed is kept only for
# ordina 1.9.4 and earlier.
PLUGINS = {
    'minutes': 'ordina-plugin-minutes',
    'multicam': 'ordina-plugin-multicam',
}
SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
CURL = '/usr/bin/curl'


def curl(url, dest, timeout):
    """Download url into dest and return the HTTP status code."""
    result = subprocess.run(
        [CURL, '-sSL', '--max-time', str(timeout), '-w', '%{http_code}', '-o', str(dest), url],
        capture_output=True, text=True)
    return result.stdout.strip() or '000'


def sign_tool():
    try:
        tool = Path(ensure_tools()) / 'sign_update'
        return tool if tool.is_file() else None
    except Exception:
        return None


def verify(tool, target, signature=None):
    """Return True/False, or None when the signing tool is unavailable."""
    if tool is None:
        return None
    args = [str(tool), '--account', KEYCHAIN_ACCOUNT, '--verify', str(target)]
    if signature:
        args.append(signature)
    return subprocess.run(args, capture_output=True, text=True).returncode == 0


def status(ok):
    return '跳过' if ok is None else ('OK  ' if ok else 'FAIL')


def check_appcast(path, tool, tmp, latest_only, timeout):
    rows = []
    rows.append(('appcast 签名', status(verify(tool, path)), ''))
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


def check_plugins(label, path, tool, tmp, timeout):
    rows = []
    envelope = json.loads(path.read_text(encoding='utf-8'))
    payload = tmp / f'{label}-payload.json'
    payload.write_bytes(base64.b64decode(envelope['payload'], validate=True))
    rows.append((f'{label} 签名', status(verify(tool, payload, envelope['signature'])), ''))
    document = json.loads(payload.read_bytes())
    for name, entry in sorted(document['plugins'].items()):
        label = f'插件 {name} v{entry["version"]}'
        target = tmp / f'plugin-{name}'
        code = curl(entry['url'], target, timeout)
        if code != '200':
            rows.append((label, 'FAIL', f'HTTP {code} {entry["url"]}'))
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            rows.append((label, 'FAIL', f'哈希 {digest[:12]}… 与清单不符'))
            continue
        rows.append((label, 'OK  ', f'{target.stat().st_size:,} 字节'))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--remote', action='store_true', help='从 GitHub Pages 读取清单，检查线上真实状态')
    parser.add_argument('--latest', action='store_true', help='只检查 appcast 最新一条')
    parser.add_argument('--timeout', type=int, default=300, help='单个下载超时秒数')
    args = parser.parse_args()

    tool = sign_tool()
    if tool is None:
        print('警告：找不到 sign_update，跳过签名校验\n')

    with tempfile.TemporaryDirectory(prefix='ordina-feed-check-') as temporary:
        tmp = Path(temporary)
        if args.remote:
            stamp = int(time.time())
            appcast = tmp / 'appcast.xml'
            plugins = tmp / 'manifest.json'
            for url, dest in ((f'{PAGES}/appcast.xml?t={stamp}', appcast),
                              (f'{PAGES}/plugins/manifest.json?t={stamp}', plugins)):
                code = curl(url, dest, args.timeout)
                if code != '200':
                    sys.exit(f'无法获取 {url}（HTTP {code}）')
            print(f'来源：{PAGES}\n')
        else:
            appcast = ROOT / 'appcast.xml'
            plugins = ROOT / 'plugins/manifest.json'
            print('来源：本地清单\n')

        rows = check_appcast(appcast, tool, tmp, args.latest, args.timeout)
        rows += check_plugins('兼容清单', plugins, tool, tmp, args.timeout)
        if args.remote:
            for name, repository in sorted(PLUGINS.items()):
                url = f'https://brokenag.github.io/{repository}/manifest.json?t={stamp}'
                dest = tmp / f'{name}-manifest.json'
                code = curl(url, dest, args.timeout)
                if code != '200':
                    rows.append((f'{name} 清单', 'FAIL', f'HTTP {code} {url}'))
                    continue
                rows += check_plugins(name, dest, tool, tmp, args.timeout)

    width = max(len(row[0]) for row in rows)
    failures = sum(1 for row in rows if row[1] == 'FAIL')
    for label, state, detail in rows:
        print(f'{label.ljust(width)}  {state}  {detail}'.rstrip())
    print()
    if failures:
        sys.exit(f'{failures} 项引用不可用')
    print(f'全部 {len(rows)} 项检查通过')


if __name__ == '__main__':
    main()
