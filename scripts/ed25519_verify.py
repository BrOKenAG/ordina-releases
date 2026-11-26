"""Verify Ed25519 signatures; no private-key or signing operations."""
import hashlib

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)


def recover(y, sign):
    xx = (y*y - 1) * pow(D*y*y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x*x - xx) % P:
        x = x * I % P
    if x % 2 != sign:
        x = P - x
    return x


def decode(raw):
    if len(raw) != 32:
        raise ValueError('invalid point')
    value = int.from_bytes(raw, 'little')
    y, sign = value & ((1 << 255) - 1), value >> 255
    if y >= P:
        raise ValueError('invalid point')
    x = recover(y, sign)
    if (y*y - x*x - 1 - D*x*x*y*y) % P or (x == 0 and sign):
        raise ValueError('invalid point')
    return (x, y, 1, x*y % P)


def add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1-x1)*(y2-x2) % P
    bb = (y1+x1)*(y2+x2) % P
    cc, dd = 2*D*t1*t2 % P, 2*z1*z2 % P
    e, f, g, h = bb-aa, dd-cc, dd+cc, bb+aa
    return (e*f % P, g*h % P, f*g % P, e*h % P)


IDENTITY = (0, 1, 1, 0)
BY = 4 * pow(5, P-2, P) % P
BX = recover(BY, 0)
BASE = (BX, BY, 1, BX*BY % P)


def multiply(point, scalar):
    result = IDENTITY
    while scalar:
        if scalar & 1:
            result = add(result, point)
        point = add(point, point)
        scalar >>= 1
    return result


def encode(point):
    x, y, z, _ = point
    inv = pow(z, P-2, P)
    x, y = x*inv % P, y*inv % P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def verify(key, message, signature):
    if len(key) != 32 or len(signature) != 64:
        return False
    try:
        point = decode(signature[:32])
        public = decode(key)
    except ValueError:
        return False
    scalar = int.from_bytes(signature[32:], 'little')
    if scalar >= Q or encode(multiply(public, Q)) != encode(IDENTITY):
        return False
    if encode(multiply(public, 8)) == encode(IDENTITY):
        return False
    if encode(multiply(point, Q)) != encode(IDENTITY):
        return False
    challenge = int.from_bytes(hashlib.sha512(signature[:32] + key + message).digest(), 'little') % Q
    return encode(multiply(BASE, scalar)) == encode(add(point, multiply(public, challenge)))
