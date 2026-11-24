"""One place that turns a document into a signed feed envelope.

Every published feed -- the remote registry, the legacy combined plugin feed,
and each plugin's own feed -- uses the same Ed25519 key held in the local
keychain account ``ordina-sparkle``. Keeping signing here means no feed is ever
assembled or signed by hand, and every consumer can verify with the single
public key already embedded in the app.
"""
import base64
import json
import subprocess
import tempfile
from pathlib import Path

from sparkle_tools import ensure_tools, KEYCHAIN_ACCOUNT

ROOT = Path(__file__).resolve().parents[1]


def canonical(document):
    """Deterministic bytes: identical input always yields identical payload."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sign_tool():
    return Path(ensure_tools()) / 'sign_update'


def sign_document(document, tool=None):
    """Return (canonical payload bytes, base64 Ed25519 signature)."""
    data = canonical(document)
    tool = tool or sign_tool()
    with tempfile.TemporaryDirectory(prefix='ordina-feed-sign-') as temporary:
        payload = Path(temporary) / 'payload.json'
        payload.write_bytes(data)
        result = subprocess.run([str(tool), '--account', KEYCHAIN_ACCOUNT,
                                 '-p', str(payload)], capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError('签名失败：' + (result.stderr.strip() or result.stdout.strip()))
        signature = result.stdout.strip()
        check = subprocess.run([str(tool), '--account', KEYCHAIN_ACCOUNT,
                                '--verify', str(payload), signature],
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise ValueError('签名自校验失败。')
    return data, signature


def envelope(document, tool=None):
    data, signature = sign_document(document, tool)
    return {'payload': base64.b64encode(data).decode('ascii'), 'signature': signature}


def envelope_bytes(document, tool=None):
    return (json.dumps(envelope(document, tool), ensure_ascii=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def verify_envelope(raw, tool=None):
    """Decode an envelope only after its signature validates."""
    obj = json.loads(raw)
    payload = base64.b64decode(obj['payload'], validate=True)
    signature = obj.get('signature', '')
    tool = tool or sign_tool()
    with tempfile.TemporaryDirectory(prefix='ordina-feed-verify-') as temporary:
        path = Path(temporary) / 'payload.json'
        path.write_bytes(payload)
        result = subprocess.run([str(tool), '--account', KEYCHAIN_ACCOUNT,
                                 '--verify', str(path), signature],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError('清单签名无效。')
    return json.loads(payload)


def decode_envelope(raw):
    """Structure-only decode for offline checks that verify signatures separately."""
    obj = json.loads(raw)
    return json.loads(base64.b64decode(obj['payload'], validate=True)), obj.get('signature', '')
