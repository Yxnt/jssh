from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jssh.cache import save_assets, save_recent
from jssh.cli import main
from jssh.complete import account_tokens, host_tokens
from jssh.config import parse_config
from jssh.models.asset import Account, Asset, NodeRef, RecentEntry


def _asset(hostname: str, accounts: tuple[Account, ...] = ()) -> Asset:
    return Asset(
        id=f"id-{hostname}",
        hostname=hostname,
        ip="10.0.0.1",
        node=NodeRef(id="n", key="1", value="Default"),
        accounts=accounts,
    )


def _write_cfg(tmp_path: Path):
    cache = tmp_path / "cache"
    cfg_path = tmp_path / "config.yaml"
    raw = {
        "jumpserver": {
            "api_url": "https://jumpserver.example.com",
            "ssh_host": "jumpserver.example.com",
            "ssh_port": 2222,
            "ssh_username": "jumpserver_user",
        },
        "auth": {"access_key_env": "JMS_AK", "secret_key_env": "JMS_SK"},
        "search": {"cache_ttl": 600, "cache_dir": str(cache)},
        "sftp": {"default_root": "Default"},
    }
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return parse_config(raw, cfg_path), cfg_path


def test_host_tokens_put_recent_first() -> None:
    assets = [_asset("alpha"), _asset("beta")]
    recent = [
        RecentEntry(
            asset_id="id-beta",
            node_key="1",
            account_id="a",
            hostname="beta",
            account_name="linux-root",
            ts="t",
        )
    ]
    assert host_tokens(assets, recent) == ["beta", "alpha"]


def test_account_tokens_for_one_host() -> None:
    root = Account(id="1", name="linux-root", username="root")
    other = Account(id="2", name="linux-app", username="app")
    assets = [_asset("alpha", (root,)), _asset("beta", (other,))]
    assert account_tokens(assets, query="alpha") == ["linux-root"]
    assert account_tokens(assets, query="beta") == ["linux-app"]


def test_complete_host_prints_cache_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, cfg_path = _write_cfg(tmp_path)
    save_assets(cfg, [_asset("shpbs01-node04")])
    assert main(["--config", str(cfg_path), "complete", "host"]) == 0
    assert capsys.readouterr().out.splitlines() == ["shpbs01-node04"]


def test_complete_account_uses_recent_without_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, cfg_path = _write_cfg(tmp_path)
    asset = _asset("shpbs01-node04")
    save_assets(cfg, [asset])
    save_recent(
        cfg,
        RecentEntry(
            asset_id=asset.id,
            node_key="1",
            account_id="acc-1",
            hostname=asset.hostname,
            account_name="linux-root",
            ts="t",
            action="ssh",
        ),
    )
    assert main(["--config", str(cfg_path), "complete", "account", asset.hostname]) == 0
    assert capsys.readouterr().out.splitlines() == ["linux-root"]


def test_complete_missing_config_is_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.yaml"
    assert main(["--config", str(missing), "complete", "host"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""
