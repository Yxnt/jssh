from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    username: str
    protocol: str = ""
    login_mode: str = ""
    priority: int | None = None
    actions: int | None = None
    sftp_root: str = ""
    username_same_with_user: bool = False

    @property
    def sftp_folder(self) -> str:
        return self.name


@dataclass(frozen=True)
class NodeRef:
    id: str
    key: str
    value: str
    name: str = ""
    assets_amount: int | None = None

    @property
    def folder(self) -> str:
        return self.value.replace("/", "_") if self.value else ""


@dataclass(frozen=True)
class Asset:
    id: str
    hostname: str
    ip: str
    org_id: str = ""
    org_name: str = ""
    comment: str = ""
    platform: str = ""
    is_active: bool = True
    node: NodeRef | None = None
    accounts: tuple[Account, ...] = ()

    @property
    def node_label(self) -> str:
        if self.node is None:
            return ""
        if self.node.name:
            return self.node.name.strip("/")
        return self.node.value

    @property
    def select_key(self) -> str:
        node_key = self.node.key if self.node else ""
        return f"{self.id}\t{node_key}"

    def with_accounts(self, accounts: tuple[Account, ...]) -> Asset:
        return Asset(
            id=self.id,
            hostname=self.hostname,
            ip=self.ip,
            org_id=self.org_id,
            org_name=self.org_name,
            comment=self.comment,
            platform=self.platform,
            is_active=self.is_active,
            node=self.node,
            accounts=accounts,
        )


@dataclass(frozen=True)
class Profile:
    id: str
    username: str
    name: str = ""


@dataclass
class RecentEntry:
    asset_id: str
    node_key: str
    account_id: str
    hostname: str
    account_name: str
    ts: str
    action: str = "sftp"


def account_from_api(raw: dict[str, Any]) -> Account | None:
    ident = raw.get("id")
    name = raw.get("name")
    if ident is None or not name:
        return None
    return Account(
        id=str(ident),
        name=str(name),
        username=str(raw.get("username") or ""),
        protocol=str(raw.get("protocol") or ""),
        login_mode=str(raw.get("login_mode") or ""),
        priority=_int_or_none(raw.get("priority")),
        actions=_int_or_none(raw.get("actions")),
        sftp_root=str(raw.get("sftp_root") or ""),
        username_same_with_user=bool(raw.get("username_same_with_user")),
    )


def asset_from_granted(raw: dict[str, Any], node: NodeRef | None = None) -> Asset | None:
    ident = raw.get("id")
    hostname = raw.get("hostname")
    if ident is None or not hostname:
        return None
    return Asset(
        id=str(ident),
        hostname=str(hostname),
        ip=str(raw.get("ip") or ""),
        org_id=str(raw.get("org_id") or ""),
        org_name=str(raw.get("org_name") or ""),
        comment=str(raw.get("comment") or ""),
        platform=str(raw.get("platform") or ""),
        is_active=bool(raw.get("is_active", True)),
        node=node,
    )


def node_from_granted(raw: dict[str, Any]) -> NodeRef | None:
    ident = raw.get("id")
    key = raw.get("key")
    value = raw.get("value")
    if ident is None or key is None or value is None:
        return None
    return NodeRef(
        id=str(ident),
        key=str(key),
        value=str(value),
        name=str(raw.get("name") or ""),
        assets_amount=_int_or_none(raw.get("assets_amount")),
    )


def profile_from_api(raw: dict[str, Any]) -> Profile | None:
    ident = raw.get("id")
    username = raw.get("username")
    if ident is None or not username:
        return None
    return Profile(id=str(ident), username=str(username), name=str(raw.get("name") or ""))


def asset_to_dict(asset: Asset) -> dict[str, Any]:
    payload = asdict(asset)
    return payload


def asset_from_cache(raw: dict[str, Any]) -> Asset | None:
    node_raw = raw.get("node")
    node = node_from_granted(node_raw) if isinstance(node_raw, dict) else None
    accounts: list[Account] = []
    for item in raw.get("accounts") or ():
        if isinstance(item, dict):
            account = account_from_api(item)
            if account:
                accounts.append(account)
    ident = raw.get("id")
    hostname = raw.get("hostname")
    if ident is None or not hostname:
        return None
    return Asset(
        id=str(ident),
        hostname=str(hostname),
        ip=str(raw.get("ip") or ""),
        org_id=str(raw.get("org_id") or ""),
        org_name=str(raw.get("org_name") or ""),
        comment=str(raw.get("comment") or ""),
        platform=str(raw.get("platform") or ""),
        is_active=bool(raw.get("is_active", True)),
        node=node,
        accounts=tuple(accounts),
    )


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
