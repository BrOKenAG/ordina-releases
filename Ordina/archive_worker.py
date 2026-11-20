import fcntl
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import time
from contextlib import contextmanager

sys.dont_write_bytecode = True

from media_archive import ArchiveError, audio_duration, classify, clip_kind, digest, is_local_source, is_within, parse_mounts, safe_directory, signature, verified_copy, write_json
from resolve_bridge import database_key
from archive_service import read_config, service_enabled, write_service_status, scan_interval_seconds


running = True
service_config = None
service_instance = None


def emit(**event):
    if service_config is not None:
        write_service_status(service_config, service_instance, dict(event, running=True, workerPID=os.getpid()))
        return
    print('MAGIC_ARCHIVE:' + json.dumps(event, ensure_ascii=False), flush=True)


def stop(*arguments):
    global running
    running = False


def keep_running(parent):
    if not running:
        return False
    if service_config is not None:
        return service_enabled(service_config, service_instance)
    return os.getppid() == parent


def clips_in(folder):
    for clip in folder.GetClipList() or []:
        yield clip
    for child in folder.GetSubFolderList() or []:
        yield from clips_in(child)


def binding_for(manager, roots, search_root):
    project = manager.GetCurrentProject()
    if project is None:
        raise ArchiveError('等待打开已关联工程。')
    identity = project.GetUniqueId()
    database = database_key(manager.GetCurrentDatabase())
    candidates = list(roots)
    base = Path(search_root) if search_root else None
    if base and base.is_dir():
        candidates.extend(str(child) for child in base.iterdir() if child.is_dir() and not child.is_symlink())
    found = []
    seen = set()
    for candidate in candidates:
        root = Path(candidate)
        if not root.is_dir() or root.is_symlink():
            continue
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        try:
            metadata = json.loads((root / '.magic-resolve.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if metadata.get('projectID') == identity and database_key(metadata.get('database', {})) == database:
            found.append(root)
    if len(found) != 1:
        raise ArchiveError('未找到唯一的项目关联，请关联当前工程或检查存储连接。')
    return project, found[0], identity, database


def same_project(manager, identity, database):
    project = manager.GetCurrentProject()
    return project is not None and project.GetUniqueId() == identity and database_key(manager.GetCurrentDatabase()) == database


def archive_clip(manager, project, clip, root, mounts, records, cancelled, progress, identity, database):
    properties = clip.GetClipProperty() or {}
    raw_path = properties.get('File Path')
    if not raw_path:
        return 'skipped'
    source = Path(raw_path).resolve()
    if not is_local_source(source, mounts) or is_within(source, [root]):
        return 'skipped'
    if not source.is_file():
        raise ArchiveError('源文件离线或是序列帧，暂不处理。')
    kind = clip_kind(properties)
    if source.suffix.lower() in {'.r3d', '.ari', '.arx', '.dng'} or (kind == 'video' and source.suffix.lower() in {'.exr', '.dpx', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}):
        raise ArchiveError('序列帧或多文件相机素材需手动处理。')
    category = classify(properties, audio_duration(source, properties) if kind == 'audio' else None)
    if project.GetUniqueId() != identity or not same_project(manager, identity, database):
        raise ArchiveError('工程或项目库已切换，未处理素材。')
    clip_id = clip.GetUniqueId()
    if not clip_id:
        raise ArchiveError('无法读取素材唯一标识，暂不处理。')
    original_signature = signature(source)
    entry = records.get(clip_id, {})
    if entry.get('source') == str(source) and entry.get('signature') == list(original_signature) and Path(entry.get('destination', '')).is_file() and is_within(Path(entry['destination']), [root]):
        if digest(source, cancelled) != entry.get('sha256') or digest(entry['destination'], cancelled) != entry.get('sha256'):
            raise ArchiveError('已归档副本或源文件发生变化，保留原链接。')
        copied = entry
    else:
        copied = verified_copy(source, root, category, progress=progress, cancelled=cancelled)
    copied.update({'signature': list(original_signature), 'category': category, 'status': 'copied'})
    records[clip_id] = copied
    write_json(root / '.magic-archive-history.json', records)
    if cancelled() or not same_project(manager, identity, database):
        raise ArchiveError('工程已切换或归档已暂停，副本保留，未改链接。')
    if not same_file(clip.GetClipProperty('File Path'), source) or signature(source) != original_signature:
        raise ArchiveError('素材路径或源文件已变化，副本保留，未改链接。')
    destination = Path(copied['destination'])
    media_pool = project.GetMediaPool()
    if not media_pool.RelinkClips([clip], str(destination.parent)):
        raise ArchiveError('副本已校验，Resolve 重链接失败，稍后重试。')
    if not same_file(clip.GetClipProperty('File Path'), destination):
        raise ArchiveError('Resolve 未确认新路径，请检查素材链接。')
    copied['status'] = 'relinked'
    write_json(root / '.magic-archive-history.json', records)
    return 'archived'


def same_file(first, second):
    try:
        return bool(first) and os.path.samefile(first, second)
    except OSError:
        return False


def reconcile_record(manager, clip, root, records, identity, database, cancelled=lambda: False):
    record = records.get(clip.GetUniqueId(), {})
    destination = record.get('destination')
    if record.get('status') != 'copied' or not destination or not is_within(Path(destination), [root]):
        return False
    if not same_file(clip.GetClipProperty('File Path'), destination) or digest(destination, cancelled) != record.get('sha256'):
        return False
    if cancelled() or not same_project(manager, identity, database):
        return False
    record['status'] = 'relinked'
    write_json(root / '.magic-archive-history.json', records)
    return True


def legacy_path(record, root):
    for key in ('legacyDestination', 'destination'):
        raw = record.get(key)
        if not raw:
            continue
        path = Path(raw)
        category = record.get('category')
        if category in {'视频', '图片', '音乐', '音效'} and path.parent.parent == root / '3_素材' / category and re.fullmatch(r'[0-9a-f]{64}_[0-9a-f]{16}', path.parent.name) and is_within(path, [root]):
            return path
    return None


def organize_legacy_clip(manager, project, clip, root, records, identity, database, cancelled):
    record = records.get(clip.GetUniqueId(), {})
    old = legacy_path(record, root)
    if old is None:
        for candidate in list(records.values()):
            if not isinstance(candidate, dict):
                continue
            candidate_path = legacy_path(candidate, root)
            if candidate_path is not None and same_file(clip.GetClipProperty('File Path'), candidate_path):
                record = dict(candidate)
                records[clip.GetUniqueId()] = record
                old = candidate_path
                break
    if old is None or not same_file(clip.GetClipProperty('File Path'), old):
        return False
    if cancelled() or not same_project(manager, identity, database):
        raise ArchiveError('工程已切换，未整理旧归档。')
    if digest(old, cancelled) != record.get('sha256'):
        raise ArchiveError('旧归档校验失败，保留原链接。')
    copied = verified_copy(old, root, record['category'], cancelled=cancelled)
    destination = Path(copied['destination'])
    record.update({'legacyDestination': str(old), 'destination': str(destination), 'status': 'copied', 'layoutVersion': 2})
    write_json(root / '.magic-archive-history.json', records)
    if cancelled() or not same_project(manager, identity, database) or not same_file(clip.GetClipProperty('File Path'), old):
        raise ArchiveError('工程或素材已变化，保留旧归档。')
    if not project.GetMediaPool().RelinkClips([clip], str(destination.parent)) or not same_file(clip.GetClipProperty('File Path'), destination):
        raise ArchiveError('整理后的副本已保留，重链接稍后重试。')
    record['status'] = 'relinked'
    write_json(root / '.magic-archive-history.json', records)
    return True


def cleanup_legacy_directories(manager, project, root, records, identity, database, cancelled):
    pending = [record for record in records.values() if isinstance(record, dict) and record.get('legacyDestination') and not record.get('legacyCleanupComplete') and not record.get('legacyBackup')]
    if not pending:
        return
    referenced = set()
    for clip in clips_in(project.GetMediaPool().GetRootFolder()):
        raw = clip.GetClipProperty('File Path')
        if raw:
            referenced.add(Path(raw).resolve().parent)
    folders = {Path(record['legacyDestination']).parent for record in pending}
    for folder in folders:
        if folder in referenced or not is_within(folder, [root]):
            continue
        members = [record for record in pending if Path(record['legacyDestination']).parent == folder]
        if not all(record.get('status') in {'relinked', 'archived', 'removed'} for record in members):
            continue
        category = members[0]['category']
        if not all(legacy_path(record, root) is not None for record in members):
            continue
        backup_parent = safe_directory(root, '.magic-archive-legacy/' + category)
        backup = backup_parent / folder.name
        if folder.is_dir() and not folder.is_symlink():
            names = {Path(record['legacyDestination']).name for record in members} | {'.DS_Store'}
            removed_only = all(record.get('status') == 'removed' for record in members)
            if any((child.name not in names and not (removed_only and child.name.startswith('.smbdelete'))) or child.is_symlink() or child.is_dir() for child in folder.iterdir()):
                continue
            if any(record.get('status') != 'removed' and (not Path(record['legacyDestination']).is_file() or digest(record['legacyDestination'], cancelled) != record['sha256']) for record in members):
                continue
            if backup.exists() or cancelled() or not same_project(manager, identity, database):
                continue
            os.rename(folder, backup)
        if backup.is_dir():
            for record in members:
                if record.get('status') == 'removed':
                    record['legacyBackupDirectory'] = str(backup)
                else:
                    record['legacyBackup'] = str(backup / Path(record['legacyDestination']).name)
                record['legacyCleanupComplete'] = True
            write_json(root / '.magic-archive-history.json', records)
        elif not folder.exists() and all(record.get('status') == 'removed' for record in members):
            for record in members:
                record['legacyCleanupComplete'] = True
            write_json(root / '.magic-archive-history.json', records)


def organize_unreferenced_legacy(manager, project, root, records, identity, database, cancelled):
    referenced = set()
    for clip in clips_in(project.GetMediaPool().GetRootFolder()):
        raw = clip.GetClipProperty('File Path')
        if raw:
            referenced.add(Path(raw).resolve())
    for record in records.values():
        if not isinstance(record, dict):
            continue
        old = legacy_path(record, root)
        if old is None or Path(record.get('destination', '')) != old or old in referenced or record.get('status') == 'removed':
            continue
        if cancelled() or not same_project(manager, identity, database):
            return
        if not old.exists():
            category_root = root / '3_素材' / record['category']
            if category_root.is_dir():
                safe_directory(root, '3_素材/' + record['category'] + '/归档')
            record.update({'legacyDestination': str(old), 'status': 'removed', 'layoutVersion': 2})
            write_json(root / '.magic-archive-history.json', records)
            continue
        if digest(old, cancelled) != record.get('sha256'):
            raise ArchiveError('旧归档校验失败，未整理：' + old.name)
        copied = verified_copy(old, root, record['category'], cancelled=cancelled)
        if cancelled() or not same_project(manager, identity, database):
            return
        record.update({'legacyDestination': str(old), 'destination': copied['destination'], 'status': 'archived', 'layoutVersion': 2})
        write_json(root / '.magic-archive-history.json', records)


def scan_once(resolve, config, cache=None):
    """One read-only preview, serialized with other ordina scan requests."""
    from archive_plan import run, scan_if_changed
    request = dict(config, action='archive_scan')
    state_directory = config.get('_scanStateDirectory')
    if not state_directory:
        return run(resolve, request)
    with scan_lock(state_directory):
        return scan_if_changed(resolve, request, cache) if cache is not None else run(resolve, request)


@contextmanager
def scan_lock(state_directory):
    directory = Path(state_directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.scan-read.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArchiveError('正在扫描素材，请稍后重试。')
        yield


class AutomaticArchive:
    """Process one explicitly eligible clip after the timeline settles."""

    def __init__(self):
        self.playhead = None
        self.still_since = 0
        self.attempted = set()
        self.project = None

    @staticmethod
    def position(resolve, scan):
        project = resolve.GetProjectManager().GetCurrentProject()
        if project is None or project.GetUniqueId() != scan['projectID'] or project.IsRenderingInProgress():
            return None
        timeline = project.GetCurrentTimeline()
        if not timeline:
            return None
        timecode = timeline.GetCurrentTimecode()
        return (timeline.GetUniqueId(), timecode) if timecode else None

    def advance(self, resolve, scan, state_directory, enabled):
        from archive_plan import execute
        if not scan or not enabled():
            self.playhead = None
            return None
        project = (scan['projectID'], scan['database'], scan['projectRoot'])
        if self.project != project:
            self.project = project
            self.attempted.clear()
            self.playhead = None
        candidates = [(item, (item['id'], tuple(item.get('signature') or []))) for item in scan['items']
                      if item.get('classification') == 'pending' and item.get('method') == 'copy'
                      and not item.get('retainedCopy')]
        self.attempted.intersection_update(key for _, key in candidates)
        next_item = next(((item, key) for item, key in candidates if key not in self.attempted), None)
        if next_item is None:
            self.playhead = None
            return None
        position = self.position(resolve, scan)
        now = time.monotonic()
        if position is None or position != self.playhead:
            self.playhead, self.still_since = position, now
            return None
        if now - self.still_since < 3:
            return None
        item, key = next_item
        next_check = now
        safe = True

        def permitted():
            nonlocal next_check, safe
            if not running:
                return False
            current = time.monotonic()
            if current >= next_check:
                safe = safe and enabled() and self.position(resolve, scan) == position
                next_check = current + 1
            return safe

        request = {name: scan[name] for name in ('projectID', 'database', 'projectRoot')}
        request.update(preview=scan['snapshot'], selectedIDs=[item['id']])
        with scan_lock(state_directory):
            self.attempted.add(key)
            emit(status='正在自动归档…')
            results = execute(resolve, request, permitted=permitted)['results']
        if not safe:
            self.playhead = None
            if results and not results[0].get('destination'):
                self.attempted.discard(key)
        return results[0] if results else None


def watch(resolve, config, connect=None):
    from archive_plan import CLASSIFICATIONS
    parent = os.getppid()
    cache = {}
    automatic_archive = AutomaticArchive()
    latest_scan = None
    while keep_running(parent):
        if service_config is not None:
            config = read_config(service_config)
            config['_scanStateDirectory'] = str(Path(service_config).parent)
        seconds = scan_interval_seconds(config)
        try:
            if connect is not None:
                resolve = connect()
            result = scan_once(resolve, config, cache=cache) if service_config is not None else scan_once(resolve, config)
            if result is not None:
                latest_scan = result
                counts = {kind: sum(item['classification'] == kind for item in result['items'])
                          for kind in CLASSIFICATIONS}
                issues = [item['name'] + '：' + item['warning'] for item in result['items'] if item['warning']]
                emit(status='监测中', project=result['projectName'], archived=counts['archived'],
                     skipped=sum(counts[kind] for kind in ('raw', 'shared', 'trusted')),
                     pending=counts['pending'], classifications=counts, scan=result, issues=issues[:50],
                     nextScanAt=time.time() + seconds, scanIntervalSeconds=seconds)
            if service_config is not None:
                def enabled():
                    return keep_running(parent) and read_config(service_config).get('automaticArchive', True) is True
                archived = automatic_archive.advance(resolve, latest_scan, config['_scanStateDirectory'], enabled)
                if archived:
                    cache.clear()
                    if archived['actualResult'] == 'relinked':
                        emit(status='已自动归档', issues=[])
                    else:
                        message = archived.get('warning') or '自动归档未完成，请查看素材状态。'
                        emit(status=message, issues=[message])
        except Exception as error:
            cache.clear()
            latest_scan = None
            emit(status=str(error), issues=[str(error)], nextScanAt=time.time() + seconds,
                 scanIntervalSeconds=seconds)
        completed = time.monotonic()
        completed_at = time.time()
        while keep_running(parent):
            current = read_config(service_config) if service_config is not None else config
            updated = scan_interval_seconds(current)
            if updated != seconds:
                seconds = updated
                emit(nextScanAt=completed_at + seconds, scanIntervalSeconds=seconds)
            remaining = completed + seconds - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))


def main():
    global service_config, service_instance
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if len(sys.argv) > 1 and sys.argv[1] in {'--once', '--archive-action'}:
        try:
            request = json.load(sys.stdin)
            sys.path.insert(0, '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules')
            import DaVinciResolveScript as api
            resolve = api.scriptapp('Resolve')
            if sys.argv[1] == '--once':
                data = scan_once(resolve, request)
            else:
                from archive_plan import require, run
                require(request.get('action') in {'archive_execute', 'storage_update'}, '无效的归档操作。')
                with scan_lock(request['_scanStateDirectory']):
                    data = run(resolve, request)
                    try:
                        data['freshScan'] = scan_once(resolve, dict(request, _scanStateDirectory=None))
                    except Exception:
                        pass
            response = {'ok': True, 'data': data}
        except Exception as error:
            response = {'ok': False, 'error': str(error)}
        print('MAGIC_ARCHIVE_SCAN:' + json.dumps(response, ensure_ascii=False), flush=True)
        return
    if len(sys.argv) >= 4 and sys.argv[1] == '--service':
        service_config = Path(sys.argv[2])
        service_instance = sys.argv[3]
        config = read_config(service_config)
    else:
        config = json.load(sys.stdin)
    sys.path.insert(0, '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules')
    import DaVinciResolveScript as api
    watch(None, config, connect=lambda: api.scriptapp('Resolve'))


if __name__ == '__main__':
    main()
