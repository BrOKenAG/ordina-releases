"""Standard plugin ZIP adapter: the only path from a developer ZIP to the
existing release engine.

The engine in ``publish_plugin.py`` build a bundle from a source *directory*.
This module turns an untrusted ZIP into a quarantined directory whose contents
already satisfy the protocol, and never lets a ZIP name choose an install
target, a command or an absolute path.

Guarantees:

* every entry is a relative protocol-safe path (no absolute paths, no ``..``,
  no backslashes, no drive letters, no control characters, no dotfiles);
* symlinks, hard-link entries, devices, encrypted entries and duplicate paths
  (including case-insensitive collisions) are rejected;
* entry count, per-file size, total uncompressed size and compression ratio are
  bounded before anything is written;
* extraction is done by hand with ``O_EXCL`` file creation, so a name can never
  overwrite a file outside the quarantine directory;
* the archive must contain exactly one ``plugin.json`` at its root, optionally
  wrapped in a single top-level folder (the common "zip a folder" shape).

The returned inventory is a *preview*. The signed inventory is still produced
by ``publish_plugin.build_bundle`` from the extracted files, so nothing in the
ZIP can declare its own hash or install target.
"""
import hashlib
import os
import stat
import zipfile
import unicodedata
from pathlib import Path

import plugin_protocol as protocol

MAX_ARCHIVE = 4_000_000        # the ZIP itself
MAX_ENTRIES = 96               # files + directories; a bundle allows 32 files
MAX_TOTAL = 8_000_000          # uncompressed payload
MAX_FILE = 1_000_000           # client limit per installed file
MAX_RATIO = 200                # compressed -> uncompressed amplification
MAX_PATH = 200                 # matches PluginProtocol.validRelativePath
IGNORED_NAMES = {'.DS_Store', '__MACOSX', '__pycache__'}


class PackageError(ValueError):
    """A ZIP that must not reach the release engine."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise PackageError(code, message)


def _entry_name(info):
    """Normalise one archive name and reject anything ambiguous."""
    raw = info.filename
    if not isinstance(raw, str) or not raw:
        _fail('package.entry', '安装包包含空文件名。')
    if '\\' in raw:
        _fail('package.entry', '安装包路径包含反斜杠：' + raw)
    if raw.startswith('/') or (len(raw) > 1 and raw[1] == ':'):
        _fail('package.entry', '安装包包含绝对路径：' + raw)
    if any(ord(character) < 32 for character in raw):
        _fail('package.entry', '安装包路径包含控制字符。')
    name = unicodedata.normalize('NFC', raw).rstrip('/')
    parts = [part for part in name.split('/') if part]
    if not parts:
        _fail('package.entry', '安装包包含无效路径：' + raw)
    if any(part in ('.', '..') for part in parts):
        _fail('package.entry', '安装包包含路径穿越：' + raw)
    if any(part in IGNORED_NAMES for part in parts):
        return None
    if len(parts) > 4:
        _fail('package.entry', '安装包目录层级过深：' + raw)
    if not protocol.safe_relative('/'.join(parts)):
        _fail('package.entry', '安装包包含协议不允许的路径：' + raw)
    return '/'.join(parts)


def _check_mode(info, name):
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind == stat.S_IFLNK:
        _fail('package.symlink', '安装包包含符号链接：' + name)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        _fail('package.special', '安装包包含非普通文件：' + name)
    if info.flag_bits & 0x1:
        _fail('package.encrypted', '安装包包含加密条目：' + name)


def read_archive(path):
    """Validate the archive and return {normalised path: ZipInfo}.

    Nothing is written: every rejection happens while the names, modes and
    sizes are being inspected. A single shared top-level folder is stripped so
    both "zip the folder" and "zip its contents" produce the same layout. The
    returned ZipInfo objects belong to a closed archive, so they are metadata
    only; use ``extract`` to obtain the bytes.
    """
    path = Path(path)
    if not path.is_file():
        _fail('package.missing', '找不到安装包：' + str(path))
    if path.stat().st_size > MAX_ARCHIVE:
        _fail('package.size', '安装包超过 4 MB 上限。')
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        _fail('package.format', '不是有效的 ZIP 安装包。')

    with archive:
        infos = archive.infolist()
        if not infos:
            _fail('package.empty', '安装包为空。')
        if len(infos) > MAX_ENTRIES:
            _fail('package.count', '安装包条目过多。')
        entries, seen, total = {}, set(), 0
        for info in infos:
            name = _entry_name(info)
            if name is None:
                continue
            _check_mode(info, name)
            folded = name.casefold()
            if folded in seen:
                _fail('package.duplicate', '安装包包含重复路径：' + name)
            seen.add(folded)
            if not info.is_dir():
                if info.file_size > MAX_FILE:
                    _fail('package.file', f'{name} 超过单文件 1 MB 上限。')
                if info.compress_size == 0 and info.file_size > 0:
                    _fail('package.corrupt', '安装包条目损坏：' + name)
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                    _fail('package.bomb', '安装包压缩比异常：' + name)
                total += info.file_size
                if total > MAX_TOTAL:
                    _fail('package.size', '安装包解压后超过 8 MB 上限。')
                entries[name] = info

    if not entries:
        _fail('package.empty', '安装包没有可用文件。')
    roots = {name.split('/', 1)[0] for name in entries}
    if len(roots) == 1 and all('/' in name for name in entries):
        prefix = roots.pop() + '/'
        entries = {name[len(prefix):]: info for name, info in entries.items()}
    if 'plugin.json' not in entries:
        _fail('package.manifest', '安装包根目录缺少 plugin.json。')
    return entries


def extract(path, destination):
    """Write the archive into an empty directory and return it."""
    entries = read_archive(path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        for name, info in entries.items():
            target = (root / name).resolve()
            if root not in target.parents and target != root:
                _fail('package.entry', '安装包路径逃逸：' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL plus a resolved-parent check: an entry can never replace
            # an existing file, and a directory entry can never become a link.
            try:
                handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                _fail('package.duplicate', '安装包包含重复路径：' + name)
            with os.fdopen(handle, 'wb') as stream:
                with archive.open(info) as source:
                    remaining = MAX_FILE
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            _fail('package.file', f'{name} 超过单文件 1 MB 上限。')
                        stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
    return root


def inventory(root, meta):
    """Preview inventory for the UI, derived exactly as build_bundle will."""
    declared = meta.get('files') if isinstance(meta.get('files'), dict) else {}
    result = []
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == 'plugin.json':
            role = 'metadata'
        elif relative in declared:
            role = declared[relative].get('role')
        elif relative.startswith('extension/'):
            role = 'extension'
        else:
            role = None
        data = path.read_bytes()
        result.append({
            'path': relative,
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'role': role,
        })
    return result
