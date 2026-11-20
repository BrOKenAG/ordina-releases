"""Read-only archive previews and explicitly approved, project-bound operations.

run(resolve, request) returns JSON-compatible dictionaries; ArchiveError is handled
by the bridge's existing error envelope. No operation creates a project root.
"""

from collections import OrderedDict, defaultdict
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from archive_worker import binding_for, clips_in, same_file, same_project
from media_archive import (ArchiveError, classify, clip_kind,
                           digest, is_within, parse_mounts, signature,
                           verified_copy, write_json)
from resolve_bridge import database_key


KINDS = {'raw', 'shared', 'trusted', 'temp'}
CLASSIFICATIONS = ('archived', 'raw', 'shared', 'trusted', 'pending', 'offline', 'unknown')
POLICY_NAME = '.ordo-storage.json'
HISTORY_NAME = '.magic-archive-history.json'
PREVIEW_HASH_LIMIT = 1024 * 1024
NETWORK_MOUNT_TYPES = {'smbfs', 'nfs', 'afpfs', 'webdav'}


def require(condition, message):
    if not condition:
        raise ArchiveError(message)


def read_mounts():
    return parse_mounts(subprocess.check_output(
        ['/sbin/mount'], text=True, encoding='utf-8', timeout=10))


def absolute_path(raw, symlink_probe=None):
    require(isinstance(raw, str) and raw and '\x00' not in raw, '目录无效。')
    path = Path(raw)
    require(path.is_absolute() and '..' not in path.parts, '请选择绝对目录，不能包含上级路径。')
    probe = symlink_probe if symlink_probe is not None else Path.is_symlink
    require(not any(probe(part) for part in (path, *path.parents)), '目录包含符号链接，请选择实际目录。')
    return path


def lexical_within(path, roots):
    """Read-only containment for absolute paths already checked for symlinks."""
    path = Path(path)
    return any(path == Path(root) or Path(root) in path.parents for root in roots)


class ScanPaths:
    """Bounded metadata caches owned by one read-only preview, never execution."""

    def __init__(self, limit=2048):
        self.limit = limit
        self.symlinks = OrderedDict()
        self.directories = OrderedDict()

    def cached(self, cache, path, read):
        if path not in cache:
            cache[path] = read(path)
            if len(cache) > self.limit:
                cache.popitem(last=False)
        cache.move_to_end(path)
        return cache[path]

    def absolute(self, raw):
        return absolute_path(raw, lambda path: self.cached(self.symlinks, path, Path.is_symlink))

    def available(self, path, mounts):
        path = Path(path)
        return self.cached(self.directories, path, lambda current: available(current, mounts))


def available(path, mounts):
    path = Path(path)
    volumes = Path('/Volumes')
    if path == volumes or volumes in path.parents:
        disk = volumes / path.relative_to(volumes).parts[0] if path != volumes else volumes
        if not any(Path(root) == disk for root, _ in mounts):
            return False
    return path.is_dir()


def read_object(path, default):
    require(not path.is_symlink(), '记录文件包含符号链接，请检查目录。')
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ArchiveError('无法读取记录：' + path.name) from error
    require(isinstance(value, dict), '记录格式无效：' + path.name)
    return value


def validate_locations(locations, root, mounts, previous=None):
    require(isinstance(locations, list), '存储位置格式无效。')
    result, identities, paths = [], set(), set()
    for location in locations:
        require(isinstance(location, dict), '存储位置格式无效。')
        identity, kind, logical = (location.get(key) for key in ('id', 'kind', 'logicalID'))
        require(all(isinstance(value, str) and value.strip() for value in (identity, kind, logical)), '存储位置标识无效。')
        require(kind in KINDS and identity not in identities, '存储位置类型或标识无效。')
        path = absolute_path(location.get('path'))
        require(path not in {Path('/'), Path('/Users'), Path('/home'), Path('/Volumes'), Path('/tmp'), Path('/private/tmp'), Path.home()}, '请选择具体素材目录。')
        require(not is_within(root, [path]), '存储位置不能覆盖项目目录。')
        require(str(path) not in paths, '存储位置重复。')
        entry = {'id': identity, 'kind': kind, 'logicalID': logical, 'path': str(path)}
        if previous is not None:
            require(available(path, mounts) or entry in previous, '存储目录不可用，请检查连接。')
        identities.add(identity)
        paths.add(str(path))
        result.append(entry)
    return result


def validate_decisions(decisions):
    require(isinstance(decisions, dict), '素材决策格式无效。')
    require(all(isinstance(key, str) and key and isinstance(value, str) and value in {'archive', 'keep'}
                for key, value in decisions.items()), '素材决策只能为 archive 或 keep。')
    return dict(decisions)


def read_policy(root, mounts):
    policy = read_object(root / POLICY_NAME, {'version': 1, 'locations': [], 'decisions': {}})
    require(policy.get('version') == 1, '不支持此存储策略版本。')
    return {'version': 1,
            'locations': validate_locations(policy.get('locations', []), root, mounts),
            'decisions': validate_decisions(policy.get('decisions', {}))}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def context(resolve, request):
    require(resolve is not None, '无法连接 Resolve。')
    manager = resolve.GetProjectManager()
    require(manager is not None, '无法读取项目库。')
    explicit = request.get('projectRoot')
    roots = [explicit] if explicit is not None else request.get('projectRoots', [])
    require(isinstance(roots, list), '项目目录列表无效。')
    for raw in roots:
        absolute_path(raw)
    project, root, identity, database = binding_for(manager, roots, None if explicit is not None else request.get('searchRoot'))
    absolute_path(str(root))
    mounts = read_mounts()
    require(available(root, mounts), '项目目录不可用，请检查连接。')
    require(bool(identity) and any(database.values()), '无法读取工程或项目库标识。')
    if 'projectID' in request:
        require(request['projectID'] == identity, '工程已切换，请重新扫描。')
    if 'database' in request:
        require(isinstance(request['database'], dict) and database_key(request['database']) == database,
                '项目库已切换，请重新扫描。')
    policy = read_policy(root, mounts)
    return manager, project, root, identity, database, mounts, policy


def proposed_destination(source, root, category, reserved, paths=None):
    """Plan collision-safe destinations without reading large media during scans."""
    base = root / '3_素材' / category / '归档'
    source_size = signature(source)[2]
    expected = None
    for number in range(1, 10001):
        folder = base if number == 1 else base / ('同名素材 ' + str(number))
        destination = folder / source.name
        (paths.absolute if paths is not None else absolute_path)(str(destination))
        within = lexical_within if paths is not None else is_within
        require(within(destination, [root]), '归档路径超出项目范围。')
        prior = reserved.get(str(destination))
        if prior is not None:
            if source == prior or (source_size <= PREVIEW_HASH_LIMIT
                                   and source_size == signature(prior)[2] and digest(source) == digest(prior)):
                return destination
            continue
        if not destination.exists():
            reserved[str(destination)] = source
            return destination
        if source_size <= PREVIEW_HASH_LIMIT and destination.is_file() and signature(destination)[2] == source_size:
            if expected is None:
                expected = digest(source)
            if digest(destination) == expected:
                reserved[str(destination)] = source
                return destination
    raise ArchiveError('同名素材过多，请检查归档目录。')


def containing_mount(path, mounts, within):
    matches = ((Path(root), kind) for root, kind in mounts if within(path, [root]))
    return max(matches, key=lambda match: len(match[0].parts), default=(None, ''))


def reported_audio_duration(properties):
    # Resolve already knows the clip duration. Avoid opening each audio file
    # or starting afinfo while building a scan preview.
    try:
        fps = float(properties.get('FPS') or 0)
    except (TypeError, ValueError):
        fps = 0
    if not math.isfinite(fps) or fps <= 0:
        fps = 0
    duration = re.fullmatch(r'(\d+):([0-5]\d):([0-5]\d)[:;](\d+)', str(properties.get('Duration') or ''))
    if duration:
        hours, minutes, seconds, frames = map(int, duration.groups())
        return hours * 3600 + minutes * 60 + seconds + min(frames / (fps or 100), 0.999)
    if fps:
        try:
            return float(properties.get('Frames') or 0) / fps
        except (TypeError, ValueError):
            pass
    return None


def scan_if_changed(resolve, request, cache):
    prepared = context(resolve, request)
    _, project, root, identity, database, mounts, policy = prepared
    clips = list(clips_in(project.GetMediaPool().GetRootFolder()))
    references = tuple((clip.GetUniqueId(), clip.GetClipProperty('File Path')) for clip in clips)
    key = (str(root), identity, database, signature(root)[:2], mounts, fingerprint(policy), references)
    if cache.get('key') == key and time.monotonic() - cache['checkedAt'] < 60:
        return None
    result = scan(resolve, request, prepared=prepared, clips=clips)
    cache.update(key=key, checkedAt=time.monotonic())
    return result


def scan(resolve, request, cache_reads=True, prepared=None, clips=None):
    manager, project, root, identity, database, mounts, policy = prepared or context(resolve, request)
    paths = ScanPaths() if cache_reads else None
    check_path = paths.absolute if paths is not None else absolute_path
    online = paths.available if paths is not None else available
    within = lexical_within if paths is not None else is_within
    pending_roots = [Path.home() / 'Downloads', Path.home() / 'Desktop']
    scan_mounts = mounts
    if paths is not None:
        pending_roots = [check_path(str(path.resolve())) for path in pending_roots]
        scan_mounts = [(Path(mount).resolve(), kind) for mount, kind in mounts]
    root_identity = list(signature(root)[:2])
    history = read_object(root / HISTORY_NAME, {})
    locations = [dict(location, online=online(location['path'], mounts)) for location in policy['locations']]
    items, reserved, identities, external = [], {}, set(), defaultdict(list)
    for clip in clips if clips is not None else clips_in(project.GetMediaPool().GetRootFolder()):
        properties = clip.GetClipProperty() or {}
        raw = properties.get('File Path') or ''
        clip_id = clip.GetUniqueId()
        require(isinstance(clip_id, str) and clip_id and clip_id not in identities, '素材唯一标识缺失或重复。')
        identities.add(clip_id)
        item = {'id': clip_id, 'name': properties.get('Clip Name') or Path(raw).name or clip_id,
                'source': raw, 'destination': None, 'bytes': 0, 'classification': 'unknown',
                'method': 'none', 'warning': '', 'signature': None,
                'decision': policy['decisions'].get(clip_id, 'archive'),
                'category': None, 'destinationSignature': None}
        items.append(item)
        if not raw:
            item['warning'] = '素材没有文件路径。'
            continue
        try:
            source = check_path(raw)
            parent_online = online(source.parent, mounts)
            matching = [location for location in locations if within(source, [location['path']])]
            location = max(matching, key=lambda entry: len(Path(entry['path']).parts)) if matching else None
            if location is not None:
                relative = str(source.relative_to(Path(location['path'])))
                item.update({'locationID': location['id'], 'logicalID': location['logicalID'], 'relativePath': relative,
                             'correspondences': [
                                 {'locationID': other['id'], 'path': str(Path(other['path']) / relative),
                                  'online': other['online'], 'verified': False}
                                 for other in locations if other['logicalID'] == location['logicalID'] and other['id'] != location['id']]})
            if not parent_online or not source.is_file() or (location is not None and not location['online']):
                item.update(classification='offline', warning='源文件离线，请连接存储后重新扫描。')
                continue
            item['signature'] = list(signature(source))
            item['bytes'] = item['signature'][2]
            mount, mount_type = containing_mount(source, scan_mounts, within)
            if within(source, [root]):
                item.update(classification='archived', method='keep')
            elif location is not None:
                item['classification'] = 'pending' if location['kind'] == 'temp' else location['kind']
            elif mount_type in NETWORK_MOUNT_TYPES:
                item['classification'] = 'shared'
            elif within(source, pending_roots):
                item['classification'] = 'pending'
            if item['classification'] in {'archived', 'raw', 'shared', 'trusted'} or item['decision'] == 'keep':
                item['method'] = 'keep'
                continue
            if item['classification'] == 'unknown':
                item['warning'] = '未知位置，需要明确批准。'
                if mount not in {None, Path('/'), Path('/System/Volumes/Data')}:
                    external[mount].append((source, clip_id))
            kind = clip_kind(properties)
            require(not (source.suffix.lower() in {'.r3d', '.ari', '.arx', '.dng'} or
                         kind == 'video' and source.suffix.lower() in {'.exr', '.dpx', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}),
                    '序列帧或多文件相机素材需手动处理。')
            category = classify(properties, reported_audio_duration(properties) if kind == 'audio' else None)
            require(item['bytes'] > 0, '源文件为空，暂不归档。')
            destination = proposed_destination(source, root, category, reserved, paths)
            item.update(category=category, destination=str(destination), method='copy',
                        destinationSignature=list(signature(destination)) if destination.exists() else None)
            collision = ''
            if destination.exists():
                collision = '发现同名同内容副本，将校验后复用。'
            elif destination.parent.name.startswith('同名素材 '):
                collision = '同名素材已分流，不覆盖已有文件。'
            if collision:
                item['warning'] = ' '.join(value for value in (item['warning'], collision) if value)
            record = history.get(clip_id, {})
            if isinstance(record, dict) and record.get('status') == 'copied':
                item['warning'] = '已保留校验副本，重链接待确认。'
                item['retainedCopy'] = True
            require(list(signature(source)) == item['signature'], '源文件仍在变化，请重新扫描。')
        except (ArchiveError, OSError, ValueError) as error:
            item.update(method='none', destination=None, warning=str(error))
    require(same_project(manager, identity, database) and list(signature(root)[:2]) == root_identity,
            '工程或项目目录已变化，请重新扫描。')
    require(fingerprint(read_policy(root, mounts)) == fingerprint(policy), '存储策略已变化，请重新扫描。')
    suggestions = []
    for mount, members in external.items():
        if len(members) >= 3:
            parent = Path(os.path.commonpath([str(source.parent) for source, _ in members]))
            suggestions.append({'kind': 'location', 'path': str(parent), 'clipIDs': [clip_id for _, clip_id in members],
                                'count': len(members)})
    result = {'projectRoot': str(root), 'projectID': identity, 'database': database,
              'projectName': project.GetName(), 'items': items, 'locations': locations, 'suggestions': suggestions}
    result['snapshot'] = {'projectRoot': str(root), 'projectID': identity, 'database': database,
                          'rootIdentity': root_identity, 'policySignature': fingerprint(policy),
                          'items': [dict(item) for item in items]}
    return result


def current_binding(manager, root, identity, database, root_identity, policy_signature):
    try:
        if not same_project(manager, identity, database):
            return False
        absolute_path(str(root))
        mounts = read_mounts()
        if not available(root, mounts) or list(signature(root)[:2]) != root_identity:
            return False
        metadata = read_object(root / '.magic-resolve.json', {})
        return (metadata.get('projectID') == identity and isinstance(metadata.get('database'), dict)
                and database_key(metadata['database']) == database
                and fingerprint(read_policy(root, mounts)) == policy_signature)
    except (ArchiveError, OSError, ValueError):
        return False


def id_list(request, key):
    value = request.get(key, [])
    require(isinstance(value, list) and all(isinstance(item, str) and item for item in value), '批准的素材标识无效。')
    require(len(value) == len(set(value)), '素材标识重复。')
    return value


def validate_snapshot(snapshot, current):
    require(isinstance(snapshot, dict) and isinstance(snapshot.get('items'), list), '请提供扫描预览。')
    fresh = current['snapshot']
    for key in ('projectRoot', 'projectID', 'database', 'rootIdentity', 'policySignature'):
        require(snapshot.get(key) == fresh[key], '工程、目录或存储策略已变化，请重新扫描。')


def matches_approved(item, approved, created):
    if item == approved:
        return True
    if item is None or approved['destinationSignature'] is not None:
        return False
    owned = created.get(approved['destination'])
    if owned is None:
        return False
    destination = Path(approved['destination'])
    if list(signature(destination)) != owned['signature']:
        return False
    return dict(item, destination=approved['destination'], destinationSignature=None,
                warning=approved['warning']) == approved


@contextmanager
def project_lock(root):
    descriptor = os.open(str(root / '.magic-archive.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArchiveError('此项目正在归档，请稍后重试。') from error
        yield
    finally:
        os.close(descriptor)


def execute(resolve, request, permitted=lambda: True):
    require(permitted(), '归档已暂停。')
    preview = request.get('preview', request.get('snapshot'))
    if isinstance(preview, dict) and 'snapshot' in preview:
        preview = preview['snapshot']
    current = scan(resolve, request, cache_reads=False)
    validate_snapshot(preview, current)
    approved = id_list(request, 'selectedIDs' if 'selectedIDs' in request else 'approvedIDs')
    unchecked = set(id_list(request, 'uncheckedIDs'))
    unknown = set(id_list(request, 'approvedUnknownIDs'))
    require(unknown.issubset(set(approved)), '未知位置批准必须属于已批准素材。')
    old_items = preview['items']
    require(all(isinstance(item, dict) and isinstance(item.get('id'), str) for item in old_items), '预览素材格式无效。')
    old = {item['id']: item for item in old_items}
    require(len(old) == len(old_items), '预览素材标识重复。')
    fresh = {item['id']: item for item in current['items']}
    selected = [identity for identity in approved if identity not in unchecked]
    for identity in selected:
        require(identity in old and identity in fresh and old[identity] == fresh[identity], '素材或目标已变化，请重新扫描。')
        item = fresh[identity]
        require(item['method'] != 'copy' or item['classification'] != 'unknown' or identity in unknown,
                '未知位置素材需要明确批准：' + item['name'])
    results = [item_result(fresh[identity], 'skipped', fresh[identity]['source'])
               for identity in selected if fresh[identity]['method'] != 'copy']
    selected = [identity for identity in selected if fresh[identity]['method'] == 'copy']
    if not selected:
        return execution_result(current, results, approved)
    manager, project, root, project_id, database, _, _ = context(resolve, request)
    snap = current['snapshot']
    def cancelled():
        return not permitted() or not current_binding(manager, root, project_id, database, snap['rootIdentity'], snap['policySignature'])
    require(not cancelled(), '工程或目录已变化，请重新扫描。')
    with project_lock(root):
        require(not cancelled(), '工程或目录已变化，请重新扫描。')
        records = read_object(root / HISTORY_NAME, {})
        created = {}
        for identity in selected:
            item = old[identity]
            copied = None
            relink_attempted = False
            clip = None
            try:
                live = scan(resolve, request, cache_reads=False)
                validate_snapshot(preview, live)
                item = next((entry for entry in live['items'] if entry['id'] == identity), None)
                require(matches_approved(item, old[identity], created), '素材或目标已变化，请重新扫描。')
                item = old[identity]
                clips = [clip for clip in clips_in(project.GetMediaPool().GetRootFolder()) if clip.GetUniqueId() == identity]
                require(len(clips) == 1 and not cancelled(), '工程或素材已变化，请重新扫描。')
                clip = clips[0]
                source = Path(item['source'])
                require(same_file(clip.GetClipProperty('File Path'), source) and list(signature(source)) == item['signature'],
                        '源文件已变化，请重新扫描。')
                copied = verified_copy(source, root, item['category'], cancelled=cancelled,
                                       expected_destination=item['destination'], expected_signature=item['signature'])
                copied.update(signature=item['signature'], category=item['category'], status='copied')
                created[copied['destination']] = {'signature': list(signature(copied['destination'])), 'sha256': copied['sha256']}
                require(not cancelled(), '工程或目录已切换，副本保留，未改链接。')
                records[identity] = copied
                write_json(root / HISTORY_NAME, records)
                require(copied['destination'] == item['destination'], '目标目录已变化，副本保留，未改链接。')
                require(not cancelled() and same_file(clip.GetClipProperty('File Path'), source)
                        and list(signature(source)) == item['signature'], '工程或源文件已变化，副本保留，未改链接。')
                destination = Path(copied['destination'])
                absolute_path(str(destination))
                require(digest(destination, cancelled) == copied['sha256'], '副本已变化，保留原链接。')
                require(not cancelled() and same_file(clip.GetClipProperty('File Path'), source)
                        and list(signature(source)) == item['signature'], '工程或源文件已变化，保留原链接。')
                relink_attempted = True
                require(project.GetMediaPool().RelinkClips([clip], str(destination.parent)), '副本已校验，Resolve 重链接失败。')
                require(same_file(clip.GetClipProperty('File Path'), destination), 'Resolve 未确认新路径，请检查素材链接。')
                require(not cancelled(), '工程已切换，副本保留，请检查素材链接。')
                copied['status'] = 'relinked'
                write_json(root / HISTORY_NAME, records)
                results.append(item_result(item, 'archived', clip.GetClipProperty('File Path'), copied=copied))
            except Exception as error:
                results.append(item_result(item or old[identity], 'error',
                                           current_clip_path(clip) if clip is not None else old[identity]['source'],
                                           copied=copied, warning=str(error), relink_attempted=relink_attempted))
    return execution_result(current, results, approved)


def item_result(item, status, actual_source, copied=None, warning='', relink_attempted=False):
    destination = copied['destination'] if copied else None
    relinked = bool(destination and same_file(actual_source, destination))
    return {'id': item['id'], 'status': status,
            'actualResult': 'relinked' if relinked else 'copy_retained' if copied else 'kept' if status == 'skipped' else 'not_copied',
            'source': item['source'], 'actualSource': actual_source,
            'destination': destination, 'originalRetained': bool(item['source'] and Path(item['source']).is_file()),
            'relinkFailed': bool(relink_attempted and not relinked),
            'warning': '' if status == 'archived' else warning or item['warning']}


def current_clip_path(clip):
    try:
        return clip.GetClipProperty('File Path')
    except Exception:
        return None


def execution_result(current, results, approved):
    order = {identity: position for position, identity in enumerate(approved)}
    results.sort(key=lambda item: order[item['id']])
    retained = list(dict.fromkeys(item['source'] for item in results if item['originalRetained']))
    return dict(current, results=results, retainedOriginals=retained)


def storage_update(resolve, request):
    manager, _, root, identity, database, mounts, policy = context(resolve, request)
    root_identity = list(signature(root)[:2])
    locations = request.get('locations', policy['locations'])
    if 'addLocation' in request:
        require('locations' not in request, '请选择一种存储位置操作。')
        locations = policy['locations'] + [request['addLocation']]
    if 'removeLocation' in request:
        require('locations' not in request and 'addLocation' not in request, '请选择一种存储位置操作。')
        remove_id = request['removeLocation']
        require(any(location['id'] == remove_id for location in locations), '素材位置已变化，请重新扫描。')
        locations = [location for location in locations if location['id'] != remove_id]
    updated = {'version': 1,
               'locations': validate_locations(locations, root, mounts, policy['locations']),
               'decisions': dict(policy['decisions'], **validate_decisions(request.get('decisions', {})))}
    require(current_binding(manager, root, identity, database, root_identity, fingerprint(policy)),
            '工程或目录已变化，请重新扫描。')
    with project_lock(root):
        require(current_binding(manager, root, identity, database, root_identity, fingerprint(policy)),
                '工程或目录已变化，请重新扫描。')
        write_json(root / POLICY_NAME, updated)
    return {'projectRoot': str(root), 'projectID': identity, 'database': database, **updated}


def run(resolve, request):
    require(isinstance(request, dict), '请求格式无效。')
    action = request.get('action')
    require(action in {'archive_scan', 'archive_execute', 'storage_update'}, '未知归档操作。')
    return {'archive_scan': scan, 'archive_execute': execute, 'storage_update': storage_update}[action](resolve, request)
