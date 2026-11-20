from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jssh.cache import save_assets
from jssh.cli import main
from jssh.config import parse_config
from jssh.models.asset import Asset, NodeRef


def test_help_lists_commands() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_list_reads_cache(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
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
    cfg = parse_config(raw, cfg_path)
    save_assets(
        cfg,
        [
            Asset(
                id="asset-1",
                hostname="shpbs01-node04",
                ip="10.0.0.20",
                node=NodeRef(id="n", key="1:2", value="Proxmox集群", name="Default/Proxmox集群"),
            )
        ],
    )
    monkeypatch.setenv("JSSH_CONFIG", str(cfg_path))
    assert main(["--config", str(cfg_path), "list"]) == 0
    out = capsys.readouterr().out
    assert "shpbs01-node04" in out
    assert "10.0.0.20" in out
    assert "Proxmox集群" in out


def test_config_omits_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    raw = {
        "jumpserver": {
            "api_url": "https://jumpserver.example.com",
            "ssh_host": "jumpserver.example.com",
            "ssh_port": 2222,
            "ssh_username": "jumpserver_user",
        },
        "auth": {"access_key_env": "JMS_AK", "secret_key_env": "JMS_SK"},
    }
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("JMS_AK", "super-secret-id")
    monkeypatch.setenv("JMS_SK", "super-secret-key")
    assert main(["--config", str(cfg_path), "config"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "super-secret" not in out
    assert payload["auth"]["access_key_env"] == "JMS_AK"
    assert payload["jumpserver"]["ssh_port"] == 2222


def test_inline_secrets_not_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    raw = {
        "jumpserver": {
            "api_url": "https://jumpserver.example.com",
            "ssh_host": "jumpserver.example.com",
            "ssh_port": 2222,
            "ssh_username": "jumpserver_user",
        },
        "auth": {
            "access_key": "ak-in-file-value",
            "secret_key": "sk-in-file-value",
            "totp_secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        },
    }
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg_path.chmod(0o600)
    monkeypatch.delenv("JMS_AK", raising=False)
    monkeypatch.delenv("JMS_SK", raising=False)
    monkeypatch.delenv("JMS_TOTP_SECRET", raising=False)
    assert main(["--config", str(cfg_path), "config"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "ak-in-file-value" not in out
    assert "sk-in-file-value" not in out
    assert "GEZDGNBVGY3TQOJQ" not in out
    assert payload["auth"]["access_key_in_config"] is True
    assert payload["auth"]["totp_secret_in_config"] is True


def test_world_readable_inline_secrets_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "jumpserver": {
                    "api_url": "https://jumpserver.example.com",
                    "ssh_host": "jumpserver.example.com",
                    "ssh_port": 2222,
                    "ssh_username": "jumpserver_user",
                },
                "auth": {"totp_secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"},
            }
        ),
        encoding="utf-8",
    )
    cfg_path.chmod(0o644)
    assert main(["--config", str(cfg_path), "config"]) == 1
    err = capsys.readouterr().err
    assert "chmod 600" in err

def test_doctor_without_config_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JSSH_CONFIG", raising=False)
    missing = tmp_path / "nope.yaml"
    assert main(["--config", str(missing), "doctor"]) == 1


def test_doctor_reports_otty(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "otty")
    monkeypatch.setenv("OTTY_PANE_ID", "p_test")
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    missing = tmp_path / "nope.yaml"
    assert main(["--config", str(missing), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "term_program: otty" in out
    assert "otty_cli: /app/otty-cli" in out
    assert "otty_pane: p_test" in out
    assert "otty_sftp_split: on" in out


def test_sftp_last_without_recent_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "jumpserver": {
                    "api_url": "https://jumpserver.example.com",
                    "ssh_host": "jumpserver.example.com",
                    "ssh_port": 2222,
                    "ssh_username": "jumpserver_user",
                },
                "auth": {"access_key_env": "JMS_AK", "secret_key_env": "JMS_SK"},
                "search": {"cache_dir": str(tmp_path / "cache")},
            }
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(cfg_path), "sftp", "--last"]) == 1
    err = capsys.readouterr().err
    assert "No recent host" in err
    assert "sftp --last" in err
