from __future__ import annotations

from typing import Any

from jssh.api.client import Client
from jssh.models.asset import (
    Account,
    Asset,
    NodeRef,
    Profile,
    account_from_api,
    asset_from_granted,
    node_from_granted,
    profile_from_api,
)
from jssh.errors import ApiError

PROFILE_PATH = "/api/v1/users/profile/"
MY_ASSETS_TREE = "/api/v1/perms/users/assets/"
MY_NODES = "/api/v1/perms/users/nodes/"
MY_NODES_WITH_ASSETS = "/api/v1/perms/users/nodes-with-assets/tree/"
MY_NODE_ASSETS = "/api/v1/perms/users/nodes/{node_id}/assets/"
MY_ASSET_SYSTEM_USERS = "/api/v1/perms/users/assets/{asset_id}/system-users/"


def fetch_profile(client: Client) -> Profile:
    payload = client.get_json(PROFILE_PATH)
    if not isinstance(payload, dict):
        raise ApiError("Profile response is not an object. Run jssh doctor --probe.")
    profile = profile_from_api(payload)
    if profile is None:
        raise ApiError(
            "Profile JSON is missing id or username. "
            f"Top-level keys: {_keys(payload)}"
        )
    return profile


def fetch_granted_assets(client: Client) -> list[Asset]:
    payload = client.get_json(MY_NODES_WITH_ASSETS)
    assets = parse_tree(payload)
    if assets:
        return assets
    payload = client.get_json(MY_ASSETS_TREE)
    assets = parse_tree(payload)
    if assets:
        return assets
    return _assets_from_nodes(client)


def fetch_accounts(client: Client, asset_id: str) -> list[Account]:
    path = MY_ASSET_SYSTEM_USERS.format(asset_id=asset_id)
    payload = client.get_json(path)
    rows = _as_rows(payload)
    accounts: list[Account] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account = account_from_api(row)
        if account:
            accounts.append(account)
    return accounts


def parse_tree(payload: Any) -> list[Asset]:
    rows = _as_rows(payload)
    nodes_by_key: dict[str, NodeRef] = {}
    parent_by_key: dict[str, str] = {}
    pending_assets: list[tuple[dict[str, Any], str]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("meta")
        if not isinstance(meta, dict):
            continue
        kind = meta.get("type")
        if kind == "node":
            node_raw = meta.get("node")
            if not isinstance(node_raw, dict):
                continue
            node = node_from_granted(node_raw)
            if node is None:
                continue
            nodes_by_key[node.key] = node
            pid = str(row.get("pId") or "")
            if pid:
                parent_by_key[node.key] = pid
        elif kind == "asset":
            asset_raw = meta.get("asset")
            if not isinstance(asset_raw, dict):
                continue
            pending_assets.append((asset_raw, str(row.get("pId") or "")))

    assets: list[Asset] = []
    seen: set[str] = set()
    for raw, parent_key in pending_assets:
        node = _node_path(parent_key, nodes_by_key, parent_by_key)
        asset = asset_from_granted(raw, node)
        if asset is None:
            continue
        key = asset.select_key
        if key in seen:
            continue
        seen.add(key)
        assets.append(asset)
    return assets


def parse_granted_list(payload: Any, node: NodeRef | None = None) -> list[Asset]:
    assets: list[Asset] = []
    for row in _as_rows(payload):
        if not isinstance(row, dict):
            continue
        asset = asset_from_granted(row, node)
        if asset:
            assets.append(asset)
    return assets


def _assets_from_nodes(client: Client) -> list[Asset]:
    payload = client.get_json(MY_NODES)
    nodes: list[NodeRef] = []
    for row in _as_rows(payload):
        if isinstance(row, dict):
            node = node_from_granted(row)
            if node:
                nodes.append(node)
    assets: list[Asset] = []
    seen: set[str] = set()
    for node in nodes:
        rows = client.paginate(MY_NODE_ASSETS.format(node_id=node.id))
        for asset in parse_granted_list(rows, node):
            if asset.select_key in seen:
                continue
            seen.add(asset.select_key)
            assets.append(asset)
    return assets


def _node_path(
    key: str,
    nodes: dict[str, NodeRef],
    parents: dict[str, str],
) -> NodeRef | None:
    if not key:
        return None
    chain: list[NodeRef] = []
    current = key
    guard = 0
    while current and guard < 32:
        node = nodes.get(current)
        if node is None:
            break
        chain.append(node)
        current = parents.get(current, "")
        guard += 1
    if not chain:
        return None
    chain.reverse()
    values = [item.folder for item in chain if item.folder]
    leaf = chain[-1]
    return NodeRef(
        id=leaf.id,
        key=leaf.key,
        value=leaf.value,
        name="/".join(values),
        assets_amount=leaf.assets_amount,
    )


def _as_rows(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return results
        raise ApiError(
            "Expected a list or a paginated object with results. "
            f"Top-level keys: {_keys(payload)}"
        )
    raise ApiError(f"Unexpected JSON type: {type(payload).__name__}")


def _keys(payload: dict[str, Any]) -> str:
    return ", ".join(sorted(payload)[:12]) or "(none)"
