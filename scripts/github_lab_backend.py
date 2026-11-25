"""GitHub adapter restricted to isolated private test repositories.

It intentionally refuses production repositories. Git reference updates are
non-forced: each new commit has the previously observed head as its parent,
so concurrent updates fail instead of replacing another transaction.
"""
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

LAB_PREFIX = 'BrOKenAG/ordina-release-lab-20261124-'
LOCK_REF = 'heads/ordina-transaction-lock'


def api(method, endpoint, body=None, allow_missing=False):
    command = ['gh', 'api', '--method', method, endpoint]
    if body is not None: command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        if allow_missing and ('HTTP 404' in result.stderr or 'Not Found' in result.stderr): return None
        raise RuntimeError('GitHub request failed: ' + result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


class GitHubLabBackend:
    def __init__(self, lock_repo, asset_repo, asset_tag, asset_name):
        self._check(lock_repo); self._check(asset_repo)
        self.lock_repo, self.asset_repo = lock_repo, asset_repo
        self.asset_tag, self.asset_name = asset_tag, asset_name
        if Path(asset_name).name != asset_name: raise ValueError('invalid asset name')

    @staticmethod
    def _check(repo):
        if not repo.startswith(LAB_PREFIX) or '/' in repo[len(LAB_PREFIX):]:
            raise ValueError('only isolated test repositories are allowed')

    def _ref(self, repo, ref='heads/main', missing=False):
        self._check(repo)
        result = api('GET', f'repos/{repo}/git/ref/{ref}', allow_missing=missing)
        return result['object']['sha'] if result else None

    def _commit(self, repo, sha):
        return api('GET', f'repos/{repo}/git/commits/{sha}')

    def _new_commit(self, repo, parent, message, content=None):
        previous = self._commit(repo, parent)
        tree = previous['tree']['sha']
        if content is not None:
            blob = api('POST', f'repos/{repo}/git/blobs',
                       {'content': base64.b64encode(content).decode(), 'encoding': 'base64'})
            tree = api('POST', f'repos/{repo}/git/trees',
                       {'base_tree': tree, 'tree': [{'path': 'manifest.json', 'mode': '100644',
                                                   'type': 'blob', 'sha': blob['sha']}]})['sha']
        return api('POST', f'repos/{repo}/git/commits',
                   {'message': message, 'tree': tree, 'parents': [parent]})['sha']

    def _advance(self, repo, ref, sha):
        api('PATCH', f'repos/{repo}/git/refs/{ref}', {'sha': sha, 'force': False})

    def acquire(self, token):
        repo = self.lock_repo
        head = self._ref(repo, LOCK_REF, missing=True)
        if head is None:
            main = self._ref(repo)
            new = self._new_commit(repo, main, 'ordina-lock:' + token)
            api('POST', f'repos/{repo}/git/refs', {'ref': 'refs/' + LOCK_REF, 'sha': new})
            return
        msg = self._commit(repo, head)['message']
        if msg == 'ordina-lock:' + token: return
        if msg != 'ordina-lock:free': raise RuntimeError('release lock is held')
        new = self._new_commit(repo, head, 'ordina-lock:' + token)
        self._advance(repo, LOCK_REF, new)

    def release(self, token):
        repo = self.lock_repo
        head = self._ref(repo, LOCK_REF)
        if self._commit(repo, head)['message'] != 'ordina-lock:' + token:
            raise RuntimeError('release lock ownership changed')
        new = self._new_commit(repo, head, 'ordina-lock:free')
        self._advance(repo, LOCK_REF, new)

    def verify_assets(self, plan):
        asset = plan['asset']
        with tempfile.TemporaryDirectory(prefix='ordina-lab-asset-') as tmp:
            subprocess.run(['gh','release','download',self.asset_tag,'--repo',self.asset_repo,
                            '--pattern',self.asset_name,'--dir',tmp],check=True,
                           stdout=subprocess.DEVNULL)
            data = (Path(tmp)/self.asset_name).read_bytes()
            if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise RuntimeError('published asset does not match')

    def read(self, repo):
        head = self._ref(repo)
        obj = api('GET', f'repos/{repo}/contents/manifest.json?ref={head}')
        return head, base64.b64decode(obj['content'])

    def write(self, repo, expected, data, token):
        self._check(repo)
        if self._ref(repo) != expected: raise RuntimeError('remote head changed')
        commit = self._new_commit(repo, expected, 'ordina-transaction:' + token, data)
        self._advance(repo, 'heads/main', commit)
        return commit

    def is_transaction_commit(self, repo, head, previous, token):
        commit = self._commit(repo, head)
        return (commit['message'] == 'ordina-transaction:' + token
                and [p['sha'] for p in commit['parents']] == [previous])
