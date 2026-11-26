#!/usr/bin/env python3
"""Transactional publishing pipeline shared by app and plugin releases.

Hard invariant
--------------
A signed feed is written or pushed ONLY after the asset it references has been
downloaded and verified: HTTP 200, byte size, SHA-256 and (for app releases)
the Sparkle Ed25519 signature. Any failure aborts immediately and reports the
completed stages, so an operator can retry or roll back without ever having
published a feed pointing at a missing asset.

Stages, in order:
    preflight -> asset -> gate -> feeds -> push -> postflight

All side effects go through a Gateway. Tests inject a fake gateway, which is
how the ordering invariant is verified without touching GitHub.
"""
import base64
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

ROOT = Path(os.environ.get('ORDINA_DEV_REPO')
            or Path(__file__).resolve().parents[1]).expanduser().resolve()
CURL = '/usr/bin/curl'
STAGES = ('preflight', 'asset', 'gate', 'feeds', 'push', 'postflight')


class ReleaseError(RuntimeError):
    def __init__(self, stage, message, completed=()):
        super().__init__(message)
        self.stage = stage
        self.completed = tuple(completed)


@dataclass(frozen=True)
class Asset:
    name: str
    data: bytes
    url: str

    @property
    def size(self):
        return len(self.data)

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class FeedTarget:
    label: str
    content: bytes
    path: Optional[Path] = None          # local file in the main repository
    repository: Optional[str] = None     # remote repo whose manifest is PUT
    remote_path: str = 'manifest.json'


@dataclass
class Plan:
    label: str
    repository: str
    tag: str
    title: str
    notes: str
    asset: Asset
    ed_signature: Optional[str] = None
    latest: bool = False
    feeds: list[FeedTarget] = field(default_factory=list)
    commit_paths: list[str] = field(default_factory=list)
    commit_message: str = 'Publish signed feeds'
    publish_appcast: bool = False
    validation_report: Optional[Path] = None


def check_asset(code, downloaded, asset, signature_ok=True):
    """Pure GATE logic. Raises unless every recorded property matches."""
    if code != '200':
        raise ValueError(f'下载地址不可用（HTTP {code}）')
    if len(downloaded) != asset.size:
        raise ValueError(f'文件大小 {len(downloaded):,} 与预期 {asset.size:,} 不符')
    if hashlib.sha256(downloaded).hexdigest() != asset.sha256:
        raise ValueError('SHA-256 与本地资产不一致')
    if not signature_ok:
        raise ValueError('Ed25519 签名不匹配')
    return True


def validate_feed_transition(current_bytes, new_bytes, verify):
    """Reject an unsigned feed, a plugin downgrade or a same-version replacement."""
    current, new = verify(current_bytes), verify(new_bytes)
    if current.get('format') != 1 or new.get('format') != 1:
        raise ValueError('插件清单格式不符，停止远端写入。')
    old_plugins, new_plugins = current.get('plugins'), new.get('plugins')
    if not isinstance(old_plugins, dict) or not isinstance(new_plugins, dict):
        raise ValueError('插件清单格式不符。')
    if set(old_plugins) - set(new_plugins):
        raise ValueError('新清单会删除已发布插件，请先人工审查。')
    def version(value):
        parts = value.split('.')
        if len(parts) < 2 or len(parts) > 4 or any(not x.isdigit() for x in parts):
            raise ValueError('插件版本无效。')
        return tuple(int(x) for x in (parts + ['0'] * (4 - len(parts))))
    for name, old in old_plugins.items():
        target = new_plugins[name]
        ov, nv = version(old.get('version', '')), version(target.get('version', ''))
        if nv < ov or (nv == ov and old.get('sha256') != target.get('sha256')):
            raise ValueError('远端插件较新或同版本内容不同，停止覆盖。')


def wait_for_registry(fetch, validate, expected, sleep, attempts=6):
    """Require exact online registry bytes and a complete independent check."""
    for attempt in range(attempts):
        try:
            if fetch() == expected and validate():
                return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        if attempt + 1 < attempts:
            sleep()
    raise ValueError('线上 registry 或完整清单检查未通过；appcast 未发布。')


def ensure_asset(plan, gateway, tmp):
    """Create the Release, or reuse an identical one on an idempotent retry."""
    if gateway.release_exists(plan.repository, plan.tag):
        existing = tmp / 'existing-asset'
        code = gateway.http_get(plan.asset.url, existing, 120)
        data = existing.read_bytes() if existing.is_file() else b''
        if code != '200' or data != plan.asset.data:
            raise ValueError('该版本已存在但资产内容不一致，请递增版本号。')
        return 'reused'
    path = tmp / plan.asset.name
    path.write_bytes(plan.asset.data)
    gateway.create_release(plan.repository, plan.tag, path,
                           plan.title, plan.notes, plan.latest)
    return 'created'


def verify_asset(plan, gateway, tmp, timeout):
    """GATE: download the published asset and verify every recorded property."""
    dest = tmp / 'verified-asset'
    code = gateway.http_get(plan.asset.url, dest, timeout)
    data = dest.read_bytes() if dest.is_file() else b''
    signature_ok = True
    if plan.ed_signature:
        signature_ok = gateway.verify_signature(dest, plan.ed_signature)
    return check_asset(code, data, plan.asset, signature_ok)


def execute(plan, gateway, tmp, timeout=300, on_stage=None):
    """Run the fixed stage order. Never proceeds past a failed stage."""
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
    labels = {feed.label for feed in plan.feeds}
    if {'registry', 'appcast'} <= labels:
        registry = [feed for feed in plan.feeds if feed.label == 'registry']
        prerequisites = [feed for feed in plan.feeds if feed.label not in ('registry', 'appcast')]
        if prerequisites:
            step('prerequisite_gate', lambda: gateway.verify_prerequisites(prerequisites, timeout))
            step('prerequisite_feeds', lambda: gateway.write_feeds(prerequisites))
            local_paths = [str(feed.path.relative_to(ROOT)) for feed in prerequisites if feed.path is not None and feed.path.is_relative_to(ROOT)]
            if local_paths:
                step('prerequisite_push', lambda: gateway.push(replace(plan, commit_paths=local_paths)))
        appcast = [feed for feed in plan.feeds if feed.label == 'appcast']
        step('registry_write', lambda: gateway.write_feeds(registry))
        step('registry_push', lambda: gateway.push(replace(plan, commit_paths=['registry.json'])))
        step('registry_gate', lambda: gateway.verify_registry_online(registry[0], timeout))
        # Appcast is an explicit separate invocation, gated by a real-client
        # validation report. Preparing a release can never notify clients.
        if not getattr(plan, 'publish_appcast', False):
            return {'stages': completed, 'asset': asset_state, 'awaiting_client_validation': True}
        step('client_validation', lambda: gateway.verify_client_validation(plan))
        step('feeds', lambda: gateway.write_feeds(appcast))
        step('push', lambda: gateway.push(replace(plan, commit_paths=['appcast.xml'])))
    else:
        step('feeds', lambda: gateway.write_feeds(plan.feeds))
        step('push', lambda: gateway.push(plan))
    step('postflight', lambda: gateway.postflight(plan))
    return {'stages': completed, 'asset': asset_state}


def describe_failure(error):
    lines = [f'阶段 {error.stage} 失败：{error}',
             '已完成阶段：' + (', '.join(error.completed) or '无')]
    if error.stage == 'gate':
        lines.append('签名清单未被写入；请检查 Release 资产后重跑同一命令（幂等）。')
    elif error.stage in ('prerequisite_feeds', 'prerequisite_push', 'registry_write', 'registry_push', 'registry_gate', 'client_validation', 'feeds', 'push', 'postflight'):
        lines.append('发布非原子操作：registry、appcast 或远端清单可能已部分写入；先检查本地与线上状态，再决定恢复或重试。')
    else:
        lines.append('未产生任何清单改动；修复后重跑同一命令。')
    return '\n'.join(lines)


class Gateway:
    """All side effects. Subclass and override; nothing happens implicitly."""

    def preflight(self, plan):
        pass

    def release_exists(self, repository, tag):
        return False

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        pass

    def http_get(self, url, dest, timeout):
        return '000'

    def verify_signature(self, path, signature):
        return True

    def write_feeds(self, feeds):
        pass

    def push(self, plan):
        pass

    def verify_prerequisites(self, feeds, timeout):
        raise NotImplementedError

    def verify_registry_online(self, target, timeout):
        raise NotImplementedError

    def verify_client_validation(self, plan):
        raise NotImplementedError

    def postflight(self, plan):
        pass


class DryRunGateway(Gateway):
    """Records the stage order without any network or disk side effects."""

    def __init__(self, asset_data):
        self.asset_data = asset_data
        self.calls = []

    def preflight(self, plan):
        self.calls.append('preflight')

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        self.calls.append('create_release')

    def http_get(self, url, dest, timeout):
        dest.write_bytes(self.asset_data)
        return '200'

    def verify_signature(self, path, signature):
        return True

    def write_feeds(self, feeds):
        self.calls.append(('write_feeds', [f.label for f in feeds]))

    def push(self, plan):
        self.calls.append('push')

    def verify_prerequisites(self, feeds, timeout):
        self.calls.append('prerequisite_gate')

    def verify_registry_online(self, target, timeout):
        self.calls.append('registry_gate')

    def verify_client_validation(self, plan):
        self.calls.append('client_validation')

    def postflight(self, plan):
        self.calls.append('postflight')


class LocalGateway(Gateway):
    """Real GitHub/git implementation used by the publish entry points."""

    def preflight(self, plan):
        self._authorize_release(plan.repository)
        def git(*args):
            return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()

        if git('branch', '--show-current') != 'main':
            raise ValueError('请在 main 分支发布。')
        if git('diff', '--cached', '--name-only'):
            raise ValueError('暂存区有其他改动，请先处理。')
        git('fetch', 'origin', 'main')
        if git('rev-parse', 'HEAD') != git('rev-parse', 'origin/main'):
            raise ValueError('本地 main 与远端不一致，请先同步。')
        subprocess.run(['gh', 'auth', 'status'], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from feed_signing import sign_tool
        if not sign_tool().is_file():
            raise ValueError('找不到签名工具。')

    def release_exists(self, repository, tag):
        result = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository],
                                cwd=ROOT, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        if 'release not found' in result.stderr.lower() or 'http 404' in result.stderr.lower():
            return False
        raise ValueError('无法确认 Release 是否存在，请检查网络与授权后重试。')

    @staticmethod
    def _authorize_release(repository):
        import re
        if repository == 'BrOKenAG/ordina-releases':
            from production_authorization import authorize_main_write
            authorize_main_write()
        elif re.fullmatch(r'BrOKenAG/ordina-plugin-[a-z][a-z0-9-]{0,31}', repository or ''):
            from dev_publish import authorize_direct_plugin_write
            authorize_direct_plugin_write({'BrOKenAG/ordina-releases', repository})
        else:
            raise ValueError('生产发布仓库不受信任。')

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        self._authorize_release(repository)
        command = ['gh', 'release', 'create', tag, str(asset_path),
                   '--repo', repository, '--title', title, '--notes', notes]
        command.append('--latest' if latest else '--latest=false')
        subprocess.run(command, cwd=ROOT, check=True)

    def http_get(self, url, dest, timeout):
        result = subprocess.run(
            [CURL, '-sSL', '--max-time', str(timeout), '-w', '%{http_code}',
             '-o', str(dest), url], capture_output=True, text=True)
        return result.stdout.strip() or '000'

    def verify_signature(self, path, signature):
        from feed_signing import sign_tool
        from sparkle_tools import KEYCHAIN_ACCOUNT
        result = subprocess.run([str(sign_tool()), '--account', KEYCHAIN_ACCOUNT,
                                 '--verify', str(path), signature],
                                capture_output=True, text=True)
        return result.returncode == 0

    def write_feeds(self, feeds):
        for target in feeds:
            if target.path is not None:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                target.path.write_bytes(target.content)
            if target.repository:
                self._put_remote(target.repository, target.remote_path, target.content)

    def _put_remote(self, repository, remote_path, content):
        raise ValueError('直接写入已禁用，请使用持久化发布事务。')

    def push(self, plan):
        if plan.commit_paths:
            raise ValueError('直接推送已禁用，请使用持久化发布事务。')

    def verify_prerequisites(self, feeds, timeout):
        import tempfile
        from feed_signing import verify_envelope
        from check_feeds import entry_url_error
        with tempfile.TemporaryDirectory(prefix='ordina-prerequisite-') as directory:
            for feed in feeds:
                doc = verify_envelope(feed.content)
                if doc.get('format') != 1 or not isinstance(doc.get('plugins'), dict):
                    raise ValueError('插件清单格式无效。')
                for name, entry in doc['plugins'].items():
                    if feed.label == 'legacy' or feed.label.startswith('compat-plugin:'):
                        prefix = '/BrOKenAG/ordina-releases/releases/download/'
                    else:
                        prefix = '/BrOKenAG/ordina-plugin-' + name + '/releases/download/'
                    problem = entry_url_error(entry, prefix)
                    if problem:
                        raise ValueError(f'{name}: {problem}')
                    target = Path(directory) / 'asset'
                    target.unlink(missing_ok=True)
                    code = self.http_get(entry['url'], target, timeout)
                    if code != '200' or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                        raise ValueError(f'{name} 资源不可用或哈希不符，停止发布清单。')

    def verify_registry_online(self, target, timeout):
        import tempfile, time
        with tempfile.TemporaryDirectory(prefix='ordina-registry-gate-') as directory:
            dest = Path(directory) / 'registry.json'
            def fetch():
                code = self.http_get('https://brokenag.github.io/ordina-releases/registry.json?gate=' + str(time.time_ns()), dest, timeout)
                return dest.read_bytes() if code == '200' and dest.is_file() else None
            def validate():
                return subprocess.run(['python3', 'scripts/check_feeds.py', '--remote', '--latest'], cwd=ROOT).returncode == 0
            wait_for_registry(fetch, validate, target.content, lambda: time.sleep(10))

    def verify_client_validation(self, plan):
        if not plan.validation_report or not plan.validation_report.is_file():
            raise ValueError('缺少真实客户端验证记录；appcast 未发布。')
        report = json.loads(plan.validation_report.read_text(encoding='utf-8'))
        if report.get('tag') != plan.tag or report.get('asset_sha256') != plan.asset.sha256 or report.get('real_client_verified') is not True:
            raise ValueError('验证记录未确认当前安装包；appcast 未发布。')

    def postflight(self, plan):
        # GitHub Pages is eventually consistent. Retry only the whole check so
        # a feed push that has not propagated yet does not abort a good release.
        import time
        for attempt in range(6):
            result = subprocess.run(['python3', 'scripts/check_feeds.py', '--remote'],
                                    cwd=ROOT)
            if result.returncode == 0:
                return
            if attempt < 5:
                print(f'postflight 第 {attempt + 1} 次未通过，等待 Pages 生效后重试…')
                time.sleep(20)
        raise ValueError('发布后体检未通过；运行 python3 scripts/check_feeds.py --remote 查看细节')
