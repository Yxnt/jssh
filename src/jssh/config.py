from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from jssh.errors import ConfigError

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "jssh"
DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "jssh"
CONFIG_NAME = "config.yaml"
DEFAULT_SSH_OPTIONS = (
    "HostKeyAlgorithms=+ssh-rsa",
    "PubkeyAcceptedAlgorithms=+ssh-rsa",
)


@dataclass(frozen=True)
class Config:
    api_url: str
    ssh_host: str
    ssh_port: int
    ssh_username: str
    ssh_identity_file: Path | None
    org_id: str | None
    access_key_env: str
    secret_key_env: str
    access_key_value: str | None
    secret_key_value: str | None
    totp_secret_value: str | None
    keychain_service: str | None
    onepassword_ak: str | None
    onepassword_sk: str | None
    totp_env: str
    onepassword_totp: str | None
    totp_digits: int
    totp_period: int
    totp_algorithm: str
    totp_auto: bool
    totp_prompts: tuple[str, ...]
    ssh_options: tuple[str, ...]
    cache_ttl: int
    cache_dir: Path
    config_path: Path
    sftp_default_root: str
    path_overrides: dict[str, str]
    account_overrides: dict[str, list[str]]
    interactive_fallback: bool
    otty_shell_integration: str
    otty_shell_install: bool
    otty_sftp_layout: str
    otty_sftp_direction: str
    otty_sftp_size: int
    otty_sftp_no_focus: bool
    tls_verify: bool | str
    timeout: float
    retries: int

    @property
    def api_base(self) -> str:
        return self.api_url.rstrip("/")


def config_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("JSSH_CONFIG")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_DIR / CONFIG_NAME


def cache_dir(cfg: Config | None = None) -> Path:
    if cfg:
        return cfg.cache_dir
    env = os.environ.get("JSSH_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CACHE_DIR


def load_config(path: str | Path | None = None) -> Config:
    resolved = config_path(path)
    if not resolved.is_file():
        raise ConfigError(
            f"Config not found: {resolved}. Copy config.example.yaml to that path."
        )
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {resolved}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping: {resolved}")
    cfg = parse_config(raw, resolved)
    _check_secret_file_mode(cfg)
    return cfg


def parse_config(raw: dict[str, Any], resolved: Path) -> Config:
    jumpserver = _mapping(raw, "jumpserver")
    auth = _mapping(raw, "auth")
    search = _mapping(raw, "search")
    sftp = _mapping(raw, "sftp")
    ssh = _mapping(raw, "ssh")
    terminal = _mapping(raw, "terminal")
    tls = _mapping(raw, "tls")
    http = _mapping(raw, "http")

    api_url = str(jumpserver.get("api_url") or "").strip()
    ssh_host = str(jumpserver.get("ssh_host") or "").strip()
    ssh_username = str(jumpserver.get("ssh_username") or "").strip()
    if not api_url or not ssh_host or not ssh_username:
        raise ConfigError("jumpserver.api_url, ssh_host, and ssh_username are required")

    org_id = jumpserver.get("org_id")
    if org_id is not None:
        org_id = str(org_id).strip() or None

    verify: bool | str = True
    if "verify" in tls:
        verify = tls["verify"]
        if isinstance(verify, str):
            verify = str(Path(verify).expanduser())
        elif not isinstance(verify, bool):
            raise ConfigError("tls.verify must be a boolean or a CA bundle path")
    ca_bundle = tls.get("ca_bundle")
    if ca_bundle:
        verify = str(Path(str(ca_bundle)).expanduser())

    cache = Path(
        os.environ.get("JSSH_CACHE_DIR")
        or search.get("cache_dir")
        or DEFAULT_CACHE_DIR
    ).expanduser()

    path_overrides = sftp.get("path_overrides") or {}
    if not isinstance(path_overrides, dict):
        raise ConfigError("sftp.path_overrides must be a mapping")
    account_overrides = sftp.get("account_overrides") or {}
    if not isinstance(account_overrides, dict):
        raise ConfigError("sftp.account_overrides must be a mapping")
    accounts: dict[str, list[str]] = {}
    for key, value in account_overrides.items():
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError("sftp.account_overrides values must be lists of strings")
        accounts[str(key)] = [str(item) for item in value]

    identity_raw = jumpserver.get("ssh_identity_file") or ssh.get("identity_file")
    identity: Path | None = None
    if identity_raw:
        identity = Path(str(identity_raw)).expanduser()

    algorithm = str(auth.get("totp_algorithm") or "sha1").lower()
    if algorithm not in {"sha1", "sha256", "sha512"}:
        raise ConfigError("auth.totp_algorithm must be sha1, sha256, or sha512")
    prompts = ssh.get("totp_prompts") or ["[MFA auth]:"]
    if not isinstance(prompts, list) or not all(isinstance(item, str) for item in prompts):
        raise ConfigError("ssh.totp_prompts must be a list of strings")
    if "options" in ssh:
        raw_opts = ssh.get("options") or []
        if not isinstance(raw_opts, list) or not all(isinstance(item, str) for item in raw_opts):
            raise ConfigError("ssh.options must be a list of strings")
        ssh_options = tuple(str(item) for item in raw_opts)
    else:
        ssh_options = DEFAULT_SSH_OPTIONS
    osc_mode = _otty_shell_mode(terminal.get("otty_shell_integration", "auto"))
    osc_install = bool(terminal.get("otty_shell_install", False))
    layout = _otty_sftp_layout(terminal.get("otty_sftp_layout", "split"))
    direction = _otty_sftp_direction(terminal.get("otty_sftp_direction", "right"))
    size = _otty_sftp_size(terminal.get("otty_sftp_size", 40))
    no_focus = bool(terminal.get("otty_sftp_no_focus", True))
    return Config(
        api_url=api_url,
        ssh_host=ssh_host,
        ssh_port=int(jumpserver.get("ssh_port") or 2222),
        ssh_username=ssh_username,
        ssh_identity_file=identity,
        org_id=org_id,
        access_key_env=str(auth.get("access_key_env") or "JMS_AK"),
        secret_key_env=str(auth.get("secret_key_env") or "JMS_SK"),
        access_key_value=_optional_secret(auth, "access_key"),
        secret_key_value=_optional_secret(auth, "secret_key"),
        totp_secret_value=_optional_secret(auth, "totp_secret"),
        keychain_service=(str(auth["keychain_service"]) if auth.get("keychain_service") else None),
        onepassword_ak=(str(auth["onepassword_ak"]) if auth.get("onepassword_ak") else None),
        onepassword_sk=(str(auth["onepassword_sk"]) if auth.get("onepassword_sk") else None),
        totp_env=str(auth.get("totp_env") or "JMS_TOTP_SECRET"),
        onepassword_totp=(str(auth["onepassword_totp"]) if auth.get("onepassword_totp") else None),
        totp_digits=int(auth.get("totp_digits") or 6),
        totp_period=int(auth.get("totp_period") or 30),
        totp_algorithm=algorithm,
        totp_auto=bool(ssh.get("totp_auto", True)),
        totp_prompts=tuple(str(item) for item in prompts),
        ssh_options=ssh_options,
        cache_ttl=int(search.get("cache_ttl") or 600),
        cache_dir=cache,
        config_path=resolved,
        sftp_default_root=str(sftp.get("default_root") or "Default"),
        path_overrides={str(k): str(v) for k, v in path_overrides.items()},
        account_overrides=accounts,
        interactive_fallback=bool(ssh.get("interactive_fallback", True)),
        otty_shell_integration=osc_mode,
        otty_shell_install=osc_install,
        otty_sftp_layout=layout,
        otty_sftp_direction=direction,
        otty_sftp_size=size,
        otty_sftp_no_focus=no_focus,
        tls_verify=verify,
        timeout=float(http.get("timeout") or 15),
        retries=int(http.get("retries") or 3),
    )


def public_config(cfg: Config) -> dict[str, Any]:
    return {
        "config_path": str(cfg.config_path),
        "cache_dir": str(cfg.cache_dir),
        "jumpserver": {
            "api_url": cfg.api_url,
            "ssh_host": cfg.ssh_host,
            "ssh_port": cfg.ssh_port,
            "ssh_username": cfg.ssh_username,
            "ssh_identity_file": str(cfg.ssh_identity_file) if cfg.ssh_identity_file else None,
            "org_id": cfg.org_id,
        },
        "auth": {
            "access_key_env": cfg.access_key_env,
            "secret_key_env": cfg.secret_key_env,
            "totp_env": cfg.totp_env,
            "access_key_in_config": bool(cfg.access_key_value),
            "secret_key_in_config": bool(cfg.secret_key_value),
            "totp_secret_in_config": bool(cfg.totp_secret_value),
            "keychain_service": cfg.keychain_service,
            "onepassword_ak": bool(cfg.onepassword_ak),
            "onepassword_sk": bool(cfg.onepassword_sk),
            "onepassword_totp": bool(cfg.onepassword_totp),
            "totp_digits": cfg.totp_digits,
            "totp_period": cfg.totp_period,
            "totp_algorithm": cfg.totp_algorithm,
        },
        "search": {"cache_ttl": cfg.cache_ttl},
        "sftp": {
            "default_root": cfg.sftp_default_root,
            "path_overrides": sorted(cfg.path_overrides),
        },
        "ssh": {
            "interactive_fallback": cfg.interactive_fallback,
            "totp_auto": cfg.totp_auto,
            "totp_prompts": list(cfg.totp_prompts),
            "options": list(cfg.ssh_options),
        },
        "terminal": {
            "otty_shell_integration": cfg.otty_shell_integration,
            "otty_shell_install": cfg.otty_shell_install,
            "otty_sftp_layout": cfg.otty_sftp_layout,
            "otty_sftp_direction": cfg.otty_sftp_direction,
            "otty_sftp_size": cfg.otty_sftp_size,
            "otty_sftp_no_focus": cfg.otty_sftp_no_focus,
        },
        "tls": {"verify": cfg.tls_verify if isinstance(cfg.tls_verify, bool) else "ca_bundle"},
    }


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _otty_shell_mode(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip().lower() or "auto"
    if text in {"true", "1", "yes", "on"}:
        return "on"
    if text in {"false", "0", "no", "off"}:
        return "off"
    if text == "auto":
        return "auto"
    raise ConfigError("terminal.otty_shell_integration must be auto, true, or false")


def _otty_sftp_layout(value: object) -> str:
    text = str(value).strip().lower() or "split"
    if text in {"split", "tab"}:
        return text
    raise ConfigError("terminal.otty_sftp_layout must be split or tab")


def _otty_sftp_direction(value: object) -> str:
    text = str(value).strip().lower() or "right"
    if text in {"right", "left", "up", "down"}:
        return text
    raise ConfigError("terminal.otty_sftp_direction must be right, left, up, or down")


def _otty_sftp_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("terminal.otty_sftp_size must be an integer 10-90") from exc
    if size < 10 or size > 90:
        raise ConfigError("terminal.otty_sftp_size must be an integer 10-90")
    return size


def _optional_secret(auth: dict[str, Any], key: str) -> str | None:
    value = auth.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _check_secret_file_mode(cfg: Config) -> None:
    if not (cfg.access_key_value or cfg.secret_key_value or cfg.totp_secret_value):
        return
    path = cfg.config_path
    if not path.is_file():
        return
    if path.stat().st_mode & 0o077:
        raise ConfigError(
            f"{path} contains secrets and is group/world readable. Run: chmod 600 {path}"
        )
