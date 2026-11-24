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
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    path: Path | None = None          # local file in the main repository
    repository: str | None = None     # remote repo whose manifest is PUT
    remote_path: str = 'manifest.json'


@dataclass
class Plan:
    label: str
    repository: str
    tag: str
    title: str
    notes: str
    asset: Asset
    ed_signature: str | None = None
    latest: bool = False
    feeds: list[FeedTarget] = field(default_factory=list)
    commit_paths: list[str] = field(default_factory=list)
    commit_message: str = 'Publish signed feeds'
    publish_appcast: bool = False
    validation_report: Path | None = None


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


def execute(plan, gateway, tmp, timeout=300):
    """Run the fixed stage order. Never proceeds past a failed stage."""
    completed = []

    def step(name, operation):
        try:
            result = operation()
        except Exception as error:
            raise ReleaseError(name, str(error), completed) from error
        completed.append(name)
        return result

    step('preflight', lambda: gateway.preflight(plan))
    asset_state = step('asset', lambda: ensure_asset(plan, gateway, tmp))
    step('gate', lambda: verify_asset(plan, gateway, tmp, timeout))
    labels = {feed.label for feed in plan.feeds}
    if {'registry', 'appcast'} <= labels:
        registry = [feed for feed in plan.feeds if feed.label == 'registry']
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
    elif error.stage in ('feeds', 'push', 'postflight'):
        lines.append('资产已验证；清单可能已部分写入，用 scripts/rollback_feeds.py 检查后再重试。')
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

    def verify_registry_online(self, target, timeout):
        self.calls.append('registry_gate')

    def verify_client_validation(self, plan):
        self.calls.append('client_validation')

    def postflight(self, plan):
        self.calls.append('postflight')


class LocalGateway(Gateway):
    """Real GitHub/git implementation used by the publish entry points."""

    def preflight(self, plan):
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
        return subprocess.run(['gh', 'release', 'view', tag, '--repo', repository],
                              cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
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
        existing = subprocess.run(
            ['gh', 'api', f'repos/{repository}/contents/{remote_path}'],
            cwd=ROOT, capture_output=True, text=True)
        body = {
            'message': f'Update {remote_path}',
            'content': base64.b64encode(content).decode('ascii'),
        }
        if existing.returncode == 0:
            body['sha'] = json.loads(existing.stdout)['sha']
        elif 'HTTP 404' not in existing.stderr:
            raise ValueError(f'{repository}/{remote_path} 读取失败，停止写入。')
        result = subprocess.run(
            ['gh', 'api', '-X', 'PUT', f'repos/{repository}/contents/{remote_path}',
             '--input', '-'], cwd=ROOT, input=json.dumps(body),
            capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f'{repository}/{remote_path} 写入失败：{result.stderr.strip()}')

    def push(self, plan):
        if not plan.commit_paths:
            return
        subprocess.run(['git', 'add', '--', *plan.commit_paths], cwd=ROOT, check=True)
        if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
            print('清单内容无变化，跳过提交')
            return
        subprocess.run(['git', 'commit', '-m', plan.commit_message], cwd=ROOT, check=True)
        subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, check=True)

    def verify_registry_online(self, target, timeout):
        import tempfile, time
        with tempfile.TemporaryDirectory(prefix='ordina-registry-gate-') as directory:
            dest = Path(directory) / 'registry.json'
            for attempt in range(6):
                code = self.http_get('https://brokenag.github.io/ordina-releases/registry.json?gate=' + str(time.time_ns()), dest, timeout)
                if code == '200' and dest.is_file() and dest.read_bytes() == target.content:
                    result = subprocess.run(['python3', 'scripts/check_feeds.py', '--remote', '--latest'], cwd=ROOT)
                    if result.returncode == 0:
                        return
                if attempt < 5:
                    time.sleep(10)
            raise ValueError('线上 registry 或完整清单检查未通过；appcast 未发布。')

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
