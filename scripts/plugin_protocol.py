"""Versioned plugin description protocol shared by every release tool.

A plugin release is described by ONE signed manifest entry that contains:

* ``descriptor`` -- presentation metadata, compatibility floor and the
  capability set the client is allowed to use.  A descriptor can never carry
  a command, a shell fragment or an absolute install target.
* ``files`` -- the exact inventory the client verifies (path, size, SHA-256,
  role, optional install name and executable bit).  The client no longer
  hardcodes a per-plugin file list.

``format`` stays 1 so ordina <= 1.9.6 can keep reading ``version/url/sha256``
from the same document; the new keys are additive.
"""
import hashlib
import re

PROTOCOL = 2
FORMAT = 1
CAPABILITIES = {
    'macos.application',
    'resolve.utilityScript',
    'resolve.support',
    'chrome.nativeMessagingHost',
    'chrome.unpackedExtension',
}
ROLES = {
    'application',
    'metadata',
    'utilityScript',
    'support',
    'nativeMessagingHost',
    'nativeMessagingManifest',
    'extension',
}
ID_RE = re.compile(r'^[a-z][a-z0-9-]{0,31}$')
VERSION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+){1,3}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
SYMBOL_RE = re.compile(r'^[a-z][a-z0-9.]{0,63}$')
SUPPORT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$')
SEGMENT_RE = re.compile(r'^[A-Za-z0-9 _.\-]+$')
INSTALL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$')


def valid_id(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def valid_version(value):
    return isinstance(value, str) and VERSION_RE.fullmatch(value) is not None


def valid_sha(value):
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def valid_symbol(value):
    return isinstance(value, str) and SYMBOL_RE.fullmatch(value) is not None


def valid_support_name(value):
    return isinstance(value, str) and SUPPORT_RE.fullmatch(value) is not None


def safe_relative(path, depth=4):
    """Mirror the client's bundle-path rules; reject traversal and dotfiles."""
    if not isinstance(path, str) or not path or len(path.encode()) > 200:
        return False
    if path.startswith('/') or '\\' in path or ':' in path or '\0' in path:
        return False
    parts = path.split('/')
    if len(parts) >= 2 and parts[0].endswith('.app') and parts[1] == 'Contents':
        depth = max(depth, 12)
    if len(parts) > depth or any(not part or part.startswith('.') for part in parts):
        return False
    return all(SEGMENT_RE.fullmatch(part) for part in parts)


def valid_install_name(value):
    return (isinstance(value, str) and INSTALL_RE.fullmatch(value) is not None
            and '/' not in value and '\\' not in value and not value.startswith('.'))


def valid_legacy_name(value):
    """Old menu entries may contain non-ASCII names (e.g. 导入阅流.py)."""
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and value.endswith('.py') and not value.startswith('.')
            and not any(character in value for character in '/\\:\0'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def descriptor(name, meta, version=None, files=None):
    """Build the signed descriptor from flat registry-source metadata."""
    result = {
        'id': name,
        'name': meta.get('name'),
        'summary': meta.get('summary'),
        'symbol': meta.get('symbol'),
        'version': version or meta.get('version'),
        'minClientVersion': meta.get('minClientVersion'),
        'kind': 'bundle' if meta.get('kind') == 'bundle' else 'script',
        'protocol': meta.get('protocol', PROTOCOL),
        'capabilities': list(meta.get('capabilities') or []),
    }
    for key in ('supportDirectoryName', 'nativeMessagingHost'):
        if meta.get(key):
            result[key] = meta[key]
    if meta.get('legacyNames'):
        result['legacyNames'] = list(meta['legacyNames'])
    for item in files or []:
        if item.get('role') == 'utilityScript':
            result['utilityScript'] = item.get('installName') or item['path'].rsplit('/', 1)[-1]
            break
    return result


def validate_descriptor(name, value):
    problems = []
    if not valid_id(name) or value.get('id') != name:
        problems.append('插件标识不合法')
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 64:
        problems.append('插件名称不合法')
    if not isinstance(value.get('summary'), str) or not 1 <= len(value['summary']) <= 160:
        problems.append('插件简介不合法')
    if not valid_symbol(value.get('symbol')):
        problems.append('图标标识不合法')
    if not valid_version(value.get('version')):
        problems.append('版本号不合法')
    if not valid_version(value.get('minClientVersion')):
        problems.append('最低客户端版本不合法')
    if value.get('kind') not in ('bundle', 'script'):
        problems.append('插件类型不合法')
    if value.get('protocol') != PROTOCOL:
        problems.append('协议版本不受支持')
    capabilities = value.get('capabilities')
    if (not isinstance(capabilities, list) or not capabilities
            or len(set(capabilities)) != len(capabilities)
            or any(item not in CAPABILITIES for item in capabilities)):
        problems.append('能力集合不合法')
    if 'macos.application' in (capabilities or []) and (not valid_version(value.get('minClientVersion')) or tuple(map(int, (value['minClientVersion'].split('.') + ['0'] * 4)[:4])) < (1, 10, 0, 0)):
        problems.append('原生插件需要 ordina 1.10.0 或更新版本')
    if value.get('supportDirectoryName') and not valid_support_name(value['supportDirectoryName']):
        problems.append('支持目录名不合法')
    if value.get('nativeMessagingHost') and not safe_relative(value['nativeMessagingHost'], depth=2):
        problems.append('本地通信入口路径不合法')
    if value.get('utilityScript') and not (
            valid_install_name(value['utilityScript']) and value['utilityScript'].endswith('.py')):
        problems.append('主脚本文件名不合法')
    legacy = value.get('legacyNames', [])
    if (not isinstance(legacy, list) or len(legacy) > 8
            or any(not valid_legacy_name(item) for item in legacy)):
        problems.append('旧文件名不合法')
    if problems:
        raise ValueError('；'.join(problems))


def validate_files(name, kind, files):
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError('文件清单数量无效')
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('文件条目无效')
        path = item.get('path')
        if not safe_relative(path) or path in seen:
            raise ValueError('文件路径无效或重复：' + str(path))
        seen.add(path)
        if item.get('role') not in ROLES:
            raise ValueError('文件角色无效：' + str(path))
        if not valid_sha(item.get('sha256')):
            raise ValueError('文件哈希无效：' + str(path))
        size = item.get('size')
        if not isinstance(size, int) or not 1 <= size <= (16_000_000 if item.get('role') == 'application' else 1_000_000):
            raise ValueError('文件大小无效：' + str(path))
        if 'installName' in item and not valid_install_name(item['installName']):
            raise ValueError('安装文件名无效：' + str(path))
        if 'executable' in item and not isinstance(item['executable'], bool):
            raise ValueError('可执行标记无效：' + str(path))
    app_items = [item['path'] for item in files if item['role'] == 'application']
    if app_items:
        roots = {path.split('/')[0] for path in app_items}
        if len(roots) != 1 or not next(iter(roots)).endswith('.app') or any(not path.startswith(next(iter(roots)) + '/Contents/') for path in app_items):
            raise ValueError('原生 App 文件路径无效')
        if any(item['path'].startswith(next(iter(roots)) + '/') and item['role'] != 'application' for item in files):
            raise ValueError('原生 App 文件角色不一致')
    roles = [item['role'] for item in files]
    if kind == 'script':
        if len(files) != 1 or roles != ['utilityScript']:
            raise ValueError('脚本插件必须只包含一个 Resolve 脚本')
    else:
        if not any(item['role'] == 'metadata' and item['path'] == 'plugin.json' for item in files):
            raise ValueError('插件包缺少 plugin.json 元数据')
        if roles.count('utilityScript') != 1:
            raise ValueError('插件包必须包含一个 Resolve 脚本')
    return True


def manifest_entry(name, meta):
    """The signed per-plugin entry: legacy fields + descriptor + file inventory."""
    entry = {
        'version': meta['version'],
        'url': meta['url'],
        'sha256': meta['sha256'],
        'size': meta['size'],
    }
    if meta.get('kind') == 'bundle':
        entry['kind'] = 'bundle'
    entry['descriptor'] = descriptor(name, meta)
    entry['files'] = [dict(item) for item in (meta.get('files') or [])]
    return entry


def validate_manifest_entry(name, entry):
    """Validate one signed per-plugin entry (format 1 envelope)."""
    value = entry.get('descriptor')
    if not isinstance(value, dict):
        raise ValueError('缺少插件描述')
    validate_descriptor(name, value)
    if entry.get('version') != value['version']:
        raise ValueError('清单版本与描述不一致')
    if entry.get('kind') not in (None, 'bundle', 'script') or value['kind'] != (
            'bundle' if entry.get('kind') == 'bundle' else 'script'):
        raise ValueError('清单类型与描述不一致')
    if not valid_sha(entry.get('sha256')):
        raise ValueError('资产哈希无效')
    size = entry.get('size')
    if not isinstance(size, int) or not 1 <= size <= (96_000_000 if 'macos.application' in value['capabilities'] else 4_000_000):
        raise ValueError('资产大小无效')
    validate_files(name, value['kind'], entry.get('files'))
    if ('macos.application' in value['capabilities']) != any(item['role'] == 'application' for item in entry['files']):
        raise ValueError('原生能力与文件清单不一致')
    return True
