"""Production transaction adapter for durable cross-repository releases.

Every repository, download URL and Pages URL is checked against an explicit
allowlist. Asset references are read only from signed feeds, downloaded over
public HTTPS and compared byte-for-byte before any repository is written.
``verify_online`` waits for GitHub Pages propagation; a timeout leaves the
journal resumable instead of marking the release complete.
"""
import base64
import hashlib
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from github_lab_backend import GitHubLabMultiBackend
from release_transaction import validate_feed_transition

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
FEED_PATHS = frozenset({'appcast.xml', 'registry.json', 'plugins/manifest.json',
                        'manifest.json', 'manifest-v2.json'})
PAGES_ATTEMPTS = 4
PAGES_DELAY = 5


class ProductionBackend(GitHubLabMultiBackend):
    @staticmethod
    def valid_allowlist(repositories):
        return bool(repositories) and all(
            isinstance(repo, str) and re.fullmatch(r'BrOKenAG/(?:ordina-releases|ordina-plugin-[a-z0-9-]+)', repo)
            for repo in repositories)

    @staticmethod
    def _safe_url(value, host):
        try:
            url = urlsplit(value)
            if (url.scheme != 'https' or url.hostname != host or url.username is not None or
                url.password is not None or url.port is not None or url.query or url.fragment):
                return None
            decoded = unquote(url.path)
            if (unquote(decoded) != decoded or '\\' in decoded or any(ord(c) < 32 for c in decoded) or
                any(part in ('.','..') for part in decoded.split('/'))):
                return None
            return decoded
        except (TypeError, ValueError):
            return None

    @classmethod
    def valid_asset_url(cls, value, allowed):
        path = cls._safe_url(value, 'github.com')
        if path is None or not cls.valid_allowlist(allowed): return False
        for repo in allowed:
            prefix = '/' + repo + '/releases/download/'
            if path.startswith(prefix):
                parts = path[len(prefix):].split('/')
                return len(parts) == 2 and all(parts)
        return False

    @classmethod
    def valid_pages_url(cls, value, allowed):
        path = cls._safe_url(value, 'brokenag.github.io')
        if path is None or not cls.valid_allowlist(allowed): return False
        return any(path.startswith('/' + repo.split('/', 1)[1] + '/') for repo in allowed)

    def __init__(self, lock_repo, asset_repo, asset_tag, asset_name, paths, allowed):
        self.allowed = frozenset(allowed)
        if not self.valid_allowlist(self.allowed) or lock_repo != 'BrOKenAG/ordina-releases':
            raise ValueError('untrusted repository configuration')
        super().__init__(lock_repo, asset_repo, asset_tag, asset_name, paths)

    def _check(self, repo):
        if repo not in self.allowed:
            raise ValueError('repository is not explicitly authorized')

    @staticmethod
    def _fetch_public(url):
        with tempfile.TemporaryDirectory(prefix='ordina-public-check-') as tmp:
            target = Path(tmp) / 'download'
            subprocess.run(['/usr/bin/curl', '--fail', '--location', '--silent', '--show-error',
                            '--max-time', '90', '--max-filesize', '1073741824', url,
                            '-o', str(target)], check=True, timeout=100)
            return target.read_bytes()

    def _feed_documents(self, plan):
        """Return (repo, path, bytes, decoded_document|None) for signed feeds."""
        feeds = plan.get('feeds')
        if not isinstance(feeds, dict) or set(feeds) != set(self.paths):
            raise ValueError('missing transaction repositories')
        documents = []
        for repo, raw in feeds.items():
            self._check(repo)
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict) or set(snapshot) != set(self.paths[repo]):
                raise ValueError('transaction file set mismatch')
            for path, encoded in snapshot.items():
                if path not in FEED_PATHS:
                    continue
                if encoded is None:
                    raise ValueError('cannot delete a signed feed during publication')
                content = base64.b64decode(encoded, validate=True)
                document = None
                if path != 'appcast.xml':
                    from feed_signing import verify_envelope
                    document = verify_envelope(content)
                    if document.get('format') not in (1, 2) or not isinstance(document.get('plugins'), dict):
                        raise ValueError('invalid signed feed')
                documents.append((repo, path, content, document))
        if not documents:
            raise ValueError('missing signed feeds')
        return documents

    @staticmethod
    def _versions(raw):
        root = ET.fromstring(raw)
        return [item.findtext(SPARKLE + 'version') for item in root.findall('./channel/item')]

    def _validate_appcast_transition(self, old, new):
        before, after = self._versions(old), self._versions(new)
        if any(version not in after for version in before):
            raise ValueError('appcast 会删除历史版本，请先人工审查')

    def validate_transitions(self, plan):
        from feed_signing import verify_envelope
        for repo, raw in plan['feeds'].items():
            _, current_bytes = self.read(repo)
            current, desired = json.loads(current_bytes), json.loads(raw)
            for path, new_encoded in desired.items():
                if path not in FEED_PATHS:
                    continue
                old_encoded = current.get(path)
                if old_encoded is None:
                    if path == 'registry.json':
                        raise ValueError('current registry is missing')
                    continue
                old = base64.b64decode(old_encoded, validate=True)
                new = base64.b64decode(new_encoded, validate=True)
                if path == 'registry.json':
                    if verify_envelope(old) != verify_envelope(new):
                        raise ValueError('registry changes require separate review')
                elif path == 'appcast.xml':
                    self._validate_appcast_transition(old, new)
                else:
                    validate_feed_transition(old, new, verify_envelope)

    def _validate_appcast(self, plan, documents, asset):
        for _, path, content, _ in documents:
            if path != 'appcast.xml':
                continue
            enclosure = None
            for item in ET.fromstring(content).findall('./channel/item'):
                node = item.find('enclosure')
                if node is not None and node.get('url') == asset['url']:
                    enclosure = node
                    break
            if enclosure is None:
                raise ValueError('appcast 未引用本次发布的资产')
            if enclosure.get('length') != str(asset['size']):
                raise ValueError('appcast 记录的大小与资产不符')
            signature = enclosure.get(SPARKLE + 'edSignature', '')
            if not signature or signature != asset.get('ed_signature'):
                raise ValueError('appcast 的 Ed25519 签名与资产不符')
            if 'edSignature:' not in content.decode('utf-8', 'replace'):
                raise ValueError('appcast 缺少签名尾注')

    def verify_assets(self, plan):
        """Re-download every referenced asset before any feed is written."""
        asset = plan['asset']
        primary = asset.get('url') or (
            'https://github.com/' + self.asset_repo + '/releases/download/'
            + quote(self.asset_tag, safe='') + '/' + quote(self.asset_name, safe=''))
        if not self.valid_asset_url(primary, self.allowed):
            raise ValueError('untrusted release asset URL')
        data = self._fetch_public(primary)
        if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise RuntimeError('public asset hash mismatch')
        if asset.get('ed_signature'):
            from feed_signing import sign_tool
            from sparkle_tools import KEYCHAIN_ACCOUNT
            with tempfile.TemporaryDirectory(prefix='ordina-asset-verify-') as tmp:
                path = Path(tmp) / 'asset'
                path.write_bytes(data)
                result = subprocess.run([str(sign_tool()), '--account', KEYCHAIN_ACCOUNT,
                                         '--verify', str(path), asset['ed_signature']],
                                        capture_output=True, text=True)
                if result.returncode:
                    raise ValueError('Ed25519 签名不匹配')

        documents = self._feed_documents(plan)
        references = []
        for _, _, _, document in documents:
            if document and document['format'] == 1:
                references.extend(document['plugins'].values())
        if not references:
            raise ValueError('missing signed-feed asset references')
        if not any(item.get('url') == primary and item.get('sha256') == asset['sha256']
                   for item in references):
            raise ValueError('new release asset is not referenced by a signed feed')
        self.validate_transitions(plan)
        self._validate_appcast(plan, documents, asset)

        checked = set()
        for item in references:
            url, expected = item.get('url'), item.get('sha256')
            if (not self.valid_asset_url(url, self.allowed) or not isinstance(expected, str)
                    or not re.fullmatch('[0-9a-f]{64}', expected)):
                raise ValueError('untrusted asset reference')
            if (url, expected) in checked:
                continue
            if hashlib.sha256(self._fetch_public(url)).hexdigest() != expected:
                raise RuntimeError('public asset hash mismatch')
            checked.add((url, expected))

    def verify_online(self, plan):
        """Completion requires the externally served bytes, with Pages retries."""
        expected = [(repo, path, content)
                    for repo, path, content, _ in self._feed_documents(plan)]
        last = None
        for attempt in range(PAGES_ATTEMPTS):
            try:
                for repo, path, content in expected:
                    url = ('https://brokenag.github.io/' + repo.split('/', 1)[1]
                           + '/' + quote(path, safe='/'))
                    if not self.valid_pages_url(url, self.allowed):
                        raise ValueError('untrusted Pages URL')
                    if self._fetch_public(url) != content:
                        raise RuntimeError('online feed not yet available or differs')
                return
            except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
                last = error
                if attempt + 1 < PAGES_ATTEMPTS:
                    print(f'Pages 第 {attempt + 1} 次未生效，等待后重试…', flush=True)
                    time.sleep(PAGES_DELAY)
        raise RuntimeError('Pages 内容尚未生效，事务保持可恢复：' + str(last))
