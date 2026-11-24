#!/usr/bin/env python3
"""Publish one plugin release as a verified transaction.

Order is fixed and enforced by release_transaction.execute():
    preflight -> create/upload Release -> GATE (GET 200 + size + SHA-256)
              -> write signed feeds -> push -> postflight

The registry source, the signed registry, the legacy combined feed and every
per-plugin feed are all derived from ONE metadata source by build_feeds.py.
None of them is maintained by hand.
"""
import argparse
import base64
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from build_feeds import entry, generate, load_source
from release_transaction import (ROOT, Asset, DryRunGateway, FeedTarget,
                                 LocalGateway, Plan, ReleaseError,
                                 describe_failure, execute)

SOURCE_PATH = ROOT / 'plugins' / 'registry.source.json'
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}$')
BUNDLE_REQUIRED = {
    'plugin.json', 'Minutes Roughcut.py', 'host.py', 'host.command',
    'extension/manifest.json',
}
BUNDLE_SKIP = {'__pycache__', '.DS_Store'}


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


def build_asset(meta):
    source = ROOT / 'Integrations' / 'OptionalPlugins' / meta['source']
    if meta['kind'] == 'bundle':
        data, ver = build_bundle(source)
    else:
        data = source.read_bytes()
        ver = version(data)
    return source, data, ver


def parse_repo_paths(items):
    result = {}
    for item in items:
        name, separator, value = item.partition('=')
        if not separator or not name or not value:
            raise ValueError('--repo-path 需要 NAME=PATH 格式：' + item)
        result[name] = Path(value).expanduser().resolve()
    return result


def main():
    plugins = load_source()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plugin', choices=sorted(plugins))
    parser.add_argument('--repo-path', action='append', default=[], metavar='NAME=PATH',
                        help='插件本地克隆路径，可重复；用于同步写入 manifest.json')
    parser.add_argument('--dry-run', action='store_true',
                        help='走完整流程但不产生任何网络或磁盘副作用')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()

    try:
        if not args.dry_run:
            raise ValueError('跨仓库插件发布暂不可用；需先完成部分失败恢复验证。')
        name = args.plugin
        meta = plugins[name]
        source, data, ver = build_asset(meta)
        previous = meta.get('version')
        if previous and tuple(map(int, ver.split('.'))) <= tuple(map(int, previous.split('.'))):
            if not args.dry_run:
                raise ValueError(f'插件版本 {ver} 必须高于已发布版本 {previous}。')
            print(f'预演：版本 {ver} 未递增，真实发布会在 preflight 前被拒绝', file=sys.stderr)

        # Compute the new source and every derived feed in memory. Nothing is
        # written to disk until the GATE has passed.
        document = json.loads(SOURCE_PATH.read_text(encoding='utf-8'))
        document['plugins'][name].update({
            'version': ver,
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
            'published': True,
        })
        source_bytes = (json.dumps(document, ensure_ascii=False, indent=2)
                        + '\n').encode('utf-8')
        generated = generate(document['plugins'])

        target = document['plugins'][name]
        asset_url = entry(name, target)['url']
        tag = f'plugin-{name}-v{ver}'
        display = meta['source']

        repo_paths = parse_repo_paths(args.repo_path)
        feeds = [
            FeedTarget('registry', generated['registry'][1], path=ROOT / 'registry.json'),
            FeedTarget('legacy', generated['legacy'][1],
                       path=ROOT / 'plugins' / 'manifest.json'),
            FeedTarget('source', source_bytes, path=SOURCE_PATH),
        ]
        for label, (doc, envelope_data) in generated.items():
            if not label.startswith('plugin:'):
                continue
            plugin_name = label.split(':', 1)[1]
            clone = repo_paths.get(plugin_name)
            feeds.append(FeedTarget(
                label, envelope_data,
                path=clone / 'manifest.json' if clone else None,
                repository=document['plugins'][plugin_name]['repository'],
            ))

        plan = Plan(
            label=f'{display} v{ver}',
            repository=target['repository'],
            tag=tag,
            title=f'{display} v{ver}',
            notes=f'{display} v{ver}',
            asset=Asset(target['asset'], data, asset_url),
            feeds=feeds,
            commit_paths=['registry.json', 'plugins/manifest.json',
                          'plugins/registry.source.json',
                          str(source.relative_to(ROOT))],
            commit_message=f'Publish {name} plugin v{ver}',
        )

        with tempfile.TemporaryDirectory(prefix='ordina-plugin-release-') as temporary:
            tmp = Path(temporary)
            gateway = (DryRunGateway(data) if args.dry_run else LocalGateway())
            result = execute(plan, gateway, tmp, args.timeout)

        if args.dry_run:
            print(f'预演通过：{plan.label} → {plan.repository}')
            print('阶段：' + ' → '.join(result['stages']))
            print('清单：' + ', '.join(f.label for f in feeds))
        else:
            print(f'已发布 {plan.label}（{result["asset"]}）到 {plan.repository}')
    except ReleaseError as error:
        sys.exit(describe_failure(error))
    except (ValueError, OSError) as error:
        sys.exit(f'发布失败：{error}')


if __name__ == '__main__':
    main()
