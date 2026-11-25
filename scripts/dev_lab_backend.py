"""Isolated backend for the developer publishing engine.

This module is only bundled into a *lab* build of the publishing tool. It
implements the same backend contract as ``ProductionBackend`` (exclusive lock,
conditional writes, verified assets, transaction-marked commits, online
re-read) against a local directory instead of GitHub, so the full six-stage
transaction can be exercised without any network or real repository.

A lab backend refuses every production repository: only ``BrOKenAG/ordina-*-lab*``
shapes are accepted, and the whole module is absent from the production tool.
"""
import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

LAB_REPO_RE = re.compile(r'BrOKenAG/ordina-(?:plugin-)?[A-Za-z0-9._-]*lab[A-Za-z0-9._-]*')
ASSET_RE = re.compile(
    r'https://github\.com/(BrOKenAG/[A-Za-z0-9._-]+)/releases/download/'
    r'([^/]+)/([^/]+)$')


class LabError(RuntimeError):
    pass


def _fail(message):
    raise LabError(message)


class LabBackend:
    def __init__(self, root):
        self.root = Path(root) / 'lab'
        self.root.mkdir(parents=True, exist_ok=True)
        self.writes = 0

    # -- storage ---------------------------------------------------------
    MAIN = 'BrOKenAG/ordina-releases'

    def _blob(self, repo):
        name = 'state.json' if repo == self.MAIN else 'manifest-v2.json'
        return self.root / 'repos' / repo / name

    def _head(self, data):
        return hashlib.sha256(data).hexdigest()[:16] if data else 'absent'

    def read(self, repo):
        self._check(repo)
        path = self._blob(repo)
        if not path.is_file():
            return 'absent', b''
        data = path.read_bytes()
        return self._head(data), data

    def write(self, repo, expected, data, token):
        self._check(repo)
        head, current = self.read(repo)
        if head != expected:
            _fail('head changed')
        limit = os.environ.get('ORDINA_DEV_LAB_FAIL_AFTER')
        if limit is not None and self.writes >= int(limit):
            _fail('injected write failure')
        self.writes += 1
        path = self._blob(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        new_head = self._head(data)
        marker = self.root / 'markers' / (repo.replace('/', '_') + '-' + new_head)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'previous': expected, 'token': token}),
                          encoding='utf-8')
        return new_head

    def is_transaction_commit(self, repo, head, previous, token):
        marker = self.root / 'markers' / (repo.replace('/', '_') + '-' + head)
        if not marker.is_file():
            return False
        record = json.loads(marker.read_text(encoding='utf-8'))
        return record.get('previous') == previous and record.get('token') == token

    # -- lock ------------------------------------------------------------
    def acquire(self, token):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'lock'
        current = path.read_text(encoding='utf-8').strip() if path.is_file() else ''
        if current in (token, ''):
            path.write_text(token, encoding='utf-8')
            return
        _fail('release lock is held')

    def release(self, token):
        path = self.root / 'lock'
        current = path.read_text(encoding='utf-8').strip() if path.is_file() else ''
        if current not in (token, ''):
            _fail('release lock ownership changed')
        path.write_text('', encoding='utf-8')

    # -- verification ----------------------------------------------------
    @staticmethod
    def _check(repo):
        if repo == 'BrOKenAG/ordina-releases':
            return
        if not LAB_REPO_RE.fullmatch(repo or ''):
            _fail('only isolated lab repositories are allowed')

    def asset_path(self, url):
        match = ASSET_RE.fullmatch(url or '')
        if not match:
            _fail('untrusted asset URL')
        self._check(match.group(1))
        return self.root / 'assets' / match.group(1) / match.group(2) / match.group(3)

    SIGNED_FEEDS = frozenset({'registry.json', 'plugins/manifest.json',
                              'manifest.json', 'manifest-v2.json'})

    def verify_assets(self, plan):
        """Every signed feed must reference the new asset with a matching hash.

        Only feeds and assets that live in the lab are inspected: the generated
        registry also lists real published plugins, whose assets are not part
        of a lab run. The invariant under test (new asset referenced by a
        signed feed, byte-exact) is unchanged.
        """
        from feed_signing import verify_envelope
        asset = plan['asset']
        path = self.asset_path(asset['url'])
        if not path.is_file():
            _fail('release asset is missing')
        data = path.read_bytes()
        if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
            _fail('public asset hash mismatch')
        references = []
        for repo, raw in plan['feeds'].items():
            self._check(repo)
            for path_name, encoded in json.loads(raw).items():
                if path_name not in self.SIGNED_FEEDS:
                    continue
                document = verify_envelope(base64.b64decode(encoded))
                if not isinstance(document.get('plugins'), dict):
                    _fail('invalid signed feed')
                references.extend(document['plugins'].values())
        if not any(item.get('url') == asset['url'] and item.get('sha256') == asset['sha256']
                   for item in references):
            _fail('new release asset is not referenced by a signed feed')
        for item in references:
            match = ASSET_RE.fullmatch(item.get('url') or '')
            if not match or not LAB_REPO_RE.fullmatch(match.group(1)):
                continue
            target = self.asset_path(item['url'])
            if not target.is_file():
                _fail('referenced asset is missing')
            if hashlib.sha256(target.read_bytes()).hexdigest() != item.get('sha256'):
                _fail('referenced asset hash mismatch')

    def verify_online(self, plan):
        for repo, raw in plan['feeds'].items():
            _, served = self.read(repo)
            if not served:
                _fail('online feed not yet available')
            document = json.loads(served)
            for path_name, encoded in json.loads(raw).items():
                if document.get(path_name) != encoded:
                    _fail('online feed not yet available or differs')


class LabGateway:
    """Side-effect surface for the six stages, backed by the lab directory."""

    def __init__(self, root):
        self.root = Path(root) / 'lab'
        self.assets = {}

    def preflight(self, plan):
        self.root.mkdir(parents=True, exist_ok=True)

    def release_exists(self, repository, tag):
        return any((self.root / 'assets' / repository / tag).glob('*'))

    def create_release(self, repository, tag, asset_path, title, notes, latest=False):
        target = self.root / 'assets' / repository / tag / Path(asset_path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = Path(asset_path).read_bytes()
        if os.environ.get('ORDINA_DEV_LAB_BAD_ASSET') == '1':
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        target.write_bytes(data)
        self.assets[tag] = data

    def http_get(self, url, dest, timeout):
        match = ASSET_RE.fullmatch(url or '')
        if not match:
            return '000'
        target = self.root / 'assets' / match.group(1) / match.group(2) / match.group(3)
        if not target.is_file():
            return '404'
        Path(dest).write_bytes(target.read_bytes())
        return '200'

    def verify_signature(self, path, signature):
        return True

    def write_feeds(self, feeds):
        pass

    def push(self, plan):
        pass

    def postflight(self, plan):
        pass


def reset(root):
    shutil.rmtree(Path(root) / 'lab', ignore_errors=True)
