#!/usr/bin/env python3
"""Publish one signed, independently updatable Resolve plugin.

Every plugin owns a release repository, so plugin versions never mix with app
releases or with each other. Single-file plugins are published as their .py
source; bundle plugins are published as a signed JSON container holding
base64-encoded files. The app's own installer performs the installation, so a
downloaded bundle never carries executable installer logic.

The same entry is also written to the legacy combined feed in this repository,
because ordina 1.9.4 and earlier read a single feed for all plugins.
"""
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
BUNDLES = {'minutes': 'Minutes Roughcut'}
REPOSITORIES = {
    'minutes': 'BrOKenAG/ordina-plugin-minutes',
    'multicam': 'BrOKenAG/ordina-plugin-multicam',
    'yueliu': 'BrOKenAG/ordina-plugin-yueliu',
}
ASSET_NAMES = {
    'minutes': 'minutes.json',
    'multicam': 'Multicam.Tools.py',
    'yueliu': 'Import.YueLiu.py',
}
# Combined feed for clients that predate per-plugin feeds. Remove once no
# supported client reads it.
LEGACY_FEED = ROOT / 'plugins/manifest.json'
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}$')
BUNDLE_REQUIRED = {
    'plugin.json', 'Minutes Roughcut.py', 'host.py', 'host.command',
    'extension/manifest.json',
}
BUNDLE_SKIP = {'__pycache__', '.DS_Store'}


def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or '命令失败：' + ' '.join(args))
    return result.stdout


def version(data):
    match = re.search(rb'(?m)^PLUGIN_VERSION\s*=\s*["\']v?([0-9]+(?:\.[0-9]+)+)["\']', data)
    if not match:
        raise ValueError('插件缺少有效的 PLUGIN_VERSION。')
    return match.group(1).decode('ascii')


def build_bundle(directory):
    """Return deterministic container bytes and the version from plugin.json."""
    meta = json.loads((directory / 'plugin.json').read_text(encoding='utf-8'))
    ver = meta.get('version')
    if not isinstance(ver, str) or not VERSION_RE.fullmatch(ver):
        raise ValueError('插件包版本无效。')
    if meta.get('id') != 'minutes':
        raise ValueError('插件包标识不符。')
    files = {}
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.suffix == '.pyc':
            continue
        if any(part in BUNDLE_SKIP for part in path.parts):
            continue
        relative = path.relative_to(directory).as_posix()
        if relative == 'install.py':
            # The installer ships inside the signed app, never in a download.
            continue
        parts = relative.split('/')
        if (not relative or relative.startswith('/') or '..' in parts or '\0' in relative
                or '\\' in relative or ':' in relative or len(parts) > 4
                or any(not part or part.startswith('.') for part in parts)):
            raise ValueError('插件包包含无效路径：' + relative)
        files[relative] = base64.b64encode(path.read_bytes()).decode('ascii')
    missing = BUNDLE_REQUIRED - set(files)
    if missing:
        raise ValueError('插件包缺少文件：' + ', '.join(sorted(missing)))
    if len(files) > 32:
        raise ValueError('插件包文件数量过多。')
    document = {'format': 1, 'id': 'minutes', 'version': ver, 'files': files}
    data = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    if len(data) > 4_000_000:
        raise ValueError('插件包体积过大。')
    return data, ver


def sign_document(document, sign, tmp):
    """Sign a feed payload and return its bytes plus the signature."""
    data = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    payload = tmp / 'feed-payload.json'
    payload.write_bytes(data)
    signature = run(sign, '--account', KEYCHAIN_ACCOUNT, '-p', str(payload)).strip()
    verify = subprocess.run([sign, '--account', KEYCHAIN_ACCOUNT, '--verify',
                             str(payload), signature], capture_output=True, text=True)
    if verify.returncode != 0:
        raise ValueError('插件清单签名校验失败。')
    return data, signature


def verified_payload(envelope, sign, tmp, label):
    """Decode a feed envelope after checking its signature."""
    payload = tmp / 'existing-payload.json'
    try:
        payload.write_bytes(base64.b64decode(envelope['payload'], validate=True))
    except (KeyError, ValueError):
        raise ValueError(f'{label}格式无效。')
    verify = subprocess.run([sign, '--account', KEYCHAIN_ACCOUNT, '--verify',
                             str(payload), envelope.get('signature', '')],
                            capture_output=True, text=True)
    if verify.returncode != 0:
        raise ValueError(f'{label}签名无效，拒绝覆盖。')
    document = json.loads(payload.read_bytes())
    if document.get('format') != 1 or not isinstance(document.get('plugins'), dict):
        raise ValueError(f'{label}格式无效。')
    return document


def remote_envelope(repository):
    """Return a plugin repository's feed envelope and blob sha, if it exists."""
    result = subprocess.run(['gh', 'api', f'repos/{repository}/contents/manifest.json'],
                            cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        return None, None
    content = json.loads(result.stdout)
    return json.loads(base64.b64decode(content['content'])), content['sha']


def put_remote(repository, sha, envelope, message):
    body = {
        'message': message,
        'content': base64.b64encode(
            json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode() + b'\n').decode(),
    }
    if sha:
        body['sha'] = sha
    result = subprocess.run(
        ['gh', 'api', '-X', 'PUT', f'repos/{repository}/contents/manifest.json', '--input', '-'],
        cwd=ROOT, text=True, input=json.dumps(body), capture_output=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or '更新插件清单失败。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('plugin', choices=sorted([*FILES, *BUNDLES]))
    args = parser.parse_args()
    is_bundle = args.plugin in BUNDLES
    display = BUNDLES[args.plugin] if is_bundle else FILES[args.plugin][:-3]
    source = ROOT / 'Integrations/OptionalPlugins' / (BUNDLES[args.plugin] if is_bundle else FILES[args.plugin])
    if is_bundle:
        data, ver = build_bundle(source)
    else:
        data = source.read_bytes()
        ver = version(data)
    repository = REPOSITORIES[args.plugin]
    asset_name = ASSET_NAMES[args.plugin]
    tag = f'plugin-{args.plugin}-v{ver}'
    if run('git', 'branch', '--show-current').strip() != 'main':
        raise ValueError('请在 main 分支发布。')
    if run('git', 'diff', '--cached', '--name-only').strip():
        raise ValueError('暂存区有其他改动，请先处理。')
    if LEGACY_FEED.exists() and run('git', 'status', '--porcelain', '--',
                                    str(LEGACY_FEED.relative_to(ROOT))).strip():
        raise ValueError('兼容清单有未提交的更改，请先处理。')
    # A remote change must never be overwritten or silently omitted.
    run('git', 'fetch', 'origin', 'main')
    if run('git', 'rev-parse', 'HEAD').strip() != run('git', 'rev-parse', 'origin/main').strip():
        raise ValueError('本地 main 与远端不一致，请先同步。')
    sign = str(ensure_tools() / 'sign_update')

    with tempfile.TemporaryDirectory(prefix='ordina-plugin-release-') as temporary:
        tmp = Path(temporary)
        asset = tmp / asset_name
        asset.write_bytes(data)

        # 1. The plugin's own repository is authoritative for its released version.
        envelope, blob_sha = remote_envelope(repository)
        previous_entries = {}
        if envelope:
            previous_entries = verified_payload(envelope, sign, tmp, '插件仓库清单')['plugins']
        elif LEGACY_FEED.exists():
            previous_entries = verified_payload(
                json.loads(LEGACY_FEED.read_text(encoding='utf-8')), sign, tmp, '兼容清单')['plugins']
        previous = previous_entries.get(args.plugin)
        if previous and tuple(map(int, ver.split('.'))) <= tuple(map(int, previous['version'].split('.'))):
            raise ValueError('插件版本必须高于已发布版本。')

        entry = {
            'version': ver,
            'url': f'https://github.com/{repository}/releases/download/{tag}/{quote(asset_name)}',
            'sha256': hashlib.sha256(data).hexdigest(),
        }
        if is_bundle:
            entry['kind'] = 'bundle'

        # 2. Publish the immutable asset before any feed references it.
        exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if exists:
            # A retry may reuse an already uploaded asset only when it is identical.
            downloaded = tmp / 'downloaded'
            downloaded.mkdir()
            run('gh', 'release', 'download', tag, '--repo', repository,
                '--pattern', asset_name, '--dir', str(downloaded))
            if (downloaded / asset_name).read_bytes() != data:
                raise ValueError('该版本已有不同的发布文件，请递增版本号。')
        else:
            run('gh', 'release', 'create', tag, str(asset), '--repo', repository,
                '--title', f'{display} v{ver}', '--notes', f'{display} v{ver}', '--latest=false')
        print(f'已上传 {repository} {tag}/{asset_name}')

        # 3. Point the plugin's own feed at it.
        plugin_bytes, plugin_signature = sign_document(
            {'format': 1, 'plugins': {args.plugin: entry}}, sign, tmp)
        put_remote(repository, blob_sha,
                   {'payload': base64.b64encode(plugin_bytes).decode(), 'signature': plugin_signature},
                   f'Publish {display} v{ver}')

        # 4. Mirror the entry into the legacy combined feed for older clients.
        combined = {'format': 1, 'plugins': {}}
        if LEGACY_FEED.exists():
            combined = verified_payload(json.loads(LEGACY_FEED.read_text(encoding='utf-8')),
                                        sign, tmp, '兼容清单')
        combined['plugins'][args.plugin] = entry
        combined_bytes, combined_signature = sign_document(combined, sign, tmp)
        LEGACY_FEED.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_FEED.write_text(json.dumps(
            {'payload': base64.b64encode(combined_bytes).decode(), 'signature': combined_signature},
            ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

        run('git', 'add', '--', str(source.relative_to(ROOT)), str(LEGACY_FEED.relative_to(ROOT)))
        run('git', 'commit', '-m', f'Publish {args.plugin} plugin v{ver}', '--',
            str(source.relative_to(ROOT)), str(LEGACY_FEED.relative_to(ROOT)))
        run('git', 'push', 'origin', 'main')
    print(f'已发布 {display} v{ver}（{"插件包" if is_bundle else "单脚本"}）到 {repository}，兼容清单已同步。')


if __name__ == '__main__':
    main()
