#!/usr/bin/env python3
"""Publish one plugin release as a verified, resumable transaction.

Order is fixed and enforced by the durable release driver:

    preflight -> create/upload Release -> GATE (GET 200 + size + SHA-256)
              -> durable cross-repository feed transaction -> postflight

The registry source, the signed registry, the legacy combined feed and every
per-plugin feed are all derived from ONE metadata source by build_feeds.py.
None of them is maintained by hand. Resume and rollback are handled by
scripts/durable_journal.py.

A plugin release is no longer tied to a plugin id the client knows. The bundle
is generated from ``plugin.json`` (descriptor + file roles) and the GATE writes
the exact per-file inventory into the signed manifest, so a brand-new plugin
can be published by updating the registry alone.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import durable_release
import plugin_protocol as protocol
from build_feeds import entry, generate, load_source
from release_transaction import (ROOT, Asset, DryRunGateway, FeedTarget,
                                 LocalGateway, Plan, ReleaseError)

SOURCE_PATH = ROOT / 'plugins' / 'registry.source.json'
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}$')
BUNDLE_SKIP = {'__pycache__', '.DS_Store'}
# Never download or install the release helper itself.
BUNDLE_EXCLUDE = {'install.py'}


def version(data):
    match = re.search(rb'(?m)^PLUGIN_VERSION\s*=\s*["\']v?([0-9]+(?:\.[0-9]+)+)["\']', data)
    if not match:
        raise ValueError('插件缺少有效的 PLUGIN_VERSION。')
    return match.group(1).decode('ascii')


def plugin_meta(directory):
    path = directory / 'plugin.json'
    if not path.is_file():
        raise ValueError('插件包缺少 plugin.json。')
    meta = json.loads(path.read_text(encoding='utf-8'))
    if not protocol.valid_id(meta.get('id')):
        raise ValueError('插件包标识无效。')
    if not protocol.valid_version(meta.get('version')):
        raise ValueError('插件包版本无效。')
    protocol.validate_descriptor(meta['id'], protocol.descriptor(
        meta['id'], meta, version=meta['version']))
    return meta


def declared_roles(meta):
    value = meta.get('files')
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('plugin.json 的 files 必须是对象。')
    result = {}
    for path, item in value.items():
        if not protocol.safe_relative(path):
            raise ValueError('插件包包含无效路径：' + str(path))
        if not isinstance(item, dict) or item.get('role') not in protocol.ROLES:
            raise ValueError('文件角色无效：' + str(path))
        result[path] = item
    return result


def verify_native_bundle(directory, inventory):
    """Verify an inventoried app in a disposable reconstruction before publishing."""
    native = [item for item in inventory if item['role'] == 'application']
    if not native:
        return
    root = native[0]['path'].split('/')[0]
    with tempfile.TemporaryDirectory(prefix='ordina-native-check-') as temporary:
        stage = Path(temporary)
        for item in native:
            source = directory / item['path']
            if source.is_symlink() or not source.is_file():
                raise ValueError('原生 App 含有不支持的文件或链接。')
            data = source.read_bytes()
            if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('原生 App 资源摘要不匹配。')
            target = stage / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o700 if item.get('executable') else 0o600)
        application = stage / root
        def verify(command):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError('无法验证原生 App 签名或公证。') from error
            if result.returncode:
                raise ValueError('原生 App 签名或公证无效。')
            return result.stdout + result.stderr
        verify(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(application)])


def build_bundle(directory, expected_id=None):
    """Return deterministic container bytes, version and the verified inventory.

    Roles come from ``plugin.json``. Files inside ``extension/`` default to the
    extension role; every other non-metadata file must be declared explicitly,
    so a new file cannot silently escape the inventory.
    """
    directory = Path(directory)
    meta = plugin_meta(directory)
    plugin_id = meta['id']
    if expected_id is not None and plugin_id != expected_id:
        raise ValueError(f'插件包标识 {plugin_id} 与请求的 {expected_id} 不符。')
    ver = meta['version']
    declared = declared_roles(meta)
    files = {}
    inventory = []
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.suffix == '.pyc':
            continue
        if any(part in BUNDLE_SKIP for part in path.parts):
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in BUNDLE_EXCLUDE:
            continue
        if not protocol.safe_relative(relative):
            raise ValueError('插件包包含无效路径：' + relative)
        data = path.read_bytes()
        if relative == 'plugin.json':
            role, options = 'metadata', {}
        elif relative in declared:
            role, options = declared[relative]['role'], declared[relative]
        elif relative.startswith('extension/'):
            role, options = 'extension', {}
        else:
            raise ValueError('plugin.json 未声明文件角色：' + relative)
        files[relative] = base64.b64encode(data).decode('ascii')
        item = {
            'path': relative,
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
            'role': role,
        }
        if role in ('utilityScript', 'nativeMessagingManifest') and options.get('installName'):
            item['installName'] = options['installName']
        if role == 'utilityScript' and 'installName' not in item:
            item['installName'] = Path(relative).name
        if options.get('executable'):
            item['executable'] = True
        inventory.append(item)
    if len(files) > 512:
        raise ValueError('插件包文件数量过多。')
    protocol.validate_files(plugin_id, 'bundle', inventory)
    if ('macos.application' in meta.get('capabilities', [])) != any(item['role'] == 'application' for item in inventory):
        raise ValueError('原生能力与文件清单不一致。')
    verify_native_bundle(directory, inventory)
    document = {'format': 1, 'id': plugin_id, 'version': ver, 'files': files}
    data = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    limit = 96_000_000 if 'macos.application' in meta.get('capabilities', []) else 4_000_000
    if len(data) > limit:
        raise ValueError('插件包体积过大。')
    return data, ver, inventory


def build_asset(meta, source=None, plugin_id=None):
    """Return (source path, asset bytes, version, file inventory)."""
    root = Path(source) if source else ROOT / 'Integrations' / 'OptionalPlugins' / meta['source']
    if meta['kind'] == 'bundle':
        if not (root / 'plugin.json').is_file():
            raise ValueError('插件源目录缺少 plugin.json：' + str(root))
        data, ver, files = build_bundle(root, expected_id=plugin_id or meta.get('id'))
    else:
        path = root if root.is_file() else root / meta['source']
        data = path.read_bytes()
        ver = version(data)
        files = [{
            'path': meta['asset'],
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
            'role': 'utilityScript',
            'installName': meta.get('installName') or meta['asset'],
        }]
    return (root if root.is_dir() else root), data, ver, files


def register_plugin(plugin_id, source):
    """Build a registry-source entry for a plugin the client has never seen."""
    directory = Path(source)
    meta = plugin_meta(directory)
    if meta['id'] != plugin_id:
        raise ValueError('plugin.json 的 id 与命令行参数不一致。')
    return {
        'repository': f'BrOKenAG/ordina-plugin-{plugin_id}',
        'feed': f'https://brokenag.github.io/ordina-plugin-{plugin_id}/manifest-v2.json',
        'downloadPrefix': f'/BrOKenAG/ordina-plugin-{plugin_id}/releases/download/',
        'kind': 'bundle',
        'asset': f'{plugin_id}.json',
        'source': directory.name,
        'published': True,
        'sha256': '0' * 64,
        'size': 1,
        'name': meta.get('name'),
        'summary': meta.get('summary'),
        'symbol': meta.get('symbol'),
        'minClientVersion': meta.get('minClientVersion'),
        'protocol': meta.get('protocol', protocol.PROTOCOL),
        'capabilities': meta.get('capabilities'),
        'supportDirectoryName': meta.get('supportDirectoryName'),
        'nativeMessagingHost': meta.get('nativeMessagingHost'),
        'legacyNames': meta.get('legacyNames'),
    }


def parse_repo_paths(items):
    result = {}
    for item in items:
        name, separator, value = item.partition('=')
        if not separator or not name or not value:
            raise ValueError('--repo-path 需要 NAME=PATH 格式：' + item)
        result[name] = Path(value).expanduser().resolve()
    return result


@dataclass(frozen=True)
class PreparedRelease:
    """Everything one release needs, derived from a single source directory."""
    name: str
    version: str
    data: bytes
    files: list
    plugins: dict
    document: dict
    generated: dict
    target: dict
    plan: Plan
    source: Path
    is_new: bool
    previous_version: Optional[str]


def prepare_release(name, source_path, notes=None, plugins=None, document=None,
                    repo_paths=None, require_version_increase=True):
    """Build the complete release plan without touching any remote.

    Shared by the command-line publisher and the developer publishing engine so
    a ZIP release goes through exactly the same asset build, feed generation
    and plan construction. Only reads the source directory and the registry
    source file; writes nothing.
    """
    plugins = plugins if plugins is not None else load_source()
    is_new = name not in plugins
    if is_new:
        if source_path is None:
            raise ValueError(f'未登记的插件 {name} 需要提供插件源目录。')
        plugins[name] = register_plugin(name, source_path)
    meta = copy.deepcopy(plugins[name])
    # ZIP metadata may evolve on update, but repository, URLs and asset names
    # remain controlled by the existing official registry entry.
    updated_fields = {}
    if not is_new and source_path is not None and meta.get('kind') == 'bundle':
        package = plugin_meta(Path(source_path))
        if package['id'] != name:
            raise ValueError('插件包标识与已有插件不一致。')
        for field in ('name', 'summary', 'symbol', 'minClientVersion', 'protocol',
                      'capabilities', 'supportDirectoryName', 'nativeMessagingHost', 'legacyNames'):
            if field in package:
                updated_fields[field] = package[field]
        meta.update(updated_fields)
    source, data, ver, files = build_asset(meta, source_path, name)
    if meta.get('protocol', protocol.PROTOCOL) != protocol.PROTOCOL:
        raise ValueError('插件协议版本不受支持。')
    previous = meta.get('version') or None
    if previous and tuple(map(int, ver.split('.'))) <= tuple(map(int, previous.split('.'))):
        message = f'插件版本 {ver} 必须高于已发布版本 {previous}。'
        if require_version_increase:
            raise ValueError(message)
        print(f'预演：{message}真实发布会在此被拒绝', file=sys.stderr)
    protocol.validate_descriptor(name, protocol.descriptor(name, meta, version=ver))
    protocol.validate_files(name, meta['kind'], files)

    document = copy.deepcopy(document) if document is not None else json.loads(
        SOURCE_PATH.read_text(encoding='utf-8'))
    if name not in document['plugins']:
        document['plugins'][name] = copy.deepcopy(plugins[name])
    document['plugins'][name].update(updated_fields)
    document['plugins'][name].update({
        'version': ver,
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
        'files': files,
        'published': True,
    })
    source_bytes = (json.dumps(document, ensure_ascii=False, indent=2)
                    + '\n').encode('utf-8')
    generated = generate(document['plugins'])

    target = document['plugins'][name]
    asset_url = entry(name, target)['url']
    tag = f'plugin-{name}-v{ver}'
    display = target.get('name') or name
    repo_paths = repo_paths or {}
    feeds = [
        FeedTarget('registry', generated['registry'][1], path=ROOT / 'registry.json'),
        FeedTarget('legacy', generated['legacy'][1],
                   path=ROOT / 'plugins' / 'manifest.json'),
        FeedTarget('source', source_bytes, path=SOURCE_PATH),
    ]
    # Each plugin repository keeps two feeds: the new format and the frozen
    # compatibility feed older clients still read.
    for label, (doc, envelope_data) in generated.items():
        if label.startswith('plugin:'):
            plugin_name = label.split(':', 1)[1]
            remote = 'manifest-v2.json'
        elif label.startswith('compat-plugin:'):
            plugin_name = label.split(':', 1)[1]
            remote = 'manifest.json'
        else:
            continue
        clone = repo_paths.get(plugin_name)
        feeds.append(FeedTarget(
            label, envelope_data,
            path=clone / remote if clone else None,
            repository=document['plugins'][plugin_name]['repository'],
            remote_path=remote,
        ))
    plan = Plan(
        label=f'{display} v{ver}',
        repository=target['repository'],
        tag=tag,
        title=f'{display} v{ver}',
        notes=notes or f'{display} v{ver}',
        asset=Asset(target['asset'], data, asset_url),
        feeds=feeds,
        commit_paths=['registry.json', 'plugins/manifest.json',
                      'plugins/registry.source.json',
                      str(source.relative_to(ROOT)) if source.is_relative_to(ROOT)
                      else 'plugins/registry.source.json'],
        commit_message=f'Publish {name} plugin v{ver}',
    )
    return PreparedRelease(name=name, version=ver, data=data, files=files,
                           plugins=plugins, document=document, generated=generated,
                           target=target, plan=plan, source=source, is_new=is_new,
                           previous_version=previous)


def main():
    plugins = load_source()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plugin', help='插件标识（未登记时需同时提供 --source）')
    parser.add_argument('--source', type=Path,
                        help='插件源目录或脚本；用于未登记的新插件')
    parser.add_argument('--repo-path', action='append', default=[], metavar='NAME=PATH',
                        help='插件本地克隆路径，可重复；仅用于预演时生成本地副本')
    parser.add_argument('--dry-run', action='store_true',
                        help='走完整流程但不产生任何网络或磁盘副作用')
    parser.add_argument('--execute', action='store_true', help='执行真实持久化发布事务')
    parser.add_argument('--notes', help='Release 更新说明；默认使用“名称 版本”')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--journal-dir', type=Path,
                        default=durable_release.TRANSACTIONS)
    parser.add_argument('--lab-report', type=Path,
                        default=durable_release.LAB_REPORT,
                        help='隔离仓库端到端验证报告')
    args = parser.parse_args()

    try:
        if not args.dry_run and not args.execute:
            raise ValueError('正式发布需显式 --execute；预演请使用 --dry-run。')
        if args.execute and not durable_release.lab_report_valid(args.lab_report):
            raise ValueError('缺少有效的隔离仓库验证报告（--lab-report），已停止发布。')
        name = args.plugin
        prepared = prepare_release(
            name, args.source, notes=args.notes, plugins=plugins,
            repo_paths=parse_repo_paths(args.repo_path),
            require_version_increase=not args.dry_run)
        plan = prepared.plan
        data, files, feeds, target = (prepared.data, prepared.files,
                                      plan.feeds, prepared.target)

        with tempfile.TemporaryDirectory(prefix='ordina-plugin-release-') as temporary:
            tmp = Path(temporary)
            journal_dir = tmp / 'journals' if args.dry_run else args.journal_dir
            staged = durable_release.prepare(
                plan, f'plugin-{name}-v{prepared.version}', journal_dir=journal_dir)
            if args.dry_run:
                backend = durable_release.DryRunBackend(staged.plan['feeds'])
                gateway = DryRunGateway(data)
            else:
                backend = durable_release.production_backend(staged, plan)
                gateway = LocalGateway()
            result = durable_release.execute(plan, gateway, backend,
                                             staged, tmp, args.timeout)
            if args.dry_run:
                print(f'预演通过：{plan.label} → {plan.repository}')
                print('阶段：' + ' → '.join(result['stages']))
                print('清单：' + ', '.join(f.label for f in feeds))
                print(f'文件：{len(files)} 项，能力：{", ".join(target["capabilities"])}')
                return
            print(f'已发布 {plan.label}（{result["asset"]}）到 {plan.repository}')
            print('事务记录：' + result['journal'])
    except ReleaseError as error:
        journal = staged.journal if 'staged' in locals() else None
        sys.exit(durable_release.describe(error, journal))
    except (ValueError, OSError) as error:
        sys.exit(f'发布失败：{error}')


if __name__ == '__main__':
    main()
