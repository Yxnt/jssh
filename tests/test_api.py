from __future__ import annotations

import httpx
import pytest

from jssh.api.client import Client
from jssh.errors import ApiError, AuthError
from jssh.secrets import AccessKey


def _client(cfg, key: AccessKey, handler) -> Client:
    return Client(cfg, key, transport=httpx.MockTransport(handler))


def test_401_is_auth_error_without_secret(tmp_cfg, access_key: AccessKey) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Signature" in request.headers["Authorization"]
        assert access_key.secret not in request.headers["Authorization"]
        return httpx.Response(401, json={"detail": "Invalid signature."})

    client = _client(tmp_cfg, access_key, handler)
    with pytest.raises(AuthError) as exc:
        client.get_json("/api/v1/users/profile/")
    message = str(exc.value)
    assert "401" in message
    assert access_key.secret not in message
    assert access_key.key_id not in message


def test_403_is_auth_error(tmp_cfg, access_key: AccessKey) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    client = _client(tmp_cfg, access_key, handler)
    with pytest.raises(AuthError) as exc:
        client.get_json("/api/v1/perms/users/assets/")
    assert "403" in str(exc.value)


def test_404_names_the_path(tmp_cfg, access_key: AccessKey) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    client = _client(tmp_cfg, access_key, handler)
    with pytest.raises(ApiError) as exc:
        client.get_json("/api/v1/does-not-exist/")
    assert "404" in str(exc.value)
    assert "/api/v1/does-not-exist/" in str(exc.value)


def test_paginate_follows_next(tmp_cfg, access_key: AccessKey) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "offset=15" in str(request.url):
            return httpx.Response(
                200,
                json={"count": 2, "next": None, "results": [{"id": "b", "hostname": "two", "ip": "10.0.0.2"}]},
            )
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": "https://jumpserver.example.com/api/v1/perms/users/nodes/n/assets/?limit=15&offset=15",
                "results": [{"id": "a", "hostname": "one", "ip": "10.0.0.1"}],
            },
        )

    client = _client(tmp_cfg, access_key, handler)
    rows = client.paginate("/api/v1/perms/users/nodes/n/assets/", params={"limit": 15})
    assert [row["hostname"] for row in rows] == ["one", "two"]
    assert len(calls) == 2


def test_list_payload_short_circuits_pagination(tmp_cfg, access_key: AccessKey) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "a", "hostname": "one"}])

    client = _client(tmp_cfg, access_key, handler)
    rows = client.paginate("/api/v1/perms/users/assets/")
    assert rows[0]["hostname"] == "one"


def test_org_header_sent_when_configured(tmp_cfg, access_key: AccessKey) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"id": "u1", "username": "alice"})

    client = _client(tmp_cfg, access_key, handler)
    client.get_json("/api/v1/users/profile/")
    assert seen.get("x-jms-org") == "DEFAULT"
