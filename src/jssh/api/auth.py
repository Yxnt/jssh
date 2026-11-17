from __future__ import annotations

import base64
import hashlib
import hmac
from email.utils import formatdate
from urllib.parse import urlsplit


SIGNED_HEADERS = ("(request-target)", "accept", "date")
ALGORITHM = "hmac-sha256"
ACCEPT = "application/json"


def gmt_date(timestamp: float | None = None) -> str:
    return formatdate(timeval=timestamp, usegmt=True)


def request_target(method: str, url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return f"{method.lower()} {path}"


def signing_string(method: str, url: str, accept: str, date: str) -> str:
    target = request_target(method, url)
    return "\n".join(
        [
            f"(request-target): {target}",
            f"accept: {accept}",
            f"date: {date}",
        ]
    )


def signature_b64(secret: str, method: str, url: str, accept: str, date: str) -> str:
    message = signing_string(method, url, accept, date).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def authorization_header(key_id: str, signature: str) -> str:
    headers = " ".join(SIGNED_HEADERS)
    return (
        f'Signature keyId="{key_id}",algorithm="{ALGORITHM}",'
        f'headers="{headers}",signature="{signature}"'
    )


def signed_headers(
    key_id: str,
    secret: str,
    method: str,
    url: str,
    *,
    date: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    date_value = date or gmt_date()
    sig = signature_b64(secret, method, url, ACCEPT, date_value)
    headers = {
        "Accept": ACCEPT,
        "Date": date_value,
        "Authorization": authorization_header(key_id, sig),
    }
    if extra:
        headers.update(extra)
    return headers
