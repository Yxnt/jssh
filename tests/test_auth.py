from __future__ import annotations

import base64
import hashlib
import hmac

from jssh.api.auth import authorization_header, signed_headers, signing_string


DATE = "Tue, 17 Nov 2020 08:19:41 GMT"
KEY_ID = "11111111-1111-1111-1111-111111111111"
SECRET = "test-secret"
URL = "https://jumpserver.example.com/api/v1/users/profile/"


def _expected(method: str, url: str, date: str) -> str:
    message = signing_string(method, url, "application/json", date).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_signing_string_matches_httpsig_header_order() -> None:
    text = signing_string("GET", URL, "application/json", DATE)
    assert text == (
        "(request-target): get /api/v1/users/profile/\n"
        "accept: application/json\n"
        f"date: {DATE}"
    )


def test_request_target_includes_query_string() -> None:
    text = signing_string(
        "GET",
        "https://jumpserver.example.com/api/v1/perms/users/nodes/abc/assets/?limit=15&offset=15",
        "application/json",
        DATE,
    )
    assert text.startswith(
        "(request-target): get /api/v1/perms/users/nodes/abc/assets/?limit=15&offset=15\n"
    )


def test_authorization_header_format() -> None:
    sig = _expected("GET", URL, DATE)
    header = authorization_header(KEY_ID, sig)
    assert header.startswith("Signature ")
    assert f'keyId="{KEY_ID}"' in header
    assert 'algorithm="hmac-sha256"' in header
    assert 'headers="(request-target) accept date"' in header
    assert f'signature="{sig}"' in header
    assert SECRET not in header


def test_signed_headers_do_not_leak_secret() -> None:
    headers = signed_headers(KEY_ID, SECRET, "GET", URL, date=DATE)
    blob = " ".join(headers.values())
    assert SECRET not in blob
    assert headers["Accept"] == "application/json"
    assert headers["Date"] == DATE
    assert headers["Authorization"] == authorization_header(KEY_ID, _expected("GET", URL, DATE))
