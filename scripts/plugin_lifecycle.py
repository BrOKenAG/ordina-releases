"""Side-effect-free planning for plugin publication-state changes."""
import copy
import hashlib
import json
import re

import build_feeds
from github_lab_backend import GitHubLabMultiBackend
import plugin_protocol


def plan(source, plugin_id, published):
    if not isinstance(source, dict) or source.get('format') != 2 or \
            not isinstance(source.get('plugins'), dict) or type(published) is not bool:
        raise ValueError('插件状态计划格式无效。')
    if not plugin_protocol.valid_id(plugin_id):
        raise ValueError('插件标识无效。')
    original = source['plugins'].get(plugin_id)
    if not isinstance(original, dict):
        raise ValueError('找不到插件。')
    if original.get('repository') != f'BrOKenAG/ordina-plugin-{plugin_id}':
        raise ValueError('插件仓库不符合官方发布策略。')
    if type(original.get('published')) is not bool:
        raise ValueError('插件上架状态无效。')
    if not original['published'] and original.get('everPublished') is not True:
        raise ValueError('插件尚无已发布版本，不能重新上架。')
    if not plugin_protocol.valid_version(original.get('version')) or \
            not re.fullmatch(r'[0-9a-f]{64}', original.get('sha256', '')) or \
            original['sha256'] == '0' * 64 or \
            type(original.get('size')) is not int or original['size'] <= 0:
        raise ValueError('插件已发布资产信息无效。')
    after = copy.deepcopy(source)
    if original['published'] != published:
        after['plugins'][plugin_id]['published'] = published
        after['plugins'][plugin_id]['everPublished'] = True
    return {'id': plugin_id, 'repository': original['repository'],
            'version': original['version'], 'published': published,
            'noop': original['published'] == published, 'after': after}


def prepare_transaction(change, asset, transaction_id):
    """Build exact signed feed bytes for the existing durable transaction engine."""
    if change['noop']:
        raise ValueError('插件状态未变化。')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', transaction_id or ''):
        raise ValueError('事务标识无效。')
    plugin_id = change['id']
    source = change['after']
    metadata = source['plugins'][plugin_id]
    if not isinstance(asset, bytes) or len(asset) != metadata['size'] or \
            hashlib.sha256(asset).hexdigest() != metadata['sha256']:
        raise ValueError('线上插件资产与记录不一致。')
    generated = build_feeds.generate(source['plugins'])
    target = {'manifest-v2.json': generated['plugin:' + plugin_id][1]}
    if 'compat-plugin:' + plugin_id in generated:
        target['manifest.json'] = generated['compat-plugin:' + plugin_id][1]
    source_bytes = (json.dumps(source, ensure_ascii=False, indent=2) + '\n').encode()
    main = {'registry.json': generated['registry'][1],
            'plugins/manifest.json': generated['legacy'][1],
            'plugins/registry.source.json': source_bytes}
    feeds = {'BrOKenAG/ordina-releases': main, change['repository']: target}
    return {'id': transaction_id,
            'asset': {'url': build_feeds.entry(plugin_id, metadata)['url'],
                      'size': len(asset), 'sha256': metadata['sha256']},
            'feeds': {repo: GitHubLabMultiBackend.encode_files(files).decode('ascii')
                      for repo, files in sorted(feeds.items())},
            'lifecycle': {'id': plugin_id, 'published': change['published']}}
