from __future__ import annotations

from jssh.api.assets import parse_granted_list, parse_tree
from jssh.models.asset import account_from_api, asset_from_granted


def test_tree_builds_node_path_from_parent_keys() -> None:
    payload = [
        {
            "id": "1",
            "name": "Default (3)",
            "pId": "",
            "meta": {"type": "node", "node": {"id": "n1", "key": "1", "value": "Default"}},
        },
        {
            "id": "1:2",
            "name": "Proxmox集群 (2)",
            "pId": "1",
            "meta": {
                "type": "node",
                "node": {"id": "n2", "key": "1:2", "value": "Proxmox集群"},
            },
        },
        {
            "id": "asset-1",
            "name": "shpbs01-node04",
            "title": "10.0.0.20",
            "pId": "1:2",
            "meta": {
                "type": "asset",
                "asset": {
                    "id": "asset-1",
                    "hostname": "shpbs01-node04",
                    "ip": "10.0.0.20",
                    "protocols": ["ssh/22"],
                    "platform": "Linux",
                },
            },
        },
    ]
    assets = parse_tree(payload)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.hostname == "shpbs01-node04"
    assert asset.ip == "10.0.0.20"
    assert asset.node is not None
    assert asset.node.name == "Default/Proxmox集群"
    assert asset.node.value == "Proxmox集群"


def test_same_asset_under_two_nodes_is_kept_twice() -> None:
    payload = [
        {
            "id": "1",
            "pId": "",
            "meta": {"type": "node", "node": {"id": "n1", "key": "1", "value": "Default"}},
        },
        {
            "id": "1:2",
            "pId": "1",
            "meta": {"type": "node", "node": {"id": "n2", "key": "1:2", "value": "Web"}},
        },
        {
            "id": "1:3",
            "pId": "1",
            "meta": {"type": "node", "node": {"id": "n3", "key": "1:3", "value": "DB"}},
        },
        {
            "id": "asset-1",
            "pId": "1:2",
            "meta": {"type": "asset", "asset": {"id": "asset-1", "hostname": "shared", "ip": "10.0.0.9"}},
        },
        {
            "id": "asset-1",
            "pId": "1:3",
            "meta": {"type": "asset", "asset": {"id": "asset-1", "hostname": "shared", "ip": "10.0.0.9"}},
        },
    ]
    assets = parse_tree(payload)
    assert len(assets) == 2
    labels = sorted(item.node_label for item in assets)
    assert labels == ["Default/DB", "Default/Web"]


def test_asset_without_hostname_is_dropped() -> None:
    assert asset_from_granted({"id": "x", "ip": "10.0.0.1"}) is None
    assert parse_granted_list([{"id": "x", "ip": "10.0.0.1"}]) == []


def test_account_uses_name_not_username_for_folder() -> None:
    account = account_from_api(
        {"id": "acc-1", "name": "linux-root", "username": "root", "protocol": "ssh"}
    )
    assert account is not None
    assert account.sftp_folder == "linux-root"
    assert account.username == "root"


def test_account_without_name_is_dropped() -> None:
    assert account_from_api({"id": "acc-1", "username": "root"}) is None
