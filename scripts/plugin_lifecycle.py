"""Side-effect-free planning for plugin publication-state changes."""
import copy
import re

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
