from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from jssh.api.auth import signed_headers
from jssh.config import Config
from jssh.errors import ApiError, AuthError
from jssh.secrets import AccessKey

RETRY_STATUSES = {502, 503, 504}


class Client:
    def __init__(self, cfg: Config, key: AccessKey, transport: httpx.BaseTransport | None = None) -> None:
        self._cfg = cfg
        self._key = key
        self._client = httpx.Client(
            base_url=cfg.api_base,
            timeout=cfg.timeout,
            verify=cfg.tls_verify,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request_json("GET", path, params=params)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        response = self.request(method, path, params=params, json=json)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"Non-JSON response from {path} (HTTP {response.status_code})") from exc

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        items: list[Any] = []
        next_path = path
        next_params = dict(params or {})
        while next_path:
            payload = self.get_json(next_path, params=next_params or None)
            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                raise ApiError(f"Unexpected pagination payload from {path}")
            results = payload.get("results")
            if not isinstance(results, list):
                raise ApiError(f"Paginated response from {path} has no results list")
            items.extend(results)
            nxt = payload.get("next")
            if not nxt:
                break
            next_path, next_params = _split_next(self._cfg.api_base, str(nxt))
        return items

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        url = path if path.startswith("http") else urljoin(self._cfg.api_base + "/", path.lstrip("/"))
        extra: dict[str, str] = {}
        if self._cfg.org_id:
            extra["X-JMS-ORG"] = self._cfg.org_id
        attempts = max(1, self._cfg.retries)
        last_error: Exception | None = None
        for attempt in range(attempts):
            headers = signed_headers(self._key.key_id, self._key.secret, method, _url_with_params(url, params), extra=extra)
            try:
                response = self._client.request(method, url, params=params, json=json, headers=headers)
            except httpx.TimeoutException:
                last_error = ApiError(f"Timed out calling {path}")
                _sleep(attempt)
                continue
            except httpx.HTTPError:
                last_error = ApiError(f"Network error calling {path}")
                _sleep(attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt + 1 < attempts:
                _sleep(attempt)
                continue
            return _raise_for_status(response, path)
        raise last_error or ApiError(f"Request failed: {path}")


def _url_with_params(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    request = httpx.Request("GET", url, params=params)
    return str(request.url)


def _split_next(api_base: str, nxt: str) -> tuple[str, dict[str, Any]]:
    if nxt.startswith("http"):
        parts = urlsplit(nxt)
        path = parts.path
        query = dict(httpx.URL(nxt).params)
        return path, query
    url = urljoin(api_base + "/", nxt)
    parts = urlsplit(url)
    return parts.path, dict(httpx.URL(url).params)


def _raise_for_status(response: httpx.Response, path: str) -> httpx.Response:
    status = response.status_code
    if status < 400:
        return response
    if status in (401, 403):
        raise AuthError(
            f"JumpServer rejected the request (HTTP {status}) for {path}. "
            "Check AK/SK, clock skew, org_id, and that the key belongs to this user."
        )
    if status == 404:
        raise ApiError(
            f"Endpoint not found (HTTP 404): {path}. "
            "This build targets JumpServer 2.4.1 paths. Run jssh doctor --probe.",
            status=404,
        )
    raise ApiError(f"JumpServer returned HTTP {status} for {path}", status=status)


def _sleep(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 4))
