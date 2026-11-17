from __future__ import annotations

from pathlib import Path

import pytest

from jssh.config import parse_config
from jssh.models.asset import Account, Asset, NodeRef
from jssh.secrets import AccessKey


@pytest.fixture
def tmp_cfg(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = {
        "jumpserver": {
            "api_url": "https://jumpserver.example.com",
            "ssh_host": "jumpserver.example.com",
            "ssh_port": 2222,
            "ssh_username": "jumpserver_user",
            "org_id": "DEFAULT",
        },
        "auth": {"access_key_env": "JMS_AK", "secret_key_env": "JMS_SK"},
        "search": {"cache_ttl": 600, "cache_dir": str(cache)},
        "sftp": {"default_root": "Default"},
        "ssh": {"interactive_fallback": True},
        "tls": {"verify": True},
        "http": {"timeout": 5, "retries": 1},
    }
    cfg = parse_config(raw, tmp_path / "config.yaml")
    return cfg


@pytest.fixture
def access_key() -> AccessKey:
    return AccessKey(key_id="11111111-1111-1111-1111-111111111111", secret="test-secret", source="env")


@pytest.fixture
def sample_asset() -> Asset:
    return Asset(
        id="asset-1",
        hostname="shpbs01-node04",
        ip="10.0.0.20",
        node=NodeRef(
            id="node-2",
            key="1:2",
            value="Proxmox集群",
            name="Default/Proxmox集群",
        ),
    )


@pytest.fixture
def sample_account() -> Account:
    return Account(id="acc-1", name="linux-root", username="root", protocol="ssh")
