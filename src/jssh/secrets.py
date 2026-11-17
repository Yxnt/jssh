from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jssh.config import Config
from jssh.errors import AuthError, ConfigError


@dataclass(frozen=True)
class AccessKey:
    key_id: str
    secret: str
    source: str


@dataclass(frozen=True)
class TotpSecret:
    secret: str
    source: str


def load_access_key(cfg: Config) -> AccessKey:
    key_id = _first(
        _env(cfg.access_key_env),
        cfg.access_key_value,
        _onepassword(cfg.onepassword_ak) if cfg.onepassword_ak else None,
        _keychain(cfg.keychain_service, "access_key") if cfg.keychain_service else None,
    )
    secret = _first(
        _env(cfg.secret_key_env),
        cfg.secret_key_value,
        _onepassword(cfg.onepassword_sk) if cfg.onepassword_sk else None,
        _keychain(cfg.keychain_service, "secret_key") if cfg.keychain_service else None,
    )
    if not key_id or not secret:
        raise AuthError(
            "Access key is missing. Set auth.access_key and auth.secret_key in config.yaml "
            f"(chmod 600), or {cfg.access_key_env}/{cfg.secret_key_env}, or Keychain."
        )
    if _env(cfg.access_key_env) and _env(cfg.secret_key_env):
        source = "env"
    elif cfg.access_key_value and cfg.secret_key_value:
        source = "config"
    elif cfg.onepassword_ak or cfg.onepassword_sk:
        source = "onepassword"
    else:
        source = "keychain"
    return AccessKey(key_id=key_id, secret=secret, source=source)


def totp_file_path(cfg: Config) -> Path:
    return cfg.config_path.parent / "totp"


def load_totp_secret(cfg: Config) -> TotpSecret | None:
    env_val = _env(cfg.totp_env)
    cfg_val = cfg.totp_secret_value
    file_val = _read_totp_file(cfg)
    op_val = _onepassword(cfg.onepassword_totp) if cfg.onepassword_totp else None
    key_val = _keychain(cfg.keychain_service, "totp_secret") if cfg.keychain_service else None
    value = _first(env_val, cfg_val, file_val, op_val, key_val)
    if not value:
        return None
    if env_val:
        source = "env"
    elif cfg_val:
        source = "config"
    elif file_val:
        source = "file"
    elif op_val:
        source = "onepassword"
    else:
        source = "keychain"
    return TotpSecret(secret=value, source=source)


def store_totp_file(cfg: Config, secret: str) -> Path:
    path = totp_file_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret.strip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _read_totp_file(cfg: Config) -> str | None:
    path = totp_file_path(cfg)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def store_keychain(service: str, account: str, value: str) -> None:
    if not service or not account:
        raise ConfigError("Keychain service and account names are required")
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            value,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def credential_presence(cfg: Config) -> dict[str, bool]:
    return {
        cfg.access_key_env: bool(_env(cfg.access_key_env)),
        cfg.secret_key_env: bool(_env(cfg.secret_key_env)),
        cfg.totp_env: bool(_env(cfg.totp_env)),
        "keychain": bool(cfg.keychain_service),
        "onepassword": bool(cfg.onepassword_ak or cfg.onepassword_sk or cfg.onepassword_totp),
    }


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _first(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _keychain(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _onepassword(ref: str) -> str | None:
    try:
        result = subprocess.run(
            ["op", "read", ref],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise AuthError("1Password CLI (op) is not available") from exc
    if result.returncode != 0:
        raise AuthError("1Password CLI could not read the configured secret reference")
    value = result.stdout.strip()
    return value or None
