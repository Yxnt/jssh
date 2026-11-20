from __future__ import annotations

from jssh.models.asset import Asset, RecentEntry


def host_tokens(assets: list[Asset], recent: list[RecentEntry] | None = None) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for item in recent or []:
        _add(tokens, seen, item.hostname)
    for asset in assets:
        _add(tokens, seen, asset.hostname)
    return tokens


def account_tokens(
    assets: list[Asset],
    recent: list[RecentEntry] | None = None,
    query: str | None = None,
) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    needle = (query or "").strip()
    if needle:
        for item in recent or []:
            if _asset_matches(item.hostname, item.asset_id, needle):
                _add(tokens, seen, item.account_name)
        for asset in assets:
            if _asset_matches(asset.hostname, asset.id, needle, asset.ip):
                for account in asset.accounts:
                    _add(tokens, seen, account.name)
        return tokens
    for item in recent or []:
        _add(tokens, seen, item.account_name)
    for asset in assets:
        for account in asset.accounts:
            _add(tokens, seen, account.name)
    return tokens


def _add(tokens: list[str], seen: set[str], value: str) -> None:
    if value and value not in seen:
        seen.add(value)
        tokens.append(value)


def _asset_matches(hostname: str, asset_id: str, needle: str, ip: str = "") -> bool:
    return needle in {hostname, asset_id, ip}
