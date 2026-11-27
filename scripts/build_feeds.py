#!/usr/bin/env python3
"""Derive every published plugin feed from the single registry source.

Source (hand-maintained, unsigned):
    plugins/registry.source.json

Generated outputs (never hand-edited):
    registry.json                  signed remote registry, Pages root
    plugins/manifest.json         signed legacy combined feed (ordina <= 1.9.4)
    <plugin clone>/manifest.json  signed per-plugin feed (ordina >= 1.9.5)

Usage:
    python3 scripts/build_feeds.py --check --repo-path minutes=... --repo-path multicam=...
    python3 scripts/build_feeds.py --local --repo-path minutes=... --repo-path multicam=...
    python3 scripts/build_feeds.py --push  --repo-path minutes=... --repo-path multicam=...

--check compares the derived payload and signature against current files and
never writes. --local writes locally. --push publishes; it is intentionally a
separate, explicit step so a build or verification never touches the network.
"""
import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from feed_signing import envelope_bytes, verify_envelope
import plugin_protocol as protocol

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'plugins' / 'registry.source.json'
REGISTRY_OUTPUT = ROOT / 'registry.json'
LEGACY_OUTPUT = ROOT / 'plugins' / 'manifest.json'
REQUIRED = ('repository', 'feed', 'downloadPrefix', 'kind', 'asset',
            'version', 'sha256', 'size', 'name', 'summary', 'symbol',
            'minClientVersion', 'capabilities')


def load_source(path=None):
    # Resolved at call time: the packaging configure step rebinds these module
    # constants, and a default argument would freeze the pre-packaging path.
    document = json.loads(Path(path or SOURCE).read_text(encoding='utf-8'))
    if document.get('format') != 2 or not isinstance(document.get('plugins'), dict):
        raise ValueError('registry 源格式无效。')
    return document['plugins']


def published(plugins):
    return {name: meta for name, meta in sorted(plugins.items()) if meta.get('published')}


def entry(name, meta):
    for key in REQUIRED:
        if key not in meta:
            raise ValueError(f'{name} 缺少字段 {key}')
    tag = f'plugin-{name}-v{meta["version"]}'
    result = {
        'version': meta['version'],
        'url': (f'https://github.com/{meta["repository"]}/releases/download/'
                f'{tag}/{quote(meta["asset"])}'),
        'sha256': meta['sha256'],
    }
    if meta['kind'] == 'bundle':
        result['kind'] = 'bundle'
    return result


def registry_descriptor(name, meta):
    """Presentation fields the client needs before fetching a full manifest."""
    value = protocol.descriptor(name, meta)
    result = {
        'repository': meta['repository'],
        'feed': meta['feed'],
        'downloadPrefix': meta['downloadPrefix'],
        'kind': 'bundle' if meta['kind'] == 'bundle' else 'script',
        'name': value['name'],
        'summary': value['summary'],
        'symbol': value['symbol'],
        'version': value['version'],
        'minClientVersion': value['minClientVersion'],
        'protocol': value['protocol'],
        # Old clients reject the whole registry on an unknown capability.
        # The signed per-plugin descriptor retains the full capability set.
        'capabilities': [cap for cap in value['capabilities'] if cap != 'macos.application'] or ['resolve.support'],
    }
    if value.get('iconID'):
        result['iconID'] = value['iconID']
    return result


def manifest_entry(name, meta):
    """Signed per-plugin entry with the complete descriptor and file inventory."""
    value = entry(name, meta)
    value['size'] = meta['size']
    if meta['kind'] == 'bundle':
        files = meta.get('files') or []
    else:
        files = [{
            'path': meta['asset'],
            'sha256': meta['sha256'],
            'size': meta['size'],
            'role': 'utilityScript',
            'installName': meta.get('installName') or meta['asset'],
        }]
    value['descriptor'] = protocol.descriptor(name, meta, files=files)
    value['files'] = [dict(item) for item in files]
    protocol.validate_manifest_entry(name, value)
    return value


def documents(plugins):
    live = published(plugins)
    registry = {
        'format': 2,
        'plugins': {name: registry_descriptor(name, meta) for name, meta in live.items()},
    }
    # The existing `plugins` map remains published-only for older clients.
    # `everPublished` is historical metadata, not a second publication state.
    withdrawn = sorted(name for name, meta in plugins.items()
                       if not meta.get('published') and meta.get('everPublished') is True)
    if withdrawn:
        if any(not protocol.valid_id(name) for name in withdrawn):
            raise ValueError('已下架插件标识无效。')
        registry['withdrawn'] = withdrawn
    entries = {name: entry(name, meta) for name, meta in live.items()}
    # Freeze legacy entries at versions that exist in the original release
    # repository. Old clients reject per-plugin repository paths.
    legacy_entries = {}
    for name, meta in live.items():
        old = meta.get('legacy')
        if not old:
            continue
        legacy_meta = dict(meta, **old, repository='BrOKenAG/ordina-releases')
        legacy_entries[name] = entry(name, legacy_meta)
    legacy = {'format': 1, 'plugins': legacy_entries}
    per_plugin = {name: {'format': 1, 'plugins': {name: manifest_entry(name, live[name])}}
                  for name in live}
    for name in withdrawn:
        per_plugin[name] = {'format': 1, 'plugins': {}}
    return registry, legacy, per_plugin


def generate(plugins, tool=None):
    """Return {label: (document, signed envelope bytes)} for every derived feed."""
    registry, legacy, per_plugin = documents(plugins)
    outputs = {
        'registry': (registry, envelope_bytes(registry, tool)),
        'legacy': (legacy, envelope_bytes(legacy, tool)),
    }
    for name, document in per_plugin.items():
        outputs['plugin:' + name] = (document, envelope_bytes(document, tool))
        if name in legacy['plugins']:
            compatibility = {'format': 1, 'plugins': {name: legacy['plugins'][name]}}
            outputs['compat-plugin:' + name] = (compatibility, envelope_bytes(compatibility, tool))
        elif name in registry.get('withdrawn', []) and plugins[name].get('legacy'):
            compatibility = {'format': 1, 'plugins': {}}
            outputs['compat-plugin:' + name] = (compatibility, envelope_bytes(compatibility, tool))
    return outputs


def resolve_paths(args, generated):
    paths = {'registry': REGISTRY_OUTPUT, 'legacy': LEGACY_OUTPUT}
    for item in args.repo_path:
        name, separator, value = item.partition('=')
        if not separator or not name or not value:
            raise ValueError('--repo-path 需要 NAME=PATH 格式：' + item)
        paths['plugin:' + name] = Path(value).expanduser().resolve() / 'manifest-v2.json'
        paths['compat-plugin:' + name] = Path(value).expanduser().resolve() / 'manifest.json'
    missing = [key for key in generated if key not in paths]
    if missing:
        raise ValueError('缺少本地插件仓库路径（--repo-path）：' + ', '.join(missing))
    return paths


def check(generated, paths, tool=None):
    """Assert every current feed is exactly what the source derives."""
    failures = 0
    for label, (document, data) in sorted(generated.items()):
        path = paths[label]
        if not path.is_file():
            if label == 'registry':
                # The signed registry is a new artifact; its absence is expected
                # until the first --local / --push run.
                print(f'NEW   {label}: {path.name} 待生成')
            else:
                print(f'MISS  {label}: {path} 不存在')
                failures += 1
            continue
        expected = json.loads(data)
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
            verify_envelope(path.read_bytes(), tool)
            same = (current.get('payload') == expected.get('payload')
                    and current.get('signature') == expected.get('signature'))
            if same:
                print(f'OK    {label}: {path.name} 内容与签名均一致')
            else:
                print(f'DIFF  {label}: {path.name} 与源派生结果不一致')
                failures += 1
        except ValueError as error:
            print(f'FAIL  {label}: {error}')
            failures += 1
    return failures == 0


def write_local(generated, paths):
    for label, (document, data) in generated.items():
        path = paths[label]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f'写入 {path}')


def push(generated, paths, plugins):
    """Publish per-plugin feeds through the API and commit the main-repo feeds."""
    for label, (document, data) in generated.items():
        if not label.startswith('plugin:'):
            continue
        name = label.split(':', 1)[1]
        repository = plugins[name]['repository']
        existing = subprocess.run(
            ['gh', 'api', f'repos/{repository}/contents/manifest.json'],
            cwd=ROOT, capture_output=True, text=True)
        body = {
            'message': f'Publish signed feed for {name}',
            'content': base64.b64encode(data).decode('ascii'),
        }
        if existing.returncode == 0:
            body['sha'] = json.loads(existing.stdout)['sha']
        result = subprocess.run(
            ['gh', 'api', '-X', 'PUT', f'repos/{repository}/contents/manifest.json',
             '--input', '-'], cwd=ROOT, input=json.dumps(body),
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f'{repository} 清单推送失败：{result.stderr.strip()}')
        print(f'已推送 {repository}/manifest.json')
    for label in ('registry', 'legacy'):
        paths[label].write_bytes(generated[label][1])
    subprocess.run(['git', 'add', '--', 'registry.json', 'plugins/manifest.json'],
                   cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', 'Rebuild signed feeds from registry source'],
                   cwd=ROOT, check=True)
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, check=True)
    print('已推送 registry.json 与兼容清单')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='与现有清单比较，不写入')
    mode.add_argument('--local', action='store_true', help='仅写入本地文件')
    mode.add_argument('--push', action='store_true', help='发布到各仓库（显式步骤）')
    parser.add_argument('--repo-path', action='append', default=[], metavar='NAME=PATH',
                        help='插件本地克隆路径，可重复')
    parser.add_argument('--sign-tool', type=Path, help='覆盖签名工具路径（测试用）')
    args = parser.parse_args()
    tool = str(args.sign_tool) if args.sign_tool else None
    try:
        if args.push:
            raise ValueError('跨仓库直接推送已停用；请先完成分阶段发布与恢复机制验证。')
        plugins = load_source()
        generated = generate(plugins, tool)
        paths = resolve_paths(args, generated)
        if args.check:
            sys.exit(0 if check(generated, paths, tool) else 1)
        if args.local:
            write_local(generated, paths)
            return
        raise ValueError('跨仓库直接推送已停用；请先完成分阶段发布与恢复机制验证。')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'生成清单失败：{error}')


if __name__ == '__main__':
    main()
