"""Hermetic Ed25519 signing for the isolated lab only.

Production feeds are signed by Sparkle's ``sign_update`` with the developer's
keychain key. That key must never be reachable from an automated test, and a
test must never depend on a key being present in whatever keychain the process
happens to inherit. A lab build therefore signs with a throwaway key file that
the test creates: no Keychain, no network, no state outside the sandbox.

This module is bundled into the lab tool only (see ``build_dev_tool.py``). It
implements RFC 8032 Ed25519 in pure Python. Signatures are interchangeable with
Sparkle's: the client verifies them with CryptoKit's
``Curve25519.Signing.PublicKey``, which is an independent implementation, so a
mistake here cannot pass unnoticed.

    python3 -c "import lab_signing; print(lab_signing.public_key_base64_file('k'))"
"""
import base64
import hashlib
from pathlib import Path

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_BY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y, sign):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _SQRT_M1 % _P
    if x % 2 != sign:
        x = _P - x
    return x


_BX = _recover_x(_BY, 0)
_BASE = (_BX, _BY, 1, _BX * _BY % _P)
_IDENTITY = (0, 1, 1, 0)


def _add(first, second):
    x1, y1, z1, t1 = first
    x2, y2, z2, t2 = second
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _multiply(point, scalar):
    result = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _encode(point):
    x, y, z, _ = point
    inverse = pow(z, _P - 2, _P)
    x, y = x * inverse % _P, y * inverse % _P
    value = y | ((x & 1) << 255)
    return value.to_bytes(32, 'little')


def _decode(raw):
    if len(raw) != 32:
        raise ValueError('签名点必须是 32 字节。')
    value = int.from_bytes(raw, 'little')
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError('签名点超出范围。')
    x = _recover_x(y, sign)
    if (y * y - x * x - 1 - _D * x * x * y * y) % _P:
        raise ValueError('签名点不在曲线上。')
    return (x, y, 1, x * y % _P)


def _clamped(seed):
    if len(seed) != 32:
        raise ValueError('实验室签名密钥必须是 32 字节。')
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], 'little')
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed):
    """Raw 32-byte public key for a 32-byte seed."""
    scalar, _ = _clamped(seed)
    return _encode(_multiply(_BASE, scalar))


def signature(seed, message):
    """Raw 64-byte Ed25519 signature over ``message``."""
    scalar, prefix = _clamped(seed)
    key = public_key(seed)
    nonce = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % _Q
    point = _encode(_multiply(_BASE, nonce))
    challenge = int.from_bytes(hashlib.sha512(point + key + message).digest(),
                               'little') % _Q
    value = (nonce + challenge * scalar) % _Q
    return point + value.to_bytes(32, 'little')


def verify(key, message, signature_bytes):
    """Self-check used by the signer before a feed is ever published."""
    if len(signature_bytes) != 64:
        return False
    try:
        point = _decode(signature_bytes[:32])
        public = _decode(key)
    except ValueError:
        return False
    value = int.from_bytes(signature_bytes[32:], 'little')
    if value >= _Q:
        return False
    challenge = int.from_bytes(hashlib.sha512(signature_bytes[:32] + key + message)
                               .digest(), 'little') % _Q
    # R + [S]B == [challenge]A  <=>  [S]B - [challenge]A == R
    left = _add(_multiply(_BASE, value),
                _multiply(public, (_Q - challenge) % _Q))
    return _encode(left) == signature_bytes[:32]


def read_seed(path):
    """Read a seed file: 64 hex characters, 32 raw bytes or base64 of either."""
    raw = Path(path).read_bytes().strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw.decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            pass
    if len(raw) == 32:
        return raw
    decoded = base64.b64decode(raw, validate=True)
    if len(decoded) == 32:
        return decoded
    raise ValueError('实验室签名密钥必须是 32 字节（hex、base64 或裸字节）。')


def public_key_base64_file(path):
    return base64.b64encode(public_key(read_seed(path))).decode('ascii')
