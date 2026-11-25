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
                            capture_output=True, text=True, timeout=30)
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

    def _release_assets(self):
        result = subprocess.run(['gh','release','view',self.asset_tag,'--repo',self.asset_repo,
                                 '--json','assets'], capture_output=True, text=True, timeout=30)
        if result.returncode:
            if 'release not found' in result.stderr.lower() or 'http 404' in result.stderr.lower():
                return None
            raise RuntimeError('cannot inspect test release: ' + result.stderr.strip())
        return {asset['name'] for asset in json.loads(result.stdout)['assets']}

    def _create_release(self, source):
        subprocess.run(['gh','release','create',self.asset_tag,str(source),'--repo',self.asset_repo,
                        '--title','Isolated release test', '--notes','Private test.', '--latest=false'],
                       check=True, stdout=subprocess.DEVNULL, timeout=120)

    def _upload_asset(self, source):
        # No --clobber: a racing publisher must never replace an existing asset.
        subprocess.run(['gh','release','upload',self.asset_tag,str(source),'--repo',self.asset_repo],
                       check=True, stdout=subprocess.DEVNULL, timeout=120)

    def _download_asset(self):
        with tempfile.TemporaryDirectory(prefix='ordina-lab-asset-') as tmp:
            subprocess.run(['gh','release','download',self.asset_tag,'--repo',self.asset_repo,
                            '--pattern',self.asset_name,'--dir',tmp],check=True,
                           stdout=subprocess.DEVNULL, timeout=120)
            return (Path(tmp)/self.asset_name).read_bytes()

    def _check_lock_owner(self, token):
        owner = token.removesuffix(':rollback')
        head = self._ref(self.lock_repo, LOCK_REF)
        if self._commit(self.lock_repo, head)['message'] != 'ordina-lock:' + owner:
            raise RuntimeError('release lock ownership changed')

    def verify_assets(self, plan):
        asset = plan['asset']
        source = Path(asset['source']) if asset.get('source') else None
        names = self._release_assets()
        if names is None or self.asset_name not in names:
            if source is None or source.name != self.asset_name or not source.is_file():
                raise RuntimeError('release asset is missing')
            data = source.read_bytes()
            if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise RuntimeError('local asset does not match the transaction')
            if names is None:
                self._create_release(source)
            else:
                self._upload_asset(source)
        # Always obtain bytes from the remote release after publication.
        data = self._download_asset()
        if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise RuntimeError('published asset does not match')

    def verify_online(self, plan):
        # Private lab repositories have no public Pages. This validates the
        # authenticated GitHub contents API; production needs Pages checks.
        for repo, value in plan['feeds'].items():
            _, data = self.read(repo)
            if data != value.encode('utf-8'):
                raise RuntimeError('remote feed does not match the transaction')

    def read(self, repo):
        head = self._ref(repo)
        obj = api('GET', f'repos/{repo}/contents/manifest.json?ref={head}')
        return head, base64.b64decode(obj['content'])

    def write(self, repo, expected, data, token):
        self._check(repo)
        self._check_lock_owner(token)
        if self._ref(repo) != expected: raise RuntimeError('remote head changed')
        commit = self._new_commit(repo, expected, 'ordina-transaction:' + token, data)
        self._check_lock_owner(token)
        self._advance(repo, 'heads/main', commit)
        return commit

    def is_transaction_commit(self, repo, head, previous, token):
        commit = self._commit(repo, head)
        return (commit['message'] == 'ordina-transaction:' + token
                and [p['sha'] for p in commit['parents']] == [previous])


class GitHubLabMultiBackend(GitHubLabBackend):
    """Test adapter that updates all selected files of one repository atomically."""
    def __init__(self, lock_repo, asset_repo, asset_tag, asset_name, paths):
        super().__init__(lock_repo, asset_repo, asset_tag, asset_name)
        self.paths = {}
        for repo, names in paths.items():
            self._check(repo)
            clean = sorted(set(names))
            if not clean or any(not self._safe_path(name) for name in clean):
                raise ValueError('invalid test file paths')
            self.paths[repo] = clean

    @staticmethod
    def _safe_path(value):
        if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 500:
            return False
        if '\\' in value or ':' in value or any(ord(char) < 32 for char in value):
            return False
        return all(part and not part.startswith('.') for part in value.split('/'))

    @staticmethod
    def encode_files(files):
        return json.dumps({name: (base64.b64encode(data).decode('ascii') if data is not None else None)
                           for name, data in files.items()}, sort_keys=True,
                          separators=(',', ':')).encode()

    def read(self, repo):
        head = self._ref(repo)
        files = {}
        from urllib.parse import quote
        for name in self.paths[repo]:
            obj = api('GET', f'repos/{repo}/contents/{quote(name, safe="/")}?ref={head}', allow_missing=True)
            files[name] = base64.b64decode(obj['content']) if obj is not None else None
        return head, self.encode_files(files)

    def write(self, repo, expected, data, token):
        self._check(repo)
        self._check_lock_owner(token)
        if self._ref(repo) != expected: raise RuntimeError('remote head changed')
        document = json.loads(data)
        if not isinstance(document, dict) or set(document) != set(self.paths[repo]):
            raise ValueError('unexpected file set')
        previous = self._commit(repo, expected)
        tree_entries = []
        for name, encoded in sorted(document.items()):
            if not self._safe_path(name): raise ValueError('unsafe path')
            if encoded is None:
                sha = None
            else:
                content = base64.b64decode(encoded, validate=True)
                sha = api('POST', f'repos/{repo}/git/blobs',
                          {'content': base64.b64encode(content).decode(), 'encoding': 'base64'})['sha']
            tree_entries.append({'path':name,'mode':'100644','type':'blob','sha':sha})
        tree = api('POST', f'repos/{repo}/git/trees',
                   {'base_tree': previous['tree']['sha'], 'tree': tree_entries})['sha']
        commit = api('POST', f'repos/{repo}/git/commits',
                     {'message':'ordina-transaction:' + token,'tree':tree,'parents':[expected]})['sha']
        self._check_lock_owner(token)
        self._advance(repo, 'heads/main', commit)
        return commit
