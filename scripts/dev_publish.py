#!/usr/bin/env python3
"""ordina developer publishing engine.

This is the local tool behind the in-app developer centre. It owns every side
effect; the Swift client only collects parameters and renders the event stream.
The existing stage-A transaction engine and stage-B plugin protocol are reused
unchanged.

Contract (stable, versioned, one JSON object per line on stdout):

    {"type":"event","event":"stage","stage":"preflight","state":"running"}
    {"type":"event","event":"log","message":"..."}
    {"type":"result","ok":true,"data":{...}}
    {"type":"result","ok":false,"error":{"code","message","stage","journal"}}

`protocol` is bumped only for an incompatible change. Unknown keys are additive
and must be ignored by clients. Nothing human-readable is ever required to
interpret a result, and no credential material is emitted.

Commands
    hello, doctor, credential-status, credential-import,
    inspect, local-test, preview, publish, status, resume, rollback, history

Exit codes: 0 success, 1 tool failure, 2 usage error.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import build_feeds
import dev_credentials
import dev_zip
import durable_release
import plugin_protocol as protocol
import plugin_lifecycle
import publish_plugin
from release_transaction import LocalGateway, ReleaseError

TOOL = 'ordina-publisher'
TOOL_VERSION = '1.6.0'
PROTOCOL = 1
MIN_CLIENT = '1.10.0'
MAX_NOTES = 2000
STAGES = ('preflight', 'asset', 'gate', 'feeds', 'push', 'postflight')
FEATURES = ('zip-input', 'local-test', 'preview', 'publish', 'journal',
            'resume', 'rollback', 'history', 'repository-setup', 'tool-update', 'plugin-lifecycle')


class Failure(Exception):
    def __init__(self, code, message, stage=None, journal=None, details=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.journal = journal
        self.details = details or {}


def emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=False,
                                separators=(',', ':')) + '\n')
    sys.stdout.flush()


def event(name, **payload):
    emit(dict({'type': 'event', 'event': name}, **payload))


def log(message):
    event('log', message=message)


def ok(data):
    emit({'type': 'result', 'ok': True, 'data': data})


def fail(error):
    payload = {'code': error.code, 'message': str(error)}
    if error.stage:
        payload['stage'] = error.stage
    if error.journal:
        payload['journal'] = str(error.journal)
    if error.details:
        payload['details'] = error.details
    emit({'type': 'result', 'ok': False, 'error': payload})


# ---------------------------------------------------------------- environment

def env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, '') else default


def lab_enabled():
    return os.environ.get('ORDINA_DEV_LAB') == '1'


def configure(root):
    """Point the shared engines at the configured repository.

    The engines compute their paths from their own file location, which is
    wrong once the tool is packaged as a standalone archive, so every path is
    rebound explicitly here. Nothing else about the engines changes.
    """
    if root is None:
        return None
    source = root / 'plugins' / 'registry.source.json'
    publish_plugin.ROOT = root
    publish_plugin.SOURCE_PATH = source
    # build_feeds derives every feed from the registry source and resolves its
    # own paths from its file location, which is the directory holding the
    # packaged archive once the tool is a .pyz.
    build_feeds.ROOT = root
    build_feeds.SOURCE = source
    build_feeds.REGISTRY_OUTPUT = root / 'registry.json'
    build_feeds.LEGACY_OUTPUT = root / 'plugins' / 'manifest.json'
    durable_release.TRANSACTIONS = root / '.transactions'
    durable_release.LAB_REPORT = root / 'docs' / 'validation' / 'lab-release.json'
    return root


def repository_root(value):
    root = Path(value).expanduser().resolve() if value else None
    if root is None:
        raise Failure('repo.missing', '未配置发布仓库路径。')
    if not (root / 'plugins' / 'registry.source.json').is_file():
        raise Failure('repo.invalid', '不是 ordina 发布仓库：' + str(root))
    return configure(root)


def git(root, *arguments):
    result = subprocess.run(['git', *arguments], cwd=root, capture_output=True,
                            text=True)
    if result.returncode:
        raise Failure('repo.git', (result.stderr or result.stdout).strip()[:400])
    return result.stdout.strip()


def repo_state(root):
    try:
        branch = git(root, 'branch', '--show-current')
        head = git(root, 'rev-parse', '--short', 'HEAD')
        dirty = bool(git(root, 'status', '--porcelain'))
    except Failure:
        return {'path': str(root), 'valid': False, 'branch': None, 'head': None,
                'clean': False}
    return {'path': str(root), 'valid': True, 'branch': branch, 'head': head,
            'clean': not dirty}


def journal_directory(root=None, value=None):
    if value:
        return Path(value).expanduser().resolve()
    if env('ORDINA_DEV_JOURNAL_DIR'):
        return Path(env('ORDINA_DEV_JOURNAL_DIR')).expanduser().resolve()
    if root is not None:
        return root / '.transactions'
    raise Failure('journal.missing', '未配置事务目录。')


def records_path(journal_dir, value=None):
    if value:
        return Path(value).expanduser().resolve()
    if env('ORDINA_DEV_RECORDS'):
        return Path(env('ORDINA_DEV_RECORDS')).expanduser().resolve()
    return Path(journal_dir) / 'records.jsonl'


def client_version():
    return env('ORDINA_DEV_CLIENT_VERSION', MIN_CLIENT)


def supports_client(client, minimum):
    """Version comparison local to this tool; the client re-checks anyway."""
    if not (protocol.valid_version(client) and protocol.valid_version(minimum)):
        return False

    def parts(value):
        return tuple(map(int, (value.split('.') + ['0'] * 4)[:4]))

    return parts(client) >= parts(minimum)


def append_record(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record, at=int(time.time()), tool=TOOL, toolVersion=TOOL_VERSION)
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')


def read_records(path, limit=50):
    path = Path(path)
    if not path.is_file():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()[-limit:]
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(result))


# ------------------------------------------------------------------- packages

def package_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_package(zip_path, plugin_id=None, root=None):
    """Extract into quarantine and validate exactly as a release would."""
    root = root or environment_repository(required=False)
    with tempfile.TemporaryDirectory(prefix='ordina-dev-inspect-') as temporary:
        extracted = dev_zip.extract(zip_path, Path(temporary) / 'package')
        meta = publish_plugin.plugin_meta(extracted)
        name = meta['id']
        if plugin_id and plugin_id != name:
            raise Failure('package.id', f'安装包标识 {name} 与请求的 {plugin_id} 不符。')
        data, version, files = publish_plugin.build_bundle(extracted, expected_id=name)
        # A ZIP always ships as a bundle; plugin.json itself has no kind field,
        # and the descriptor must agree with the release the client will unpack.
        descriptor = protocol.descriptor(name, dict(meta, kind='bundle'),
                                         version=version, files=files)
        protocol.validate_descriptor(name, descriptor)
        protocol.validate_files(name, 'bundle', files)
        inventory = dev_zip.inventory(extracted, meta)

    known = publish_plugin.load_source() if root is not None else {}
    existing = known.get(name)
    previous = existing.get('version') if existing else None
    if existing is None:
        action = 'new'
    elif previous == version:
        action = 'same'
    elif previous and tuple(map(int, version.split('.'))) < \
            tuple(map(int, previous.split('.'))):
        action = 'downgrade'
    else:
        action = 'update'
    minimum = descriptor['minClientVersion']
    reasons = []
    if not supports_client(client_version(), minimum):
        reasons.append(f'需要 ordina {minimum} 或更高版本')
    if descriptor['protocol'] != protocol.PROTOCOL:
        reasons.append('插件协议版本不受支持')
    repository = (existing or {}).get('repository') or f'BrOKenAG/ordina-plugin-{name}'
    return {
        'package': {
            'id': name, 'name': descriptor['name'], 'summary': descriptor['summary'],
            'symbol': descriptor['symbol'], 'version': version, 'kind': 'bundle',
            'protocol': descriptor['protocol'], 'minClientVersion': minimum,
            'capabilities': descriptor['capabilities'],
            'fileCount': len(files), 'assetSize': len(data),
            'assetSha256': hashlib.sha256(data).hexdigest(),
            'zipSha256': package_digest(zip_path),
            'files': inventory,
        },
        'action': action,
        'existing': ({'version': previous, 'repository': existing.get('repository'),
                      'published': bool(existing.get('published'))}
                     if existing else None),
        'compatibility': {
            'ok': not reasons, 'reasons': reasons,
            'clientVersion': client_version(), 'required': minimum,
        },
        'repository': {'slug': repository, 'known': existing is not None},
        'descriptor': descriptor,
        'release': {'version': version, 'kind': 'bundle', 'files': files,
                    'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)},
    }


def prepare_zip(zip_path, notes, plugin_id=None, root=None, require_increase=True):
    root = root or environment_repository(required=True)
    with tempfile.TemporaryDirectory(prefix='ordina-dev-release-') as temporary:
        extracted = dev_zip.extract(zip_path, Path(temporary) / 'package')
        meta = publish_plugin.plugin_meta(extracted)
        if plugin_id and plugin_id != meta['id']:
            raise Failure('package.id',
                          f'安装包标识 {meta["id"]} 与请求的 {plugin_id} 不符。')
        try:
            return publish_plugin.prepare_release(
                meta['id'], extracted, notes=notes,
                plugins=publish_plugin.load_source(),
                require_version_increase=require_increase)
        except ValueError as error:
            raise Failure('release.invalid', str(error))


def restrict_feeds(prepared, backend):
    """In a lab run only the released plugin's own feeds are written.

    Production publishes every derived feed because all of them live in
    allowlisted repositories. The isolated backend only accepts lab-shaped
    repositories, so the plan is narrowed to the feed under test; the main
    repository feeds (registry, legacy, source) are always included.
    """
    if backend != 'lab':
        return prepared
    main = {'registry', 'legacy', 'source'}
    prepared.plan.feeds = [
        feed for feed in prepared.plan.feeds
        if feed.label in main or feed.label.endswith(':' + prepared.name)
    ]
    return prepared


def environment_repository(required=True):
    value = env('ORDINA_DEV_REPO')
    if not value:
        if required:
            raise Failure('repo.missing', '未配置发布仓库路径。')
        return None
    return repository_root(value)


# ------------------------------------------------------------------ backends

def lab_root():
    value = env('ORDINA_DEV_LAB_ROOT')
    if not value:
        raise Failure('backend.lab', '隔离环境需要显式配置 ORDINA_DEV_LAB_ROOT。')
    return Path(value).expanduser().resolve()


class AuthorizedBackend:
    """Repeat the authorization gate before each remote mutation."""
    def __init__(self, backend, authorize):
        self.backend = backend
        self.authorize = authorize

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def acquire(self, token):
        self.authorize()
        return self.backend.acquire(token)

    def release(self, token):
        self.authorize()
        return self.backend.release(token)

    def write(self, repo, expected, data, token):
        self.authorize()
        return self.backend.write(repo, expected, data, token)


class AuthorizedGateway(LocalGateway):
    def __init__(self, authorize):
        self.authorize = authorize

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        self.authorize()
        return super().create_release(repository, tag, asset_path, title, notes, latest)


def build_backend(kind, staged, plan, root, authorize=None):
    if kind == 'lab':
        if not lab_enabled():
            raise Failure('backend.lab', '隔离后端只允许在隔离环境使用。')
        try:
            import dev_lab_backend
        except ImportError:
            raise Failure('backend.lab', '当前发布工具未包含隔离后端。')
        shared = lab_root()
        return dev_lab_backend.LabBackend(shared), dev_lab_backend.LabGateway(shared)
    if authorize is None:
        raise Failure('auth.missing', '生产事务缺少权限验证。')
    return AuthorizedBackend(durable_release.production_backend(staged, plan), authorize), AuthorizedGateway(authorize)


def credential(options, repository, scope='plugin.publish'):
    path = options.credentials or env('ORDINA_DEV_CREDENTIALS')
    try:
        document, source = dev_credentials.load(path, scope=scope,
                                                repository=repository)
    except dev_credentials.CredentialError as error:
        raise Failure(error.code, str(error))
    return document, source


def production_authorize(options, repository, allow_create=False):
    """Recheck signed delegation, GitHub write permission and pinned signing key."""
    if lab_enabled() or env('ORDINA_PLUGIN_LAB') == '1':
        raise Failure('backend.lab', '隔离授权不能用于生产写入。')
    import re
    if not re.fullmatch(r'BrOKenAG/ordina-plugin-[a-z][a-z0-9-]{0,31}', repository or ''):
        raise Failure('auth.repository', '发布目标不在官方仓库范围。')
    credential(options, repository)
    for slug in ('BrOKenAG/ordina-releases', repository):
        result = None
        try:
            result = subprocess.run(['gh', 'api', f'repos/{slug}'], capture_output=True,
                                    text=True, timeout=15)
            info = json.loads(result.stdout) if result.returncode == 0 else {}
            permissions = info.get('permissions') if isinstance(info, dict) else None
            permitted = isinstance(permissions, dict) and permissions.get('push') is True
        except (OSError, ValueError, subprocess.SubprocessError):
            permitted = False
        if not permitted:
            # Repository creation requires a separate explicit scope and the
            # signed-in official owner; all other failures remain fail-closed.
            if allow_create and slug == repository and result is not None and result.returncode != 0 and \
                    ('HTTP 404' in result.stderr or 'Not Found' in result.stderr):
                credential(options, repository, scope='repository.create')
                try:
                    user = subprocess.run(['gh', 'api', 'user', '--jq', '.login'],
                                          capture_output=True, text=True, timeout=15)
                    if user.returncode == 0 and user.stdout.strip().lower() == 'brokenag':
                        continue
                except (OSError, subprocess.SubprocessError):
                    pass
            raise Failure('auth.github', f'无法确认 {slug} 的 GitHub 写入权限。')
    try:
        from feed_signing import sign_document
        sign_document({'purpose': 'ordina-publish-authorization',
                       'nonce': secrets.token_hex(32)})
    except (ValueError, OSError, subprocess.SubprocessError, ImportError) as error:
        raise Failure('auth.signing', '无法验证可信发布签名能力。') from error


def authorize_repositories(options, repositories):
    plugins = sorted(set(repositories) - {'BrOKenAG/ordina-releases'})
    if not plugins:
        raise Failure('auth.repository', '生产事务缺少插件发布仓库。')
    for repository in plugins:
        production_authorize(options, repository)


def authorize_direct_plugin_write(repositories):
    """Guard legacy/direct Python entry points with the same production checks."""
    if lab_enabled():
        raise Failure('backend.lab', '隔离授权不能用于生产写入。')
    from types import SimpleNamespace
    authorize_repositories(SimpleNamespace(credentials=None), repositories)


def repository_state(slug, kind):
    if kind == 'lab':
        root = lab_root()
        return {'exists': (root / 'lab' / 'assets' / slug).is_dir(),
                'slug': slug, 'source': 'lab'}
    result = subprocess.run(['gh', 'api', f'repos/{slug}'], capture_output=True,
                            text=True)
    if result.returncode == 0:
        return {'exists': True, 'slug': slug, 'source': 'github'}
    if 'HTTP 404' in result.stderr or 'Not Found' in result.stderr:
        return {'exists': False, 'slug': slug, 'source': 'github'}
    return {'exists': None, 'slug': slug, 'source': 'github',
            'error': '无法确认仓库状态：' + result.stderr.strip()[:300]}


def repository_steps(slug, name):
    return [
        {'id': 'create', 'title': '创建公开仓库',
         'command': f'gh repo create {slug} --public --add-readme'},
        {'id': 'pages', 'title': '启用 GitHub Pages（main 分支根目录）',
         'command': f'gh api -X POST repos/{slug}/pages -f source[branch]=main '
                    f'-f source[path]=/'},
        {'id': 'registry', 'title': '在 registry.source.json 中新增插件条目',
         'command': '由发布事务自动完成'},
    ]


def create_repository(slug, name, kind):
    if kind == 'lab':
        (lab_root() / 'lab' / 'assets' / slug).mkdir(parents=True, exist_ok=True)
        return {'created': True, 'slug': slug, 'source': 'lab'}
    result = subprocess.run(
        ['gh', 'repo', 'create', slug, '--public', '--add-readme',
         '--description', f'ordina plugin: {name}'],
        capture_output=True, text=True)
    if result.returncode:
        raise Failure('repository.create', '创建仓库失败：' +
                      (result.stderr or result.stdout).strip()[:300])
    pages = subprocess.run(
        ['gh', 'api', '-X', 'POST', f'repos/{slug}/pages',
         '-f', 'source[branch]=main', '-f', 'source[path]=/'],
        capture_output=True, text=True)
    if pages.returncode:
        raise Failure('repository.pages', '启用 Pages 失败：' +
                      pages.stderr.strip()[:300])
    return {'created': True, 'slug': slug, 'source': 'github'}


# -------------------------------------------------------------------- preview

def file_diff(old, new):
    old_map = {item['path']: item for item in old or []}
    new_map = {item['path']: item for item in new or []}
    added = sorted(set(new_map) - set(old_map))
    removed = sorted(set(old_map) - set(new_map))
    changed = sorted(path for path in set(old_map) & set(new_map)
                     if old_map[path].get('sha256') != new_map[path].get('sha256'))
    return {'added': added, 'removed': removed, 'changed': changed,
            'unchanged': sorted(set(old_map) & set(new_map) -
                                set(changed))}


def feed_plan(prepared, root):
    result = []
    for target in prepared.plan.feeds:
        entry = {'label': target.label, 'bytes': len(target.content)}
        if target.repository:
            entry.update({'repository': target.repository,
                          'path': target.remote_path, 'action': 'update'})
        elif target.path is not None:
            try:
                relative = str(target.path.relative_to(root))
            except ValueError:
                relative = str(target.path)
            current = target.path.read_bytes() if target.path.is_file() else None
            entry.update({'repository': None, 'path': relative,
                          'action': 'unchanged' if current == target.content
                                    else ('create' if current is None else 'update')})
        result.append(entry)
    return result


def fingerprint(prepared, zip_path, notes, root, client):
    material = {
        'zip': package_digest(zip_path), 'version': prepared.version,
        'notes': notes or '', 'tool': TOOL, 'toolVersion': TOOL_VERSION,
        'protocol': PROTOCOL, 'client': client,
        'registry': hashlib.sha256(
            (root / 'plugins' / 'registry.source.json').read_bytes()).hexdigest(),
        'asset': hashlib.sha256(prepared.data).hexdigest(),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def build_preview(options, root):
    zip_path = Path(options.zip).expanduser()
    notes = (options.notes or '').strip()
    inspected = inspect_package(zip_path, options.plugin_id, root)
    prepared = restrict_feeds(prepare_zip(zip_path, notes, options.plugin_id, root,
                                         require_increase=False), options.backend)
    old_files = (inspected['existing'] and
                 (publish_plugin.load_source().get(inspected['package']['id']) or {})
                 .get('files')) or []
    checks = []

    def check(identifier, condition, message, confirm=False):
        checks.append({'id': identifier, 'ok': bool(condition), 'message': message,
                       'confirm': bool(confirm)})

    check('protocol', inspected['compatibility']['ok'],
          '插件协议与客户端兼容' if inspected['compatibility']['ok']
          else '；'.join(inspected['compatibility']['reasons']))
    check('version', inspected['action'] in ('new', 'update'),
          {'new': '新增插件', 'update': '版本递增',
           'same': '版本与线上相同，不会重复发布',
           'downgrade': '目标版本低于线上版本'}[inspected['action']])
    check('files', bool(prepared.files), f'{len(prepared.files)} 个文件已通过协议校验')
    check('notes', len(notes) <= MAX_NOTES,
          '更新说明已填写' if notes else '未填写更新说明（将使用默认说明）')
    try:
        credential(options, prepared.target['repository'])
        check('credential', True, '开发者授权有效')
        credential_error = None
    except Failure as error:
        credential_error = error
        check('credential', False, str(error))
    state = repository_state(prepared.target['repository'], options.backend)
    # Creating a repository for a brand-new plugin is an explicit confirmation
    # (publish --create-repository), not a blocker: the preview lists the exact
    # steps that will run and the fingerprint covers that decision.
    check('repository', state['exists'] is not False,
          '目标仓库已存在' if state['exists'] is True
          else ('无法确认目标仓库状态，发布前会再次检查' if state['exists'] is None
                else '目标仓库尚未创建，发布前需确认创建'),
          confirm=state['exists'] is False)
    if options.backend == 'production':
        repo = repo_state(root)
        check('worktree', repo['clean'] and repo['branch'] == 'main',
              '工作区干净且位于 main 分支' if repo['clean'] and repo['branch'] == 'main'
              else '发布仓库需要位于 main 分支且工作区干净')
        check('lab-report', durable_release.lab_report_valid(),
              '隔离环境验证报告有效' if durable_release.lab_report_valid()
              else '缺少有效的隔离环境验证报告')
    if credential_error is not None:
        # Authorisation is enforced by the tool, not by the hidden UI: without a
        # valid credential and the publish scope there is no preview to run.
        raise credential_error
    diff = file_diff(old_files, prepared.files)
    blockers = [item for item in checks
                if not item['ok'] and not item.get('confirm')]
    return {
        'preview': {
            'plugin': {'id': prepared.name, 'name': prepared.target.get('name'),
                       'version': prepared.version,
                       'previousVersion': prepared.previous_version,
                       'repository': prepared.target['repository'],
                       'action': inspected['action'],
                       'tag': prepared.plan.tag,
                       'releaseNotes': prepared.plan.notes},
            'asset': {'name': prepared.target['asset'], 'size': len(prepared.data),
                      'sha256': hashlib.sha256(prepared.data).hexdigest()},
            'files': diff, 'fileCount': len(prepared.files),
            'capabilities': prepared.target['capabilities'],
            'compatibility': inspected['compatibility'],
            'feeds': feed_plan(prepared, root),
            'registry': {'before': inspected['existing'],
                         'after': {'version': prepared.version,
                                   'size': len(prepared.data)}},
            'checks': checks,
            'blockers': [{'code': item['id'], 'message': item['message']}
                         for item in blockers],
            'confirmation': any(item.get('confirm') for item in checks),
            'repository': state,
            'repositorySteps': repository_steps(prepared.target['repository'],
                                                prepared.name),
            'fingerprint': fingerprint(prepared, zip_path, notes, root,
                                       client_version()),
        },
    }


# ------------------------------------------------------------------- journals

def meta_path(journal):
    return Path(str(journal) + '.meta.json')


def write_meta(journal, prepared, notes):
    meta = {'format': 1, 'plugin': prepared.name,
            'name': prepared.target.get('name'), 'version': prepared.version,
            'previousVersion': prepared.previous_version,
            'repository': prepared.target['repository'], 'tag': prepared.plan.tag,
            'notes': notes[:MAX_NOTES], 'createdAt': int(time.time()),
            'tool': TOOL, 'toolVersion': TOOL_VERSION}
    meta_path(journal).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                  encoding='utf-8')
    return meta


def journal_summary(path):
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    meta = {}
    if meta_path(path).is_file():
        try:
            meta = json.loads(meta_path(path).read_text(encoding='utf-8'))
        except ValueError:
            meta = {}
    repositories = document.get('repositories', {})
    return {
        'journal': str(path), 'status': document.get('status'),
        'transaction': document.get('plan', {}).get('id'),
        'plugin': meta.get('plugin'), 'name': meta.get('name'),
        'version': meta.get('version'), 'previousVersion': meta.get('previousVersion'),
        'repository': meta.get('repository'), 'tag': meta.get('tag'),
        'notes': meta.get('notes'), 'createdAt': meta.get('createdAt'),
        'toolVersion': meta.get('toolVersion'),
        'repositories': {repo: {'before': item.get('before_head'),
                                'after': item.get('after_head'),
                                'rolledBack': item.get('rollback_head')}
                         for repo, item in repositories.items()},
        'resumable': document.get('status') in ('preparing', 'publishing'),
        'rollbackable': document.get('status') in ('preparing', 'publishing', 'rolling_back', 'complete'),
    }


def all_journals(directory):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob('*.json')):
        if path.name.endswith('.meta.json'):
            continue
        summary = journal_summary(path)
        if summary:
            result.append(summary)
    return sorted(result, key=lambda item: item.get('createdAt') or 0, reverse=True)


def backend_for_journal(options, path):
    """Rebuild the backend that wrote a journal, in the same environment."""
    if options.backend == 'lab':
        import dev_lab_backend
        document = json.loads(Path(path).read_text(encoding='utf-8'))
        durable = document.get('plan')
        if not isinstance(durable, dict) or not isinstance(durable.get('feeds'), dict):
            raise Failure('journal.invalid', '事务记录缺少有效的发布计划。')
        return dev_lab_backend.LabBackend(lab_root()), durable, document
    return durable_release.backend_from_journal(path)


def load_journal(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise Failure('journal.missing', '找不到事务记录：' + str(path))
    return path


# ------------------------------------------------------------------ commands

def command_hello(options):
    ok({'tool': TOOL, 'version': TOOL_VERSION, 'protocol': PROTOCOL,
        'minClientVersion': MIN_CLIENT, 'clientVersion': client_version(),
        'features': list(FEATURES), 'python': sys.version.split()[0],
        'lab': lab_enabled(), 'backend': options.backend})


def command_doctor(options):
    root = environment_repository(required=False)
    requirements = []

    def requirement(identifier, condition, message):
        requirements.append({'id': identifier, 'ok': bool(condition),
                             'message': message})

    requirement('python', sys.version_info >= (3, 9),
                f'Python {sys.version.split()[0]}')
    from feed_signing import signing_mode, sign_tool
    mode = signing_mode()
    if mode == 'lab-key':
        signing = True
    else:
        try:
            signing = sign_tool().is_file()
        except (ImportError, OSError):
            signing = False
    requirement('signing', signing,
                {'lab-key': '实验室签名密钥可用', 'keychain': '签名工具可用'}
                .get(mode, '缺少签名工具') if signing else '缺少签名工具')
    state = repo_state(root) if root else {'valid': False, 'path': None,
                                           'branch': None, 'head': None,
                                           'clean': False}
    requirement('repository', state['valid'],
                '发布仓库可用' if state['valid'] else '未配置发布仓库')
    credential_state = dev_credentials.status(options.credentials or
                                              env('ORDINA_DEV_CREDENTIALS'))
    requirement('credential', credential_state['valid'],
                '开发者授权有效' if credential_state['valid']
                else credential_state.get('message', '开发者授权无效'))
    # A standalone deployment may not have gh on PATH. That is a diagnostic
    # result, not a crash: doctor must still return a structured report.
    try:
        gh = subprocess.run(['gh', 'auth', 'status'], capture_output=True,
                            text=True, timeout=15)
        github_ok = gh.returncode == 0
    except (OSError, subprocess.SubprocessError):
        github_ok = False
    requirement('github', github_ok,
                'GitHub 授权可用' if github_ok else 'gh 未安装或未授权')
    journals = all_journals(journal_directory(root, options.journal_dir))
    pending = [item for item in journals if item['resumable']]
    ok({'tool': TOOL, 'version': TOOL_VERSION, 'protocol': PROTOCOL,
        'minClientVersion': MIN_CLIENT, 'clientVersion': client_version(),
        'features': list(FEATURES), 'python': sys.version.split()[0],
        'backend': options.backend, 'lab': lab_enabled(), 'signing': mode,
        'repository': state, 'credential': credential_state,
        'journals': {'total': len(journals), 'pending': len(pending)},
        'ready': all(item['ok'] for item in requirements),
        'requirements': requirements})


def command_inspect(options):
    root = environment_repository(required=False)
    ok(inspect_package(Path(options.zip).expanduser(), options.plugin_id, root))


def command_local_test(options):
    root = environment_repository(required=False)
    zip_path = Path(options.zip).expanduser()
    inspected = inspect_package(zip_path, options.plugin_id, root)
    prepared = prepare_zip(zip_path, '', options.plugin_id, root,
                           require_increase=False)
    directory = Path(options.work_dir).expanduser() if options.work_dir else \
        Path(tempfile.mkdtemp(prefix='ordina-local-test-'))
    directory.mkdir(parents=True, exist_ok=True)
    bundle = directory / f'{prepared.name}-{prepared.version}.json'
    bundle.write_bytes(prepared.data)
    descriptor = inspected['descriptor']
    release = {'version': prepared.version, 'kind': 'bundle',
               'url': 'local-test://' + prepared.name,
               'sha256': hashlib.sha256(prepared.data).hexdigest(),
               'size': len(prepared.data), 'descriptor': descriptor,
               'files': prepared.files}
    ok({'release': release, 'bundlePath': str(bundle),
        'fileCount': len(prepared.files), 'version': prepared.version,
        'plugin': prepared.name, 'name': descriptor['name'],
        'action': inspected['action'],
        'previousVersion': prepared.previous_version,
        'compatibility': inspected['compatibility']})


def command_preview(options):
    root = environment_repository(required=True)
    result = build_preview(options, root)
    ok(result['preview'])


def command_publish(options):
    if not options.execute:
        raise Failure('publish.confirm', '正式发布需要显式 --execute。')
    root = environment_repository(required=True)
    zip_path = Path(options.zip).expanduser()
    notes = (options.notes or '').strip()
    if len(notes) > MAX_NOTES:
        raise Failure('publish.notes', f'更新说明超过 {MAX_NOTES} 字。')
    inspected = inspect_package(zip_path, options.plugin_id, root)
    slug = inspected['repository']['slug']
    if options.backend == 'production':
        production_authorize(options, slug, allow_create=options.create_repository)
    prepared = restrict_feeds(prepare_zip(zip_path, notes, options.plugin_id, root),
                              options.backend)
    slug = prepared.target['repository']
    credential(options, slug)
    if options.backend == 'production' and not durable_release.lab_report_valid():
        raise Failure('publish.labreport', '缺少有效的隔离环境验证报告，已停止发布。')
    expected = options.expect_fingerprint
    if expected and expected != fingerprint(prepared, zip_path, notes, root,
                                            client_version()):
        raise Failure('preview.stale',
                      '安装包或配置在预览之后发生变化，请重新预览后再发布。')
    state = repository_state(slug, options.backend)
    if not state['exists']:
        if not options.create_repository:
            raise Failure('repository.missing', f'目标仓库 {slug} 不存在。',
                          details={'repository': slug,
                                   'steps': repository_steps(slug, prepared.name)})
        credential(options, slug, scope='repository.create')
        log(f'创建仓库 {slug}')
        create_repository(slug, prepared.name, options.backend)
        if options.backend == 'production':
            production_authorize(options, slug)

    journal_dir = journal_directory(root, options.journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ordina-dev-publish-') as temporary:
        tmp = Path(temporary)
        staged = durable_release.prepare(
            prepared.plan, f'plugin-{prepared.name}-v{prepared.version}',
            journal_dir=journal_dir)
        meta = write_meta(staged.journal, prepared, notes)
        repositories = set(staged.paths) | {slug}
        guard = (lambda: authorize_repositories(options, repositories)) if options.backend == 'production' else None
        backend, gateway = build_backend(options.backend, staged,
                                         prepared.plan, root, guard)

        def on_stage(name, state_name):
            event('stage', stage=name, state=state_name,
                  index=STAGES.index(name) + 1, total=len(STAGES))

        log(f'{prepared.plan.label} → {slug}')
        try:
            if options.backend == 'production':
                production_authorize(options, slug)
            result = durable_release.execute(prepared.plan, gateway, backend,
                                            staged, tmp, options.timeout,
                                            on_stage=on_stage)
        except ReleaseError as error:
            raise Failure('publish.failed', str(error), stage=error.stage,
                          journal=staged.journal,
                          details={'completed': list(error.completed),
                                   'recoverable': error.stage in
                                   ('feeds', 'push', 'postflight')})
    sync_worktree(root)
    append_record(records_path(journal_dir, options.records), {
        'kind': 'publish', 'plugin': prepared.name, 'version': prepared.version,
        'previousVersion': prepared.previous_version, 'repository': slug,
        'tag': prepared.plan.tag, 'journal': str(staged.journal),
        'status': 'complete', 'notes': notes[:MAX_NOTES],
    })
    ok({'journal': str(staged.journal), 'plugin': prepared.name,
        'version': prepared.version, 'repository': slug,
        'tag': prepared.plan.tag, 'stages': result['stages'],
        'asset': result['asset'], 'meta': meta,
        'records': str(records_path(journal_dir, options.records))})


# Main-repository files that also exist in the developer's working tree. The
# production backend writes them into the git worktree and commits them; the
# isolated backend keeps the same bytes in one blob, so they are replayed onto
# disk here. Otherwise the working copy would disagree with what was published.
WORKTREE_FEEDS = ('registry.json', 'plugins/manifest.json',
                  'plugins/registry.source.json', 'appcast.xml')


def sync_worktree(root):
    """Mirror committed main-repository feeds into the local working copy."""
    if not lab_enabled() or root is None:
        return
    if os.environ.get('ORDINA_DEV_LAB_WRITE_SOURCE') not in (None, '', '1'):
        return
    store = lab_root() / 'lab' / 'repos' / 'BrOKenAG' / 'ordina-releases' / 'state.json'
    if not store.is_file():
        return
    try:
        committed = json.loads(store.read_text(encoding='utf-8'))
    except ValueError:
        return
    for name in WORKTREE_FEEDS:
        value = committed.get(name)
        if not value:
            continue
        target = Path(root) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(value))


def command_status(options):
    if options.all:
        root = environment_repository(required=False)
        directory = journal_directory(root, options.journal_dir)
        ok({'journals': all_journals(directory)})
        return
    if not options.journal:
        raise Failure('journal.missing', '需要 --journal 或 --all。')
    summary = journal_summary(load_journal(options.journal))
    if summary is None:
        raise Failure('journal.invalid', '事务记录无法读取。')
    ok(summary)


def command_resume(options):
    path = load_journal(options.journal)
    summary = journal_summary(path) or {}
    if summary.get('status') == 'complete':
        raise Failure('journal.complete', '该事务已完成，无需恢复。')
    root = environment_repository(required=True)
    credential(options, summary.get('repository') or
               f'BrOKenAG/ordina-plugin-{summary.get("plugin")}')
    if options.backend == 'production':
        production_authorize(options, summary.get('repository') or
                             f'BrOKenAG/ordina-plugin-{summary.get("plugin")}')
    backend, durable, _ = backend_for_journal(options, path)
    if options.backend == 'production':
        repository = summary.get('repository') or f'BrOKenAG/ordina-plugin-{summary.get("plugin")}'
        repositories = set(durable['feeds']) | {repository}
        backend = AuthorizedBackend(backend, lambda: authorize_repositories(options, repositories))
    event('stage', stage='feeds', state='running', index=4, total=len(STAGES))
    try:
        durable_release.resume_journal(path, backend)
    except (ValueError, OSError, RuntimeError) as error:
        raise Failure('journal.resume', str(error), journal=path)
    sync_worktree(root)
    append_record(records_path(journal_directory(root, options.journal_dir),
                              options.records), {
        'kind': 'resume', 'plugin': summary.get('plugin'),
        'version': summary.get('version'), 'journal': str(path),
        'status': 'complete'})
    ok({'journal': str(path), 'status': 'complete',
        'plugin': summary.get('plugin'), 'version': summary.get('version')})


def command_rollback(options):
    path = load_journal(options.journal)
    summary = journal_summary(path) or {}
    if summary.get('status') == 'rolled_back':
        raise Failure('journal.rolledback', '该事务已回滚。')
    root = environment_repository(required=True)
    credential(options, summary.get('repository') or
               f'BrOKenAG/ordina-plugin-{summary.get("plugin")}')
    if options.backend == 'production':
        production_authorize(options, summary.get('repository') or
                             f'BrOKenAG/ordina-plugin-{summary.get("plugin")}')
    backend, durable, _ = backend_for_journal(options, path)
    if options.backend == 'production':
        repository = summary.get('repository') or f'BrOKenAG/ordina-plugin-{summary.get("plugin")}'
        repositories = set(durable['feeds']) | {repository}
        backend = AuthorizedBackend(backend, lambda: authorize_repositories(options, repositories))
    try:
        from durable_transaction import rollback
        rollback(path, backend)
    except (ValueError, OSError, RuntimeError) as error:
        raise Failure('journal.rollback', str(error), journal=path)
    sync_worktree(root)
    append_record(records_path(journal_directory(root, options.journal_dir),
                              options.records), {
        'kind': 'rollback', 'plugin': summary.get('plugin'),
        'version': summary.get('version'), 'journal': str(path),
        'status': 'rolled_back', 'reason': (options.reason or '')[:300]})
    ok({'journal': str(path), 'status': 'rolled_back',
        'plugin': summary.get('plugin'), 'version': summary.get('version'),
        'repositories': sorted(durable.get('feeds', {}))})


def command_history(options):
    root = environment_repository(required=False)
    directory = journal_directory(root, options.journal_dir)
    records = read_records(records_path(directory, options.records), options.limit)
    journals = all_journals(directory)[:options.limit]
    ok({'records': records, 'journals': journals})


def lifecycle_source(options, root):
    if options.backend == 'lab':
        return json.loads((root / 'plugins' / 'registry.source.json').read_text(encoding='utf-8'))
    try:
        result = subprocess.run(['gh', 'api', 'repos/BrOKenAG/ordina-releases/contents/plugins/registry.source.json'],
                                capture_output=True, text=True, timeout=20, check=True)
        item = json.loads(result.stdout)
        return json.loads(base64.b64decode(item['content'], validate=False))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise Failure('lifecycle.source', '无法读取官方插件目录。') from error


def lifecycle_preview(options, root):
    if options.published not in ('yes', 'no'):
        raise Failure('lifecycle.action', '请选择下架或重新上架。')
    source = lifecycle_source(options, root)
    try:
        change = plugin_lifecycle.plan(source, options.plugin_id, options.published == 'yes')
    except (ValueError, TypeError) as error:
        raise Failure('lifecycle.invalid', str(error)) from error
    credential(options, change['repository'])
    if options.backend == 'production':
        production_authorize(options, change['repository'])
        if not durable_release.lab_report_valid():
            raise Failure('publish.labreport', '缺少有效的隔离环境验证报告。')
    fingerprint = hashlib.sha256(json.dumps({'before': source, 'id': change['id'],
                                             'published': change['published']},
                                            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    meta = source['plugins'][change['id']]
    return {'id': change['id'], 'name': meta.get('name') or change['id'],
            'symbol': meta.get('symbol') or 'puzzlepiece.extension', 'version': change['version'],
            'published': change['published'], 'noop': change['noop'], 'fingerprint': fingerprint}


def command_lifecycle_list(options):
    root = environment_repository(required=True)
    source = lifecycle_source(options, root)
    result = []
    for plugin_id, meta in sorted(source.get('plugins', {}).items()):
        if meta.get('published') is not True and meta.get('everPublished') is not True:
            continue
        result.append({'id': plugin_id, 'name': meta.get('name') or plugin_id,
                       'symbol': meta.get('symbol') or 'puzzlepiece.extension',
                       'version': meta.get('version') or '', 'published': meta.get('published') is True})
    ok({'plugins': result})


def command_lifecycle_preview(options):
    root = environment_repository(required=True)
    ok(lifecycle_preview(options, root))


def command_lifecycle_set(options):
    if not options.execute or not options.expect_fingerprint:
        raise Failure('lifecycle.confirm', '请先预览并明确确认操作。')
    root = environment_repository(required=True)
    preview = lifecycle_preview(options, root)
    if preview['fingerprint'] != options.expect_fingerprint:
        raise Failure('preview.stale', '插件目录已变化，请重新预览。')
    if preview['noop']:
        ok({'journal': '', 'status': 'unchanged', 'plugin': preview['id'], 'version': preview['version']})
        return
    directory = journal_directory(root, options.journal_dir)
    if any(item.get('status') not in ('complete', 'rolled_back') for item in all_journals(directory)):
        raise Failure('journal.pending', '存在未完成事务，请先恢复或回滚。')
    source = lifecycle_source(options, root)
    change = plugin_lifecycle.plan(source, options.plugin_id, options.published == 'yes')
    # The preview fingerprint is rechecked after fetching the source for use.
    current = hashlib.sha256(json.dumps({'before': source, 'id': change['id'],
                                        'published': change['published']}, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
    if current != options.expect_fingerprint:
        raise Failure('preview.stale', '插件目录已变化，请重新预览。')
    target = source['plugins'][change['id']]
    url = build_feeds.entry(change['id'], target)['url']
    if options.backend == 'lab':
        import dev_lab_backend
        raw_backend = dev_lab_backend.LabBackend(lab_root())
        asset = raw_backend.asset_path(url).read_bytes()
    else:
        from github_production_backend import ProductionBackend
        asset = ProductionBackend._fetch_public(url)
    tid = 'lifecycle-' + change['id'] + '-' + secrets.token_hex(10)
    durable = plugin_lifecycle.prepare_transaction(change, asset, tid)
    paths = {repo: sorted(json.loads(raw)) for repo, raw in durable['feeds'].items()}
    if options.backend == 'lab':
        backend = raw_backend
    else:
        tag = f'plugin-{change["id"]}-v{change["version"]}'
        backend = ProductionBackend('BrOKenAG/ordina-releases', change['repository'], tag,
                                    target['asset'], paths, set(paths))
        backend = AuthorizedBackend(backend, lambda: authorize_repositories(options, set(paths)))
    durable['expected_heads'] = plugin_lifecycle.verify_transaction(durable, backend)
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / (tid + '.json')
    meta = {'format': 1, 'plugin': change['id'], 'name': preview['name'],
            'version': change['version'], 'repository': change['repository'],
            'tag': f'plugin-{change["id"]}-v{change["version"]}',
            'notes': '重新上架' if change['published'] else '下架',
            'createdAt': int(time.time()), 'toolVersion': TOOL_VERSION}
    meta_path(journal).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    try:
        import durable_transaction
        event('stage', stage='feeds', state='running', index=1, total=1)
        durable_transaction.publish(durable, journal, backend)
        event('stage', stage='feeds', state='ok', index=1, total=1)
    except Failure as error:
        raise Failure(error.code, str(error), stage='feeds', journal=journal) from error
    except (ValueError, OSError, RuntimeError) as error:
        raise Failure('lifecycle.failed', str(error), stage='feeds', journal=journal) from error
    sync_worktree(root)
    append_record(records_path(directory, options.records), {
        'kind': 'republish' if change['published'] else 'withdraw', 'plugin': change['id'],
        'version': change['version'], 'journal': str(journal), 'status': 'complete'})
    ok({'journal': str(journal), 'status': 'complete', 'plugin': change['id'],
        'version': change['version'], 'published': change['published']})


def command_auth_authorize(options):
    """Read-only proof of release authority; never grants the engine authority."""
    import re
    root = environment_repository(required=True)
    source = lifecycle_source(options, root)
    entries = source.get('plugins', {}) if isinstance(source, dict) else {}
    if not isinstance(entries, dict):
        raise Failure('auth.repository', '插件目录无效。')
    candidates = sorted({item.get('repository') for key, item in entries.items()
                         if isinstance(key, str) and isinstance(item, dict)
                         and item.get('repository') == 'BrOKenAG/ordina-plugin-' + key
                         and re.fullmatch(r'[a-z][a-z0-9-]{0,31}', key)})
    last_error = None
    for repository in candidates:
        try:
            if options.backend == 'production':
                if lab_enabled():
                    raise Failure('auth.lab', '隔离授权不能用于生产。')
                production_authorize(options, repository)
            else:
                if not lab_enabled():
                    raise Failure('auth.lab', '隔离环境未启用。')
                credential(options, repository)
                from feed_signing import sign_document
                sign_document({'purpose': 'ordina-lab-auth', 'nonce': secrets.token_hex(32)})
            ok({'authorized': True})
            return
        except (Failure, ValueError, OSError) as error:
            last_error = error
    if isinstance(last_error, Failure):
        raise last_error
    raise Failure('auth.unavailable', '无法确认发布权限及可信签名能力。')


def command_credential_status(options):
    ok(dev_credentials.status(options.credentials or env('ORDINA_DEV_CREDENTIALS')))


def command_credential_import(options):
    if not options.file:
        raise Failure('auth.file', '需要 --file 指向授权文件。')
    raw = Path(options.file).expanduser().read_text(encoding='utf-8').strip()
    try:
        document = dev_credentials.verify_raw(raw)
        dev_credentials.check(document)
    except dev_credentials.CredentialError as error:
        raise Failure(error.code, str(error))
    if lab_enabled():
        ok({'imported': True, 'source': 'lab-file',
            'developer': document['developer'],
            'expires': document['expires'], 'scopes': document['scopes']})
        return
    ok(dict(dev_credentials.store(raw), developer=document['developer'],
            expires=document['expires'], scopes=document['scopes']))


COMMANDS = {
    'hello': command_hello,
    'doctor': command_doctor,
    'credential-status': command_credential_status,
    'auth-authorize': command_auth_authorize,
    'credential-import': command_credential_import,
    'inspect': command_inspect,
    'local-test': command_local_test,
    'preview': command_preview,
    'publish': command_publish,
    'status': command_status,
    'resume': command_resume,
    'rollback': command_rollback,
    'history': command_history,
    'lifecycle-list': command_lifecycle_list,
    'lifecycle-preview': command_lifecycle_preview,
    'lifecycle-set': command_lifecycle_set,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=sorted(COMMANDS))
    parser.add_argument('--zip', help='ZIP 安装包路径')
    parser.add_argument('--plugin-id', help='期望的插件标识（脚本包必填）')
    parser.add_argument('--published', choices=('yes', 'no'))
    parser.add_argument('--notes', help='更新说明')
    parser.add_argument('--repo', help='ordina 发布仓库路径')
    parser.add_argument('--journal-dir', help='事务目录')
    parser.add_argument('--journal', help='事务记录路径')
    parser.add_argument('--records', help='发布记录文件')
    parser.add_argument('--credentials', help='授权文件（仅隔离环境）')
    parser.add_argument('--file', help='credential-import 的授权文件')
    parser.add_argument('--work-dir', help='local-test 的输出目录')
    parser.add_argument('--reason', help='回滚原因')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--all', action='store_true',
                        help='列出事务目录中的全部记录')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--execute', action='store_true', help='显式执行正式发布')
    parser.add_argument('--create-repository', action='store_true',
                        help='确认创建缺失的目标仓库')
    parser.add_argument('--expect-fingerprint',
                        help='预览指纹；与当前输入不一致时拒绝发布')
    parser.add_argument('--backend', default=env('ORDINA_DEV_BACKEND', 'production'),
                        choices=('production', 'lab'))
    options = parser.parse_args()
    if options.repo:
        os.environ['ORDINA_DEV_REPO'] = options.repo
    if options.backend == 'lab' and not lab_enabled():
        emit({'type': 'result', 'ok': False,
              'error': {'code': 'backend.lab',
                        'message': '隔离后端只允许在 ORDINA_DEV_LAB=1 时使用。'}})
        sys.exit(1)
    if lab_enabled() and options.backend == 'production' and options.command in ('publish', 'resume', 'rollback', 'lifecycle-set'):
        emit({'type': 'result', 'ok': False,
              'error': {'code': 'backend.lab',
                        'message': '隔离授权不能用于生产发布。'}})
        sys.exit(1)
    try:
        COMMANDS[options.command](options)
    except Failure as error:
        if os.environ.get('ORDINA_DEV_DEBUG') == '1':
            import traceback
            traceback.print_exc()
        fail(error)
        sys.exit(1)
    except dev_zip.PackageError as error:
        # The ZIP adapter already names the precise violation; keep that code
        # instead of flattening every rejection into a generic tool error.
        if os.environ.get('ORDINA_DEV_DEBUG') == '1':
            import traceback
            traceback.print_exc()
        fail(Failure(error.code, str(error)))
        sys.exit(1)
    except (ValueError, OSError) as error:
        fail(Failure('tool.error', str(error)))
        sys.exit(1)


if __name__ == '__main__':
    main()
