from __future__ import annotations

from datetime import datetime, timezone

from jssh.api.assets import fetch_accounts, fetch_granted_assets, fetch_profile
from jssh.api.client import Client
from jssh.cache import (
    is_fresh,
    load_assets,
    save_assets,
    save_recent,
)
from jssh.config import Config
from jssh.errors import ApiError, ConfigError
from jssh.models.asset import Account, Asset, Profile, RecentEntry
from jssh.secrets import AccessKey, load_access_key
from jssh.ui.fzf import filter_assets


def open_client(cfg: Config, key: AccessKey | None = None) -> Client:
    return Client(cfg, key or load_access_key(cfg))


def sync_assets(cfg: Config, client: Client | None = None) -> tuple[Profile, list[Asset]]:
    own = client is None
    session = client or open_client(cfg)
    try:
        profile = fetch_profile(session)
        assets = fetch_granted_assets(session)
        save_assets(cfg, assets)
        return profile, assets
    finally:
        if own:
            session.close()


def load_or_sync(
    cfg: Config,
    *,
    force: bool = False,
    allow_stale: bool = True,
) -> tuple[list[Asset], bool]:
    cached = load_assets(cfg)
    if cached and not force and is_fresh(cfg, cached[1]):
        return cached[0], False
    try:
        _, assets = sync_assets(cfg)
        return assets, False
    except (ApiError, OSError):
        if cached and allow_stale:
            return cached[0], True
        raise


def accounts_for(cfg: Config, asset: Asset, client: Client | None = None) -> list[Account]:
    if asset.accounts:
        return list(asset.accounts)
    overrides = cfg.account_overrides.get(asset.id) or cfg.account_overrides.get(asset.hostname)
    own = client is None
    session = client or open_client(cfg)
    try:
        try:
            accounts = fetch_accounts(session, asset.id)
        except ApiError:
            accounts = []
        if accounts:
            _remember_accounts(cfg, asset, accounts)
            return accounts
        if overrides:
            mapped = [
                Account(id=f"override:{name}", name=name, username=name)
                for name in overrides
            ]
            return mapped
        raise ConfigError(
            f"No system accounts for {asset.hostname} from "
            f"/api/v1/perms/users/assets/{asset.id}/system-users/. "
            "Add sftp.account_overrides after you confirm the account names on JumpServer. "
            "Do not guess."
        )
    finally:
        if own:
            session.close()


def remember_use(cfg: Config, asset: Asset, account: Account, action: str) -> None:
    save_recent(
        cfg,
        RecentEntry(
            asset_id=asset.id,
            node_key=asset.node.key if asset.node else "",
            account_id=account.id,
            hostname=asset.hostname,
            account_name=account.name,
            ts=datetime.now(timezone.utc).isoformat(),
            action=action,
        ),
    )


def resolve_query(assets: list[Asset], query: str | None) -> list[Asset]:
    if not query:
        return assets
    matched = filter_assets(assets, query)
    exact = [item for item in matched if item.id == query or item.hostname == query]
    return exact or matched


def _remember_accounts(cfg: Config, asset: Asset, accounts: list[Account]) -> None:
    cached = load_assets(cfg)
    if not cached:
        return
    items, payload = cached
    updated: list[Asset] = []
    for item in items:
        if item.id == asset.id:
            updated.append(item.with_accounts(tuple(accounts)))
        else:
            updated.append(item)
    save_assets(cfg, updated, stale=bool(payload.get("stale")))


def account_from_name(accounts: list[Account], name: str) -> Account:
    matched = [item for item in accounts if item.name == name or item.username == name or item.id == name]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise ConfigError(f"Account {name!r} is not in the granted list")
    raise ConfigError(f"Account {name!r} matches more than one granted account")
