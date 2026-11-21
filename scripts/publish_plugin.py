#!/usr/bin/env python3
"""Publish one signed, independently updatable Resolve plugin."""
import argparse
import base64
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from sparkle_tools import ensure_tools, KEYCHAIN_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
FILES = {'multicam': 'Multicam Tools.py', 'yueliu': 'Import YueLiu.py'}
FEED = ROOT / 'plugins/manifest.json'
REPOSITORY = 'BrOKenAG/ordina-releases'


def run(*args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def version(data):
    match = re.search(rb'(?m)^PLUGIN_VERSION\s*=\s*["\']v?([0-9]+(?:\.[0-9]+)+)["\']', data)
    if not match:
        raise ValueError('插件缺少有效的 PLUGIN_VERSION。')
    return match.group(1).decode('ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('plugin', choices=FILES)
    args = parser.parse_args()
    filename = FILES[args.plugin]
    source = ROOT / 'Integrations/OptionalPlugins' / filename
    asset_name = args.plugin + '.py'
    data = source.read_bytes()
    ver = version(data)
    tag = f'plugin-{args.plugin}-v{ver}'
    if run('git', 'branch', '--show-current', capture=True).strip() != 'main':
        raise ValueError('请在 main 分支发布。')
    if run('git', 'diff', '--cached', '--name-only', capture=True).strip():
        raise ValueError('暂存区有其他改动，请先处理。')
    if FEED.exists() and run('git', 'status', '--porcelain', '--', str(FEED.relative_to(ROOT)), capture=True).strip():
        raise ValueError('插件清单有未提交的更改，请先处理。')
    # A remote change must never be overwritten or silently omitted.
    run('git', 'fetch', 'origin', 'main')
    local = run('git', 'rev-parse', 'HEAD', capture=True).strip()
    remote = run('git', 'rev-parse', 'origin/main', capture=True).strip()
    if local != remote:
        raise ValueError('本地 main 与远端不一致，请先同步。')
    sign = str(ensure_tools() / 'sign_update')
    with tempfile.TemporaryDirectory(prefix='ordina-plugin-release-') as temporary:
        tmp = Path(temporary)
        payload_path = tmp / 'payload.json'
        asset = tmp / asset_name
        asset.write_bytes(data)
        document = {'format': 1, 'plugins': {}}
        if FEED.exists():
            envelope = json.loads(FEED.read_text(encoding='utf-8'))
            payload_path.write_bytes(base64.b64decode(envelope['payload'], validate=True))
            run(sign, '--account', KEYCHAIN_ACCOUNT, '--verify', str(payload_path), envelope['signature'])
            document = json.loads(payload_path.read_bytes())
            if document.get('format') != 1 or not isinstance(document.get('plugins'), dict):
                raise ValueError('现有插件清单格式无效。')
        previous = document['plugins'].get(args.plugin)
        if previous and tuple(map(int, ver.split('.'))) <= tuple(map(int, previous['version'].split('.'))):
            raise ValueError('插件版本必须高于已发布版本。')
        document['plugins'][args.plugin] = {
            'version': ver,
            'url': f'https://github.com/{REPOSITORY}/releases/download/{tag}/{quote(asset_name)}',
            'sha256': hashlib.sha256(data).hexdigest(),
        }
        payload_path.write_bytes(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        signature = run(sign, '--account', KEYCHAIN_ACCOUNT, '-p', str(payload_path), capture=True).strip()
        run(sign, '--account', KEYCHAIN_ACCOUNT, '--verify', str(payload_path), signature)
        envelope = {'payload': base64.b64encode(payload_path.read_bytes()).decode(), 'signature': signature}
        # Publish immutable asset before publishing the feed that references it.
        exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPOSITORY], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if exists:
            # A retry may reuse an already uploaded asset only when it is identical.
            downloaded = tmp / 'downloaded'
            downloaded.mkdir()
            run('gh', 'release', 'download', tag, '--repo', REPOSITORY,
                '--pattern', asset_name, '--dir', str(downloaded))
            if (downloaded / asset_name).read_bytes() != data:
                raise ValueError('该版本已有不同的发布文件，请递增版本号。')
        else:
            run('gh', 'release', 'create', tag, str(asset), '--repo', REPOSITORY,
                '--title', f'{filename[:-3]} v{ver}', '--notes', f'{filename[:-3]} v{ver}', '--latest=false')
        FEED.parent.mkdir(parents=True, exist_ok=True)
        FEED.write_text(json.dumps(envelope, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        run('git', 'add', '--', str(source.relative_to(ROOT)), str(FEED.relative_to(ROOT)))
        run('git', 'commit', '-m', f'Publish {args.plugin} plugin v{ver}', '--', str(source.relative_to(ROOT)), str(FEED.relative_to(ROOT)))
        run('git', 'push', 'origin', 'main')
    print(f'已发布 {filename} v{ver}。')


if __name__ == '__main__':
    main()
