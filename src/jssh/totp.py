from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

from jssh.errors import AuthError

_HASHES = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}


def decode_totp_secret(secret: str) -> bytes:
    cleaned = "".join(secret.split()).upper().replace("-", "")
    if not cleaned:
        raise AuthError("TOTP secret is empty")
    pad = (-len(cleaned)) % 8
    padded = cleaned + ("=" * pad)
    try:
        return base64.b32decode(padded, casefold=True)
    except Exception as exc:
        raise AuthError("TOTP secret is not valid base32") from exc


def totp_code(
    secret: str,
    *,
    timestamp: float | None = None,
    digits: int = 6,
    period: int = 30,
    algorithm: str = "sha1",
) -> str:
    key = decode_totp_secret(secret)
    digestmod = _HASHES.get(algorithm.lower())
    if digestmod is None:
        raise AuthError("TOTP algorithm must be sha1, sha256, or sha512")
    if digits < 6 or digits > 8:
        raise AuthError("TOTP digits must be 6, 7, or 8")
    if period <= 0:
        raise AuthError("TOTP period must be positive")
    counter = int((time.time() if timestamp is None else timestamp) // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, digestmod).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)
