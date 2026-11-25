#!/usr/bin/env python3
"""Bridge the fixed release stage machine to the durable cross-repo journal.

``release_transaction.execute`` remains the zero-side-effect rehearsal path.
Real publishing goes through :func:`execute`, which keeps the same six stages
but delegates every remote feed write to ``durable_transaction.publish``:

    preflight -> asset -> gate -> feeds -> push -> postflight

``feeds`` writes all repositories inside ONE resumable journal; ``push`` only
confirms the journal is complete. The journal and the staged asset live under
``.transactions/`` so an interrupted release can be resumed with the exact
same plan bytes, or rolled back to the recorded pre-publish state.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import durable_transaction as tx
from github_lab_backend import GitHubLabMultiBackend
from github_production_backend import ProductionBackend
from release_transaction import (ROOT, Plan, ReleaseError, describe_failure,
                                 ensure_asset, verify_asset)

MAIN = 'BrOKenAG/ordina-releases'
TRANSACTIONS = ROOT / '.transactions'
LAB_REPORT = ROOT / 'docs' / 'validation' / 'lab-release.json'
ID_RE = re.compile(r'[A-Za-z0-9_-]{1,80}')
LAB_CHECKS = ('multi_file_transaction', 'lost_response_resume',
              'foreign_head_rejected', 'rollback', 'pages_delay_resume')


class DryRunBackend:
    """In-memory remote for --dry-run: identical stage flow, zero side effects."""

    def __init__(self, repositories):
        self.heads = {repo: 'dry-h0' for repo in repositories}
        self.values = {repo: b'' for repo in repositories}
        self.markers = {}
        self.lock = None
        self.calls = []

    def acquire(self, token):
        if self.lock not in (None, token):
            raise RuntimeError('locked')
        self.lock = token

    def release(self, token):
        if self.lock != token:
            raise RuntimeError('not owner')
        self.lock = None

    def verify_assets(self, plan):
        self.calls.append('verify_assets')

    def verify_online(self, plan):
        self.calls.append('verify_online')

    def read(self, repo):
        return self.heads[repo], self.values[repo]

    def is_transaction_commit(self, repo, head, previous, token):
        return self.markers.get((repo, head)) == (previous, token)

    def write(self, repo, expected, data, token):
        if self.heads[repo] != expected:
            raise RuntimeError('head changed')
        old = self.heads[repo]
        self.heads[repo] += 'x'
        self.markers[(repo, self.heads[repo])] = (old, token)
        self.values[repo] = data
        self.calls.append(('write', repo))
        return self.heads[repo]


@dataclass(frozen=True)
class Prepared:
    plan: dict                 # durable_transaction plan (JSON-serialisable)
    journal: Path
    files: dict                # repository -> {path: bytes}
    paths: dict                # repository -> [path] for the backend
    asset_source: Path


def scope_id(plan, scope):
    """Stable id: the same release always maps to the same journal."""
    clean = re.sub(r'[^A-Za-z0-9_-]+', '-', scope).strip('-')[:40]
    digest = hashlib.sha256(
        f'{scope}|{plan.repository}|{plan.tag}|{plan.asset.sha256}'.encode()
    ).hexdigest()[:20]
    value = f'{clean}-{digest}'
    if not ID_RE.fullmatch(value):
        raise ValueError('事务标识无效')
    return value


def collect_files(plan, include_appcast=True, only_appcast=False):
    """Group every feed target into {repository: {path: bytes}}.

    Main-repository files are always owned by MAIN, never by ``plan.repository``
    (which is a plugin repository for plugin releases).
    """
    files = {MAIN: {}}
    for target in plan.feeds:
        is_appcast = target.label == 'appcast'
        if only_appcast and not is_appcast:
            continue
        if not include_appcast and is_appcast:
            continue
        if target.repository:
            files.setdefault(target.repository, {})[target.remote_path] = target.content
        elif target.path is not None and target.path.is_relative_to(ROOT):
            files[MAIN][target.path.relative_to(ROOT).as_posix()] = target.content
    grouped = {repo: fs for repo, fs in sorted(files.items()) if fs}
    if not grouped:
        raise ValueError('事务没有可写入的清单')
    for repo, fs in grouped.items():
        for path, data in fs.items():
            if not isinstance(data, bytes) or not GitHubLabMultiBackend._safe_path(path):
                raise ValueError(f'不受允许的发布文件：{repo}/{path}')
    return grouped


def prepare(plan, scope, include_appcast=True, only_appcast=False,
            journal_dir=TRANSACTIONS):
    """Derive a deterministic durable plan without touching any remote."""
    files = collect_files(plan, include_appcast, only_appcast)
    tid = scope_id(plan, scope)
    directory = Path(journal_dir)
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / f'{tid}.json'
    asset_source = directory / f'{tid}.asset'
    if asset_source.exists():
        if asset_source.read_bytes() != plan.asset.data:
            raise ValueError('事务资产文件已被改动，请递增版本号或清理该事务目录后重试')
    else:
        asset_source.write_bytes(plan.asset.data)

    feeds = {repo: GitHubLabMultiBackend.encode_files(fs).decode('ascii')
             for repo, fs in sorted(files.items())}
    try:
        staged = asset_source.relative_to(ROOT).as_posix()
    except ValueError:
        staged = str(asset_source)
    durable = {
        'id': tid,
        'asset': {
            'size': plan.asset.size,
            'sha256': plan.asset.sha256,
            'url': plan.asset.url,
            'source': staged,
        },
        'feeds': feeds,
    }
    if plan.ed_signature:
        durable['asset']['ed_signature'] = plan.ed_signature
    return Prepared(durable, journal, files,
                    {repo: sorted(fs) for repo, fs in files.items()}, asset_source)


def lab_report_valid(path=None):
    """A real isolated-repo validation report is required for production runs."""
    try:
        report = json.loads(Path(path or LAB_REPORT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if report.get('format') != 1 or report.get('passed') is not True:
        return False
    checks = report.get('checks')
    return isinstance(checks, dict) and all(checks.get(name) == 'pass'
                                            for name in LAB_CHECKS)


def production_backend(prepared, plan):
    """Lock on MAIN; the release asset lives in the release's own repository."""
    return ProductionBackend(MAIN, plan.repository, plan.tag, plan.asset.name,
                             prepared.paths, set(prepared.paths))


def confirm(journal, durable):
    """The push stage is only a checkpoint: all writes already happened."""
    record = json.loads(Path(journal).read_text(encoding='utf-8'))
    if record.get('status') != 'complete':
        raise RuntimeError('跨仓库事务未完成，不能进入 postflight')
    repos = record.get('repositories', {})
    if set(repos) != set(durable['feeds']) or any(
            item.get('after_head') is None for item in repos.values()):
        raise RuntimeError('事务记录不完整，请检查远端状态后恢复')


def backend_from_journal(journal_path):
    """Rebuild the production backend from a persisted plan (no CLI args needed)."""
    path = Path(journal_path)
    journal = json.loads(path.read_text(encoding='utf-8'))
    durable = journal.get('plan')
    if not isinstance(durable, dict) or not isinstance(durable.get('feeds'), dict):
        raise ValueError('事务记录缺少有效的发布计划')
    files = {repo: json.loads(raw) for repo, raw in durable['feeds'].items()}
    paths = {repo: sorted(files[repo]) for repo in files}
    match = re.fullmatch(
        r'https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)',
        durable['asset'].get('url', ''))
    if not match:
        raise ValueError('事务资产地址无效')
    repository, tag, name = match.group(1), unquote(match.group(2)), unquote(match.group(3))
    backend = ProductionBackend(MAIN, repository, tag, name, paths, set(paths))
    return backend, durable, journal


def resume_journal(journal_path, backend):
    tx.resume(journal_path, backend)
    journal = json.loads(Path(journal_path).read_text(encoding='utf-8'))
    confirm(journal_path, journal['plan'])
    return journal['plan']


def execute(plan, gateway, backend, prepared, tmp, timeout=300, on_stage=None):
    """Run the fixed stages; durable_transaction owns every remote feed write."""
    completed = []

    def step(name, operation):
        if on_stage is not None:
            on_stage(name, 'running')
        try:
            result = operation()
        except Exception as error:
            if on_stage is not None:
                on_stage(name, 'failed')
            raise ReleaseError(name, str(error), completed) from error
        completed.append(name)
        if on_stage is not None:
            on_stage(name, 'ok')
        return result

    step('preflight', lambda: gateway.preflight(plan))
    asset_state = step('asset', lambda: ensure_asset(plan, gateway, tmp))
    step('gate', lambda: verify_asset(plan, gateway, tmp, timeout))
    step('feeds', lambda: tx.publish(prepared.plan, prepared.journal, backend))
    step('push', lambda: confirm(prepared.journal, prepared.plan))
    step('postflight', lambda: gateway.postflight(plan))
    return {'stages': completed, 'asset': asset_state,
            'journal': str(prepared.journal)}


def describe(error, journal=None):
    text = describe_failure(error)
    if journal and error.stage in ('feeds', 'push', 'postflight'):
        text += (f'\n事务记录：{journal}'
                 f'\n恢复：重跑同一命令（会自动从记录续发）；回滚：--rollback {journal}')
    return text
