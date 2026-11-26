#!/usr/bin/env python3
"""Build the standalone developer publishing tool and its signed manifest.

The tool is a deterministic Python zipapp (``ordina-publisher-<version>.pyz``)
that contains the release engine, the plugin protocol and the ZIP adapter. It is
deployed outside ordina.app so it can be updated without touching the app
bundle or its signature.

    python3 scripts/build_dev_tool.py --version 1.0.0 --sign
    python3 scripts/build_dev_tool.py --check

``--sign`` writes the signed manifest envelope ``dev-tool/manifest.json`` with
the Ed25519 release key already pinned in the client. ``--check`` rebuilds the
archive in memory and compares it with the committed source manifest, so a
stale hash can never be published.
"""
import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist' / 'dev-tool'
MANIFEST_SOURCE = ROOT / 'dev-tool' / 'manifest.source.json'
TOOL = 'ordina-publisher'
# The declared version is baked into the entry point, so the archive reports
# its own release number instead of the one in the checked-in source tree.
ENTRY = '''import os
import sys
if not {lab!r} and os.environ.get('ORDINA_DEV_LAB') == '1':
    sys.exit('Production tool cannot run with lab enabled.')
import dev_publish
dev_publish.TOOL_VERSION = {version!r}
if __name__ == '__main__':
    sys.exit(dev_publish.main())
'''
# Everything the engine imports at run time. dev_lab_backend is only bundled
# into a lab build: the production tool must not contain an isolated backend.
MODULES = (
    'dev_publish.py', 'production_authorization.py', 'dev_credentials.py', 'dev_zip.py', 'durable_release.py',
    'durable_transaction.py', 'durable_journal.py', 'plugin_protocol.py',
    'publish_plugin.py', 'release_transaction.py', 'build_feeds.py',
    'feed_signing.py', 'ed25519_verify.py', 'plugin_lifecycle.py', 'sparkle_tools.py', 'github_production_backend.py',
    'github_lab_backend.py', 'check_feeds.py', 'rollback_feeds.py',
)
LAB_MODULES = ('dev_lab_backend.py', 'lab_signing.py')
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
ASSET_TAG = 'dev-tool-v{version}'


def read_version():
    text = (ROOT / 'scripts' / 'dev_publish.py').read_text(encoding='utf-8')
    for line in text.splitlines():
        if line.startswith('TOOL_VERSION = '):
            return line.split('=', 1)[1].strip().strip("'\"")
    raise SystemExit('dev_publish.py 缺少 TOOL_VERSION')


def build(version, lab=False):
    """Return deterministic archive bytes for the given tool version."""
    modules = list(MODULES) + (list(LAB_MODULES) if lab else [])
    buffer = _Buffer()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        def add(name, data):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            info.create_system = 3
            archive.writestr(info, data)

        add('__main__.py', ENTRY.format(version=version, lab=lab).encode('utf-8'))
        add('VERSION', f'{TOOL} {version}\n'.encode('utf-8'))
        for name in modules:
            path = ROOT / 'scripts' / name
            if not path.is_file():
                raise SystemExit('缺少模块：' + name)
            add(name, path.read_bytes())
    return buffer.value


class _Buffer:
    """Minimal seekable buffer so ZipFile can write without a temp file."""

    def __init__(self):
        self._chunks = []
        self._position = 0
        self._closed = False

    def write(self, data):
        self._chunks.append((self._position, bytes(data)))
        self._position += len(data)
        return len(data)

    def tell(self):
        return self._position

    def flush(self):
        return None

    def seek(self, offset, whence=0):
        if whence == 0:
            self._position = offset
        elif whence == 1:
            self._position += offset
        else:
            self._position = max(0, self._position + offset)
        return self._position

    def close(self):
        self._closed = True

    @property
    def value(self):
        result = bytearray(max(self._position, 0))
        for offset, chunk in self._chunks:
            result[offset:offset + len(chunk)] = chunk
        return bytes(result)


def entry(version, data, notes=''):
    return {
        'version': version,
        'protocol': 1,
        'minClientVersion': '1.10.0',
        'asset': f'{TOOL}-{version}.pyz',
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
        'tag': ASSET_TAG.format(version=version),
        'url': (f'https://github.com/BrOKenAG/ordina-releases/releases/download/'
                f'{ASSET_TAG.format(version=version)}/{TOOL}-{version}.pyz'),
        'notes': notes,
    }


def document(entries):
    ordered = sorted(entries, key=lambda item: _version_key(item['version']))
    return {'format': 1, 'tool': TOOL, 'channel': 'stable', 'versions': ordered}


def _version_key(value):
    return tuple(map(int, (value.split('.') + ['0'] * 4)[:4]))


def load_source():
    if not MANIFEST_SOURCE.is_file():
        return {'format': 1, 'tool': TOOL, 'channel': 'stable', 'versions': []}
    return json.loads(MANIFEST_SOURCE.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default=read_version())
    parser.add_argument('--notes', default='')
    parser.add_argument('--lab', action='store_true', help='包含隔离后端（仅供验收）')
    parser.add_argument('--sign', action='store_true', help='写入签名清单')
    parser.add_argument('--check', action='store_true', help='校验已提交的清单与源码一致')
    parser.add_argument('--output', type=Path, default=DIST)
    args = parser.parse_args()

    data = build(args.version, lab=args.lab)
    digest = hashlib.sha256(data).hexdigest()
    current = entry(args.version, data, args.notes)

    if args.check:
        source = load_source()
        match = [item for item in source.get('versions', [])
                 if item.get('version') == args.version]
        if not match:
            sys.exit(f'清单中没有 {args.version}。')
        if match[0].get('sha256') != digest or match[0].get('size') != len(data):
            sys.exit(f'{args.version} 的清单哈希与源码不一致，请重新构建并签名。')
        print(f'{TOOL} {args.version} 清单校验通过：{digest}')
        return

    args.output.mkdir(parents=True, exist_ok=True)
    asset = args.output / current['asset']
    asset.write_bytes(data)
    print(f'构建 {asset}（{len(data):,} 字节，sha256 {digest[:16]}…）')

    source = load_source()
    versions = [item for item in source.get('versions', [])
                if item.get('version') != args.version]
    versions.append(current)
    source = document(versions)
    MANIFEST_SOURCE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_SOURCE.write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'更新 {MANIFEST_SOURCE.relative_to(ROOT)}')

    if not args.sign:
        print('未签名；正式清单需要 --sign（使用 ordina-sparkle  Ed25519 密钥）。')
        return
    from feed_signing import envelope_bytes
    envelope = envelope_bytes(source)
    target = ROOT / 'dev-tool' / 'manifest.json'
    target.write_bytes(envelope)
    print(f'写入签名清单 {target.relative_to(ROOT)}（{len(envelope):,} 字节）')


if __name__ == '__main__':
    main()
