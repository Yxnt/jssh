from __future__ import annotations

from pathlib import Path

from jssh.models.asset import Account, Asset, NodeRef
from jssh.services.path_resolver import copy_command, resolve_sftp_path, sftp_argv, ssh_argv
from jssh.ui.fzf import filter_assets


def test_verified_koko_path(tmp_cfg, sample_asset: Asset, sample_account: Account) -> None:
    target = resolve_sftp_path(tmp_cfg, sample_asset, sample_account)
    assert target.path == "/Default/Proxmox集群/shpbs01-node04/linux-root/"
    assert target.path.endswith("/linux-root/")
    assert "/root/" not in target.path
    argv = sftp_argv(tmp_cfg, target)
    assert argv[:3] == ["sftp", "-P", "2222"]
    assert argv[-1] == (
        "jumpserver_user@jumpserver.example.com:"
        "/Default/Proxmox集群/shpbs01-node04/linux-root/"
    )
    assert "HostKeyAlgorithms=+ssh-rsa" in argv


def test_spaces_and_unicode_stay_in_one_argv(tmp_cfg) -> None:
    asset = Asset(
        id="a",
        hostname="host 01",
        ip="10.0.0.8",
        node=NodeRef(id="n", key="1:2", value="集群 A", name="Default/集群 A"),
    )
    account = Account(id="u", name="linux root", username="root")
    target = resolve_sftp_path(tmp_cfg, asset, account)
    assert target.path == "/Default/集群 A/host 01/linux root/"
    argv = sftp_argv(tmp_cfg, target)
    assert argv[:3] == ["sftp", "-P", "2222"]
    assert argv[-1] == (
        "jumpserver_user@jumpserver.example.com:/Default/集群 A/host 01/linux root/"
    )
    quoted = copy_command(argv)
    assert quoted.startswith("'sftp'")
    assert "'jumpserver_user@jumpserver.example.com:/Default/集群 A/host 01/linux root/'" in quoted


def test_slash_in_names_is_replaced(tmp_cfg) -> None:
    asset = Asset(
        id="a",
        hostname="a/b",
        ip="10.0.0.8",
        node=NodeRef(id="n", key="1", value="foo/bar", name="foo/bar"),
    )
    account = Account(id="u", name="su/name", username="root")
    target = resolve_sftp_path(tmp_cfg, asset, account)
    assert target.path == "/foo/bar/a_b/su_name/"


def test_path_override(tmp_cfg, sample_asset: Asset, sample_account: Account) -> None:
    cfg = tmp_cfg
    object.__setattr__(
        cfg,
        "path_overrides",
        {"shpbs01-node04": "/Custom/shpbs01-node04/{account}/"},
    )
    target = resolve_sftp_path(cfg, sample_asset, sample_account)
    assert target.path == "/Custom/shpbs01-node04/linux-root/"
    assert target.source == "config override"


def test_ssh_argv_is_gateway_only(tmp_cfg, sample_asset: Asset) -> None:
    argv = ssh_argv(tmp_cfg)
    joined = " ".join(argv)
    assert argv[0:3] == ["ssh", "-p", "2222"]
    assert argv[-2:] == ["-t", "jumpserver_user@jumpserver.example.com"]
    assert "HostKeyAlgorithms=+ssh-rsa" in argv
    assert "PubkeyAcceptedAlgorithms=+ssh-rsa" in argv
    assert "root@" not in joined
    assert sample_asset.ip not in joined
    assert "@root@" not in joined


def test_identity_file_is_passed(tmp_cfg, sample_asset: Asset, sample_account: Account, tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("dummy", encoding="utf-8")
    object.__setattr__(tmp_cfg, "ssh_identity_file", key)
    ssh = ssh_argv(tmp_cfg)
    assert "-i" in ssh
    assert str(key) in ssh
    assert "IdentitiesOnly=yes" in ssh
    assert "HostKeyAlgorithms=+ssh-rsa" in ssh
    target = resolve_sftp_path(tmp_cfg, sample_asset, sample_account)
    sftp = sftp_argv(tmp_cfg, target)
    assert "-i" in sftp
    assert str(key) in sftp
    assert "HostKeyAlgorithms=+ssh-rsa" in sftp


def test_search_matches_hostname_ip_node_and_chinese(sample_asset: Asset) -> None:
    assets = [sample_asset]
    assert filter_assets(assets, "shpbs")[0].hostname == "shpbs01-node04"
    assert filter_assets(assets, "10.0.0.20")[0].hostname == "shpbs01-node04"
    assert filter_assets(assets, "Proxmox")[0].hostname == "shpbs01-node04"
    assert filter_assets(assets, "集群")[0].hostname == "shpbs01-node04"
    assert filter_assets(assets, "no-such") == []
