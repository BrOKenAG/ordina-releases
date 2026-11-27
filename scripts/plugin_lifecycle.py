"""Side-effect-free planning for plugin publication-state changes."""
import copy
import base64
import hashlib
import json
import re

import build_feeds
from github_lab_backend import GitHubLabMultiBackend
import plugin_protocol


KEEP = '__keep__'


def plan(source, plugin_id, published, icon_id=KEEP):
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
    icon_changed = False
    if icon_id != KEEP:
        target_icon = None if icon_id in (None, '', 'default') else icon_id
        if not plugin_protocol.valid_icon_id(target_icon):
            raise ValueError('内置图标标识无效。')
        icon_changed = original.get('iconID') != target_icon
    else:
        target_icon = original.get('iconID')
    after = copy.deepcopy(source)
    if original['published'] != published:
        after['plugins'][plugin_id]['published'] = published
        after['plugins'][plugin_id]['everPublished'] = True
    if icon_changed:
        if target_icon is None:
            after['plugins'][plugin_id].pop('iconID', None)
        else:
            after['plugins'][plugin_id]['iconID'] = target_icon
    return {'id': plugin_id, 'repository': original['repository'],
            'version': original['version'], 'published': published,
            'iconID': target_icon, 'iconChanged': icon_changed,
            'noop': original['published'] == published and not icon_changed,
            'before': copy.deepcopy(source), 'after': after}


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
            'lifecycle': {'id': plugin_id, 'published': change['published'],
                          'iconID': change['iconID'], 'iconChanged': change['iconChanged'],
                          'before': change['before']}}


def verify_transaction(transaction, remote):
    """Verify signed desired feeds and remote snapshots before a state change."""
    from feed_signing import verify_envelope
    action = transaction.get('lifecycle') or {}
    icon = action.get('iconID') if action.get('iconChanged') else KEEP
    change = plan(action.get('before'), action.get('id'), action.get('published'), icon)
    if change['noop']:
        raise ValueError('插件状态未变化。')
    plugin_id, repo = change['id'], change['repository']
    main_repo = 'BrOKenAG/ordina-releases'
    if set(transaction.get('feeds', {})) != {main_repo, repo}:
        raise ValueError('事务仓库范围无效。')
    metadata = change['before']['plugins'][plugin_id]
    expected_asset = {'url': build_feeds.entry(plugin_id, metadata)['url'],
                      'size': metadata['size'], 'sha256': metadata['sha256']}
    if transaction.get('asset') != expected_asset:
        raise ValueError('事务资产与原发布记录不一致。')
    before = build_feeds.documents(change['before']['plugins'])
    after = build_feeds.documents(change['after']['plugins'])

    def documents(values, source):
        registry, legacy, per_plugin = values
        result = {main_repo: {'registry.json': registry,
                              'plugins/manifest.json': legacy,
                              'plugins/registry.source.json': source},
                  repo: {'manifest-v2.json': per_plugin[plugin_id]}}
        if source['plugins'][plugin_id].get('legacy'):
            entry = legacy['plugins'].get(plugin_id)
            result[repo]['manifest.json'] = {'format': 1,
                                             'plugins': {plugin_id: entry} if entry else {}}
        return result

    old = documents(before, change['before'])
    new = documents(after, change['after'])
    heads = {}
    for repository in (main_repo, repo):
        desired = json.loads(transaction['feeds'][repository])
        head, remote_bytes = remote.read(repository)
        heads[repository] = head
        current = json.loads(remote_bytes)
        if set(desired) != set(new[repository]) or set(current) != set(old[repository]):
            raise ValueError('事务文件范围无效。')
        observed = {}
        for path in desired:
            desired_bytes = base64.b64decode(desired[path], validate=True)
            current_bytes = base64.b64decode(current[path], validate=True)
            decode = json.loads if path.endswith('registry.source.json') else verify_envelope
            desired_doc = decode(desired_bytes)
            current_doc = decode(current_bytes)
            observed[path] = current_doc
            if desired_doc != new[repository][path]:
                raise ValueError('签名清单或远端状态与事务不一致。')
        if observed not in (old[repository], new[repository]):
            raise ValueError('远端快照已变化，停止事务。')
    return heads
