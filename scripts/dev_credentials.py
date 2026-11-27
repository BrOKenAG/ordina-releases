"""Developer publishing credentials: independent verification, Keychain storage.

A credential is a *signed* document, not a password and not a token. It is
signed with the same Ed25519 release key that signs feeds, so the trust root is
already pinned in the shipped client; the publishing tool verifies the
signature itself and then checks expiry, scope and the repository allowlist.

Nothing here is ever bundled into ordina.app. The tool reads the credential
from the macOS Keychain (``ordina-dev-publisher``), and only in an explicitly
labelled lab run may it read an owner-only file instead. No credential, token
or private key is written to the repository, a log, a journal or JSON output.
"""
import fnmatch
import json
import os
import stat
import subprocess
from pathlib import Path

KEYCHAIN_SERVICE = 'ordina-dev-publisher'
KEYCHAIN_ACCOUNT = 'default'
SCOPED_KEYCHAIN_SERVICE = 'ordina-dev-publisher.scoped'
KEY_ID = 'ordina-feed-1'
MAX_LIFETIME = 400 * 24 * 3600
SCOPES = ('plugin.publish', 'plugin.localTest', 'plugin.rollback',
          'tool.install', 'repository.create')
REPO_PATTERN = 'BrOKenAG/ordina-plugin-*'
LAB_ENV = 'ORDINA_DEV_LAB'


class CredentialError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise CredentialError(code, message)


def _is_lab():
    return os.environ.get(LAB_ENV) == '1'


def _read_keychain(service=KEYCHAIN_SERVICE, account=KEYCHAIN_ACCOUNT):
    result = subprocess.run(
        ['/usr/bin/security', 'find-generic-password', '-s', service,
         '-a', account, '-w'], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def read_raw(path=None):
    """Return (raw credential text, source label). Never logs the content."""
    if path is not None:
        if not _is_lab():
            _fail('auth.lab', '从文件读取授权只允许在隔离环境中使用。')
        candidate = Path(path).expanduser()
        try:
            mode = candidate.stat().st_mode
        except OSError:
            _fail('auth.missing', '找不到授权文件。')
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _fail('auth.permissions', '授权文件权限过宽，需要 0600。')
        return candidate.read_text(encoding='utf-8').strip(), 'file'
    if _is_lab():
        _fail('auth.missing', '隔离环境缺少授权文件，已停止以避免访问钥匙串。')
    raw = _read_keychain()
    if not raw:
        _fail('auth.missing', '钥匙串中没有开发者授权（ordina-dev-publisher）。')
    return raw, 'keychain'


def store(raw):
    """Verify before writing, then replace the Keychain item."""
    if _is_lab():
        _fail('auth.lab', '隔离环境不允许修改钥匙串。')
    verify_raw(raw)
    result = subprocess.run(
        ['/usr/bin/security', 'add-generic-password', '-U', '-s', KEYCHAIN_SERVICE,
         '-a', KEYCHAIN_ACCOUNT, '-w', raw.strip()],
        capture_output=True, text=True)
    if result.returncode != 0:
        _fail('auth.keychain', '无法写入钥匙串：' + result.stderr.strip())
    return {'source': 'keychain', 'service': KEYCHAIN_SERVICE}


def remove():
    if _is_lab():
        _fail('auth.lab', '隔离环境不允许修改钥匙串。')
    result = subprocess.run(
        ['/usr/bin/security', 'delete-generic-password', '-s', KEYCHAIN_SERVICE,
         '-a', KEYCHAIN_ACCOUNT], capture_output=True, text=True)
    return result.returncode == 0


def verify_raw(raw):
    """Verify the envelope signature and return the credential document."""
    if not isinstance(raw, str) or not raw.strip():
        _fail('auth.invalid', '授权内容为空。')
    if len(raw) > 8192:
        _fail('auth.invalid', '授权内容过大。')
    try:
        envelope = json.loads(raw)
        payload = envelope['payload']
        signature = envelope['signature']
    except (ValueError, KeyError, TypeError):
        _fail('auth.invalid', '授权格式无效。')
    if not isinstance(payload, str) or not isinstance(signature, str):
        _fail('auth.invalid', '授权格式无效。')
    try:
        from feed_signing import verify_envelope
        document = verify_envelope(raw)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        _fail('auth.signature', '授权签名无效：' + str(error))
    return document


def check(document, now=None, scope=None, repository=None):
    """Validate claims. Raises CredentialError with a stable code."""
    import time
    now = now if now is not None else int(time.time())
    if not isinstance(document, dict) or document.get('format') != 1:
        _fail('auth.invalid', '授权文档格式无效。')
    developer = document.get('developer')
    if not isinstance(developer, str) or not 1 <= len(developer) <= 64:
        _fail('auth.invalid', '授权开发者标识无效。')
    if document.get('keyId') != KEY_ID:
        _fail('auth.signature', '授权签名密钥不受信任。')
    issued, not_before, expires = (document.get('issuedAt'), document.get('notBefore'),
                                   document.get('expires'))
    if not all(isinstance(value, int) for value in (issued, not_before, expires)):
        _fail('auth.invalid', '授权有效期无效。')
    if expires <= issued or expires - issued > MAX_LIFETIME:
        _fail('auth.invalid', '授权有效期过长或倒置。')
    if now < not_before - 300:
        _fail('auth.notyet', '授权尚未生效。')
    if now > expires:
        _fail('auth.expired', '授权已过期，请重新签发。')
    scopes = document.get('scopes')
    if (not isinstance(scopes, list) or not scopes
            or any(item not in SCOPES for item in scopes)):
        _fail('auth.invalid', '授权范围无效。')
    repositories = document.get('repositories')
    if (not isinstance(repositories, list) or not 1 <= len(repositories) <= 8
            or any(not isinstance(item, str) or not item.startswith('BrOKenAG/')
                   or '*' in item.split('/', 1)[0] or len(item) > 80
                   for item in repositories)):
        _fail('auth.invalid', '授权仓库范围无效。')
    if scope is not None and scope not in scopes:
        _fail('auth.scope', f'授权不包含所需权限：{scope}')
    if repository is not None:
        if not any(fnmatch.fnmatchcase(repository, pattern) for pattern in repositories):
            _fail('auth.repository', f'授权不包含仓库：{repository}')
        if not fnmatch.fnmatchcase(repository, REPO_PATTERN):
            _fail('auth.repository', f'仓库不在发布策略允许范围内：{repository}')
    return True


def status(path=None):
    """Non-secret summary for doctor/UI output."""
    try:
        raw, source = read_raw(path)
        document = verify_raw(raw)
        check(document)
    except CredentialError as error:
        return {'valid': False, 'code': error.code, 'message': str(error)}
    return {
        'valid': True, 'source': source, 'developer': document['developer'],
        'keyId': document['keyId'], 'expires': document['expires'],
        'scopes': document['scopes'], 'repositories': document['repositories'],
    }


def load(path=None, scope=None, repository=None):
    """Read, verify and authorise one credential for a concrete action."""
    raw, source = read_raw(path)
    document = verify_raw(raw)
    try:
        check(document, scope=scope, repository=repository)
    except CredentialError as error:
        if (error.code not in ('auth.scope', 'auth.repository') or path is not None
                or _is_lab() or not repository):
            raise
        scoped = _read_keychain(SCOPED_KEYCHAIN_SERVICE, repository)
        if not scoped:
            raise
        document = verify_raw(scoped)
        check(document, scope=scope, repository=repository)
        if document.get('repositories') != [repository]:
            _fail('auth.repository', '临时授权仓库范围无效。')
        source = 'keychain-scoped'
    return document, source


def store_scoped(raw, repository):
    """Store a signed, narrowly scoped authorization without replacing the original."""
    if _is_lab():
        _fail('auth.lab', '隔离环境不允许修改钥匙串。')
    document = verify_raw(raw)
    check(document, scope='plugin.publish', repository=repository)
    if document.get('repositories') != [repository] or document['expires'] - document['issuedAt'] > 1800:
        _fail('auth.invalid', '临时授权范围或期限无效。')
    result = subprocess.run(['/usr/bin/security', 'add-generic-password', '-U', '-s',
                             SCOPED_KEYCHAIN_SERVICE, '-a', repository, '-w', raw.strip()],
                            capture_output=True, text=True)
    if result.returncode != 0:
        _fail('auth.keychain', '无法保存临时授权。')
