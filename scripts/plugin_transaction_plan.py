"""Derive the complete set of plugin release files without publishing them."""
import copy
import hashlib
import json
import re
from pathlib import PurePosixPath

from build_feeds import generate

MAIN = 'BrOKenAG/ordina-releases'


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', value):
        raise ValueError('插件版本无效')
    values = [int(part) for part in value.split('.')]
    return tuple(values + [0] * (4 - len(values)))


def build(source, plugin, version, asset, source_files):
    if not isinstance(source, dict) or source.get('format') != 2 or not isinstance(source.get('plugins'), dict):
        raise ValueError('插件源格式无效')
    plugins = copy.deepcopy(source)
    meta = plugins['plugins'].get(plugin)
    if not isinstance(meta, dict): raise ValueError('未知插件')
    if meta.get('version') and _version(version) <= _version(meta['version']):
        raise ValueError('插件版本必须递增')
    _version(version)
    if not isinstance(asset, bytes) or not asset: raise ValueError('插件资产为空')
    name = meta.get('source', '')
    if not isinstance(name,str) or not name or '/' in name or '\\' in name or name.startswith('.'):
        raise ValueError('插件源路径无效')
    prefix = 'Integrations/OptionalPlugins/' + name
    if not source_files: raise ValueError('缺少插件源码')
    for path, data in source_files.items():
        if not isinstance(path,str) or not isinstance(data,bytes): raise ValueError('插件源码无效')
        parts = PurePosixPath(path).parts
        if (path.startswith('/') or '\\' in path or any(x in ('.','..') or x.startswith('.') for x in parts)
                or not (path == prefix or path.startswith(prefix + '/'))):
            raise ValueError('插件源码路径不受允许')
    meta.update({'version':version,'sha256':hashlib.sha256(asset).hexdigest(),
                 'size':len(asset),'published':True})
    generated = generate(plugins['plugins'])
    files = {MAIN: {
        'registry.json':generated['registry'][1],
        'plugins/manifest.json':generated['legacy'][1],
        'plugins/registry.source.json':(json.dumps(plugins,ensure_ascii=False,indent=2)+'\n').encode(),
        **source_files,
    }}
    for name, descriptor in plugins['plugins'].items():
        if not descriptor.get('published'): continue
        repo = descriptor['repository']
        if repo == MAIN or repo in files: raise ValueError('插件发布仓库重复')
        current = generated.get('plugin:' + name)
        if current is None: raise ValueError('缺少插件清单')
        files[repo] = {'manifest-v2.json':current[1]}
        compatibility = generated.get('compat-plugin:' + name)
        if compatibility: files[repo]['manifest.json'] = compatibility[1]
    return {'files':files,'source':plugins,'asset_sha256':meta['sha256']}
