#!/usr/bin/env python3
"""Real end-to-end validation against the two PRIVATE GitHub lab repositories.

This never touches BrOKenAG/ordina-releases or any plugin repository. It uses
synthetic files and a synthetic release asset. A local HTTP server stands in
for GitHub Pages so eventual consistency (404 / stale -> correct -> resume) is
exercised without publishing anything public.

    python3 scripts/verify_lab_release.py --execute

On success it writes docs/validation/lab-release.json, which the production
publish entry points require before --execute is accepted.
"""
import argparse
import base64
import hashlib
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import durable_release
import durable_transaction as tx
import github_lab_backend as lab
from github_lab_backend import GitHubLabBackend, GitHubLabMultiBackend

MAIN = 'BrOKenAG/ordina-release-lab-20261124-main'
PLUGIN = 'BrOKenAG/ordina-release-lab-20261124-plugin'
PATHS = {
    MAIN: ['appcast.xml', 'registry.json', 'plugins/manifest.json'],
    PLUGIN: ['manifest.json', 'manifest-v2.json'],
}
REPORT = ROOT / 'docs' / 'validation' / 'lab-release.json'


class PagesState:
    def __init__(self):
        self.content = {}

    def publish(self, plan):
        self.content = {}
        for repo, raw in plan['feeds'].items():
            for path, encoded in json.loads(raw).items():
                if encoded is None:
                    continue
                key = '/' + repo.split('/', 1)[1] + '/' + quote(path, safe='/')
                self.content[key] = base64.b64decode(encoded, validate=True)


class PagesHandler(BaseHTTPRequestHandler):
    state = None

    def do_GET(self):
        data = self.state.content.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class PagesBackend(GitHubLabMultiBackend):
    """Lab backend whose completion check reads a local Pages-like server."""

    def __init__(self, *args, port, state, **kwargs):
        super().__init__(*args, **kwargs)
        self.port = port
        self.state = state
        self.online = True

    def verify_online(self, plan):
        if not self.online:
            raise RuntimeError('Pages 未生效（模拟）')
        for repo, raw in plan['feeds'].items():
            for path, encoded in json.loads(raw).items():
                if encoded is None:
                    continue
                url = '/' + repo.split('/', 1)[1] + '/' + quote(path, safe='/')
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
                try:
                    connection.request('GET', url)
                    response = connection.getresponse()
                    data = response.read()
                finally:
                    connection.close()
                if response.status != 200 or data != base64.b64decode(encoded, validate=True):
                    raise RuntimeError('线上内容尚未与事务一致')


def plan_for(asset, suffix):
    data = asset.read_bytes()
    files = {repo: {name: (f'synthetic {suffix} {repo} {name}').encode()
                    for name in names}
             for repo, names in PATHS.items()}
    feeds = {repo: GitHubLabMultiBackend.encode_files(fs).decode('ascii')
             for repo, fs in files.items()}
    return {
        'id': 'lab-' + suffix + '-' + uuid.uuid4().hex[:12],
        'asset': {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                  'source': str(asset.resolve())},
        'feeds': feeds,
    }


def check(name, operation, results):
    try:
        operation()
    except Exception as error:
        results[name] = 'fail'
        print(f'FAIL {name}: {error}', flush=True)
        raise
    results[name] = 'pass'
    print(f'PASS {name}', flush=True)


def release_stale_lock():
    """Best-effort cleanup so an interrupted run never blocks the next one."""
    ref = lab.api('GET', f'repos/{MAIN}/git/ref/heads/ordina-transaction-lock',
                  allow_missing=True)
    if ref is None:
        return
    head = ref['object']['sha']
    message = lab.api('GET', f'repos/{MAIN}/git/commits/{head}')['message']
    if message == 'ordina-lock:free':
        return
    token = message.removeprefix('ordina-lock:')
    GitHubLabBackend(MAIN, PLUGIN, 'x', 'x').release(token)
    print('已清理残留事务锁：' + token, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--execute', action='store_true',
                        help='真实访问两个私有测试仓库')
    parser.add_argument('--report', type=Path, default=REPORT)
    args = parser.parse_args()
    if not args.execute:
        sys.exit('需要 --execute 才会访问私有测试仓库。')

    state = PagesState()
    PagesHandler.state = state
    server = ThreadingHTTPServer(('127.0.0.1', 0), PagesHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = {}

    try:
        with tempfile.TemporaryDirectory(prefix='ordina-lab-verify-') as temporary:
            directory = Path(temporary)
            asset = directory / 'lab-verify-asset.txt'
            asset.write_bytes(b'ordina isolated release validation asset\n')
            tag = 'lab-verify-' + uuid.uuid4().hex[:12]
            heads = {repo: lab.api('GET', f'repos/{repo}/git/ref/heads/main')['object']['sha']
                     for repo in PATHS}
            probe = GitHubLabMultiBackend(MAIN, PLUGIN, tag, asset.name, PATHS)
            baseline = {repo: probe.read(repo)[1] for repo in PATHS}

            def backend(cls=PagesBackend, **kwargs):
                return cls(MAIN, PLUGIN, tag, asset.name, PATHS,
                           port=port, state=state, **kwargs)

            # 1. Normal multi-file cross-repository transaction.
            normal = {}
            def publish_normal():
                plan = plan_for(asset, 'normal')
                normal['plan'] = plan
                normal['journal'] = directory / (plan['id'] + '.json')
                state.publish(plan)
                remote = backend()
                tx.publish(plan, normal['journal'], remote)
                for repo, expected in plan['feeds'].items():
                    assert remote.read(repo)[1] == expected.encode()
            check('multi_file_transaction', publish_normal, results)

            # 2. Roll the completed transaction back to the exact baseline.
            def rollback_normal():
                remote = backend()
                tx.rollback(normal['journal'], remote)
                for repo in PATHS:
                    assert remote.read(repo)[1] == baseline[repo]
            check('rollback', rollback_normal, results)

            # 3. Lost response after the first remote commit, then resume.
            def lost_response():
                plan = plan_for(asset, 'lost')
                journal = directory / (plan['id'] + '.json')
                state.publish(plan)

                class Interrupt(PagesBackend):
                    once = True

                    def write(self, *values):
                        result = super().write(*values)
                        if self.once:
                            self.once = False
                            raise ConnectionError('模拟：远端已提交但响应丢失')
                        return result

                try:
                    tx.publish(plan, journal, backend(Interrupt))
                except ConnectionError:
                    pass
                else:
                    raise RuntimeError('未模拟到断线')
                assert json.loads(journal.read_text())['status'] != 'complete'
                remote = backend()
                tx.publish(plan, journal, remote)
                for repo, expected in plan['feeds'].items():
                    assert remote.read(repo)[1] == expected.encode()
                tx.rollback(journal, remote)
            check('lost_response_resume', lost_response, results)

            # 4. Pages not yet propagated: journal stays pending, resume completes.
            def pages_delay():
                plan = plan_for(asset, 'pages')
                journal = directory / (plan['id'] + '.json')
                state.content = {}
                remote = backend()
                remote.online = False
                try:
                    tx.publish(plan, journal, remote)
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError('Pages 未生效时不应完成事务')
                assert json.loads(journal.read_text())['status'] != 'complete'
                state.publish(plan)
                remote = backend()
                tx.resume(journal, remote)
                assert json.loads(journal.read_text())['status'] == 'complete'
                tx.rollback(journal, remote)
            check('pages_delay_resume', pages_delay, results)

            # 5. A foreign commit to a later repository must not be overwritten.
            def foreign_head():
                plan = plan_for(asset, 'foreign')
                journal = directory / (plan['id'] + '.json')
                state.publish(plan)

                class Concurrent(PagesBackend):
                    injected = False

                    def write(self, repo, expected, data, token):
                        result = super().write(repo, expected, data, token)
                        if repo == MAIN and not self.injected:
                            self.injected = True
                            tree = lab.api('POST', f'repos/{PLUGIN}/git/trees', {
                                'base_tree': heads[PLUGIN],
                                'tree': [{'path': 'manifest.json', 'mode': '100644',
                                          'type': 'blob', 'content': 'foreign-publisher'}],
                            })['sha']
                            commit = lab.api('POST', f'repos/{PLUGIN}/git/commits', {
                                'message': 'foreign publisher', 'tree': tree,
                                'parents': [heads[PLUGIN]],
                            })['sha']
                            lab.api('PATCH', f'repos/{PLUGIN}/git/refs/heads/main',
                                    {'sha': commit, 'force': False})
                        return result

                try:
                    tx.publish(plan, journal, backend(Concurrent))
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError('并发提交未被拒绝')
                assert backend().read(PLUGIN)[1] != plan['feeds'][PLUGIN].encode()
                token = json.loads(journal.read_text())['token']
                remote = backend()
                remote.release(token)
                # Test repositories only: force both refs back to the baseline.
                for repo in PATHS:
                    lab.api('PATCH', f'repos/{repo}/git/refs/heads/main',
                            {'sha': heads[repo], 'force': True})
            check('foreign_head_rejected', foreign_head, results)
    finally:
        try:
            release_stale_lock()
        except Exception as error:
            print('锁清理失败：' + str(error), flush=True)
        server.shutdown()
        thread.join(timeout=5)
        report = {
            'format': 1,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'repositories': [MAIN, PLUGIN],
            'checks': results,
            'passed': all(results.get(name) == 'pass'
                          for name in durable_release.LAB_CHECKS),
            'notes': '私有测试仓库真实端到端验证；本地 HTTP 服务模拟 Pages 最终一致性；正式仓库未修改。',
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
        print('验证报告：' + str(args.report), flush=True)

    if not all(results.get(name) == 'pass' for name in durable_release.LAB_CHECKS):
        sys.exit('隔离仓库验证未全部通过。')
    print('隔离仓库端到端验证全部通过；正式仓库未修改。')


if __name__ == '__main__':
    main()
